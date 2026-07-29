"""End-to-end coverage of the telemetry write boundary against real PostgreSQL.

Milestone 2 exposes no ingestion endpoint, so these tests call
``TelemetryService.record_sample`` the way the simulation runtime will: on a
session of their own, which they commit or roll back exactly as the request
dependency would. Everything they assert afterwards is read back from the
database, because the two properties this boundary exists for are properties of
what ends up stored.

Those two properties are the whole subject of the file. A re-delivered sample
must change nothing, and a late sample must land in history without rolling the
current value back. Both are decisions taken in the service rather than
constraints enforced by the schema, so both are asserted by *what did not
happen*: no second row, no bumped ``revision``, no replaced value.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.points.models import DEFAULT_REVISION, DataQuality
from ai_greenhouse.telemetry.exceptions import ArchivedPointError, TelemetryValueTypeError
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord
from ai_greenhouse.telemetry.service import TelemetryService
from tests.integration.factories import (
    POINTS_URL,
    archive,
    count_rows,
    create_growbox,
    create_point,
)

OBSERVED_AT: datetime = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
"""Virtual measurement time of the sample every test starts from."""

RECEIVED_AT: datetime = datetime(2026, 7, 29, 12, 0, 30, tzinfo=UTC)
"""Intake time of that same sample, half a minute later on purpose.

The two instants differ in every test so that a step which quietly defaulted one
from the other would have to fail rather than coincide.
"""

STATE_RESPONSE_FIELDS: set[str] = {
    "point_id",
    "value",
    "observed_at",
    "received_at",
    "quality",
    "revision",
    "updated_at",
}
"""The Milestone 1 shape of ``GET /points/{id}/state``, which M2 must not change."""


def sample(point_id: str, **overrides: Any) -> TelemetrySampleRecord:
    """Build a valid record for one point.

    Args:
        point_id: The point being measured.
        **overrides: Fields replacing the defaults.

    Returns:
        The record, ready to be offered to the service.
    """
    return TelemetrySampleRecord(
        **{
            "id": uuid4(),
            "point_id": UUID(point_id),
            "value": 21.5,
            "observed_at": OBSERVED_AT,
            "received_at": RECEIVED_AT,
            "quality": DataQuality.SIMULATED,
        }
        | overrides
    )


async def stored_samples(connection: AsyncConnection, point_id: str) -> list[dict[str, Any]]:
    """Read one point's samples out of the test transaction, oldest first.

    Args:
        connection: The connection the test transaction runs on.
        point_id: The point whose history to read.

    Returns:
        Every stored sample of that point, as plain dictionaries.
    """
    result = await connection.execute(
        text("SELECT * FROM telemetry_samples WHERE point_id = :point_id ORDER BY observed_at"),
        {"point_id": UUID(point_id)},
    )
    return [dict(row) for row in result.mappings()]


async def stored_state(connection: AsyncConnection, point_id: str) -> dict[str, Any]:
    """Read one point's state projection out of the test transaction.

    Args:
        connection: The connection the test transaction runs on.
        point_id: The point whose projection to read.

    Returns:
        The projection row as a plain dictionary.
    """
    result = await connection.execute(
        text("SELECT * FROM point_current_states WHERE point_id = :point_id"),
        {"point_id": UUID(point_id)},
    )
    return dict(result.mappings().one())


@pytest.fixture
def service(session: AsyncSession) -> TelemetryService:
    """Build the service on the session the test owns.

    Args:
        session: The session joined to the test transaction.

    Returns:
        The telemetry service under test.
    """
    return TelemetryService(session)


@pytest.fixture
async def point(http_client: httpx.AsyncClient) -> dict[str, Any]:
    """Create the float point with a unit that most tests measure.

    Args:
        http_client: The client used to build the topology.

    Returns:
        The created point's representation.
    """
    growbox = await create_growbox(http_client)
    return await create_point(http_client, growbox.site["id"])


async def test_a_recorded_sample_is_stored_and_becomes_the_current_state(
    service: TelemetryService,
    session: AsyncSession,
    connection: AsyncConnection,
    point: dict[str, Any],
) -> None:
    before: dict[str, Any] = await stored_state(connection, point["id"])

    await service.record_sample(sample(point["id"], value=21.5, unit="K"))
    await session.commit()

    history: list[dict[str, Any]] = await stored_samples(connection, point["id"])
    after: dict[str, Any] = await stored_state(connection, point["id"])

    assert len(history) == 1
    assert history[0]["value"] == 21.5
    assert history[0]["observed_at"] == OBSERVED_AT
    assert history[0]["received_at"] == RECEIVED_AT
    assert history[0]["quality"] == DataQuality.SIMULATED
    assert history[0]["unit"] == point["unit"], "the point's unit outranks the producer's"
    assert after["value"] == 21.5
    assert after["observed_at"] == OBSERVED_AT
    assert after["received_at"] == RECEIVED_AT
    assert after["quality"] == DataQuality.SIMULATED
    assert after["revision"] == before["revision"] + 1


async def test_the_state_endpoint_returns_the_recorded_value(
    service: TelemetryService,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    await service.record_sample(sample(point["id"], value=21.5))
    await session.commit()

    response = await http_client.get(f"{POINTS_URL}/{point['id']}/state")
    body: dict[str, Any] = response.json()

    assert response.status_code == 200, response.text
    assert set(body) == STATE_RESPONSE_FIELDS, "Milestone 1 clients must keep working"
    assert body["value"] == 21.5
    assert body["quality"] == DataQuality.SIMULATED
    assert body["revision"] == DEFAULT_REVISION + 1


async def test_re_recording_the_same_sample_id_changes_nothing(
    service: TelemetryService,
    session: AsyncSession,
    connection: AsyncConnection,
    point: dict[str, Any],
) -> None:
    delivered: TelemetrySampleRecord = sample(point["id"])
    await service.record_sample(delivered)
    await session.commit()
    before: dict[str, Any] = await stored_state(connection, point["id"])

    await service.record_sample(delivered)
    await session.commit()
    after: dict[str, Any] = await stored_state(connection, point["id"])

    assert await count_rows(connection, "telemetry_samples") == 1
    assert after["revision"] == before["revision"]
    assert after["updated_at"] == before["updated_at"], "the projection was not touched at all"


@pytest.mark.parametrize(
    "lateness",
    [timedelta(minutes=5), timedelta(0)],
    ids=["older", "same_instant"],
)
async def test_a_sample_that_is_not_newer_stays_in_history_only(
    service: TelemetryService,
    session: AsyncSession,
    connection: AsyncConnection,
    point: dict[str, Any],
    lateness: timedelta,
) -> None:
    await service.record_sample(sample(point["id"], value=21.5))
    await session.commit()
    before: dict[str, Any] = await stored_state(connection, point["id"])

    await service.record_sample(sample(point["id"], value=18.0, observed_at=OBSERVED_AT - lateness))
    await session.commit()
    after: dict[str, Any] = await stored_state(connection, point["id"])

    assert await count_rows(connection, "telemetry_samples") == 2, "history keeps the late sample"
    assert after["value"] == 21.5
    assert after["observed_at"] == OBSERVED_AT
    assert after["revision"] == before["revision"]


async def test_a_boolean_is_not_accepted_as_an_integer(
    service: TelemetryService,
    connection: AsyncConnection,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    counter: dict[str, Any] = await create_point(
        http_client,
        point["site_id"],
        code="fan-cycles",
        metric_type="fan_cycles",
        data_type="integer",
        unit=None,
    )

    with pytest.raises(TelemetryValueTypeError):
        await service.record_sample(sample(counter["id"], value=True))

    assert await count_rows(connection, "telemetry_samples") == 0


async def test_a_sample_for_an_unknown_point_is_refused(
    service: TelemetryService,
    connection: AsyncConnection,
) -> None:
    with pytest.raises(ReferencedPointNotFoundError):
        await service.record_sample(sample(str(uuid4())))

    assert await count_rows(connection, "telemetry_samples") == 0


async def test_a_sample_for_an_archived_point_is_refused(
    service: TelemetryService,
    connection: AsyncConnection,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    await archive(http_client, f"{POINTS_URL}/{point['id']}")

    with pytest.raises(ArchivedPointError):
        await service.record_sample(sample(point["id"]))

    assert await count_rows(connection, "telemetry_samples") == 0


async def test_a_failed_step_leaves_no_partial_write(
    service: TelemetryService,
    session: AsyncSession,
    connection: AsyncConnection,
    point: dict[str, Any],
) -> None:
    """One simulation step records two points; a failure on the second undoes the first."""
    await service.record_sample(sample(point["id"], value=21.5))

    with pytest.raises(TelemetryValueTypeError):
        await service.record_sample(sample(point["id"], value="warm"))
    await session.rollback()

    state: dict[str, Any] = await stored_state(connection, point["id"])
    assert await count_rows(connection, "telemetry_samples") == 0
    assert state["value"] is None
    assert state["revision"] == DEFAULT_REVISION
