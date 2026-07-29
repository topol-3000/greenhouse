"""HTTP coverage for the bounded, count-free telemetry history read."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.points.models import DataQuality
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord
from ai_greenhouse.telemetry.service import TelemetryService
from tests.integration.factories import POINTS_URL, create_growbox, create_point

BASE_TIME: datetime = datetime(2026, 7, 29, 10, 5, tzinfo=UTC)


def history_url(point_id: str) -> str:
    """Return the history endpoint for one point."""
    return f"{POINTS_URL}/{point_id}/telemetry"


def sample(
    point_id: str,
    *,
    sample_id: UUID | None = None,
    value: float = 22.4,
    observed_at: datetime = BASE_TIME,
) -> TelemetrySampleRecord:
    """Build a simulated temperature sample."""
    return TelemetrySampleRecord(
        id=sample_id or uuid4(),
        point_id=UUID(point_id),
        value=value,
        observed_at=observed_at,
        received_at=observed_at + timedelta(milliseconds=140),
        quality=DataQuality.SIMULATED,
    )


@pytest.fixture
def service(session: AsyncSession) -> TelemetryService:
    """Build the service used by the in-process telemetry producer."""
    return TelemetryService(session)


@pytest.fixture
async def point(http_client: httpx.AsyncClient) -> dict[str, Any]:
    """Create the temperature point whose history is queried."""
    growbox = await create_growbox(http_client)
    return await create_point(http_client, growbox.site["id"])


async def test_history_is_newest_first_with_a_stable_id_tiebreaker(
    service: TelemetryService,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    lower_id = UUID("00000000-0000-0000-0000-000000000001")
    higher_id = UUID("00000000-0000-0000-0000-000000000002")
    newest_id = UUID("00000000-0000-0000-0000-000000000003")
    await service.record_sample(sample(point["id"], sample_id=lower_id, value=20.0))
    await service.record_sample(sample(point["id"], sample_id=higher_id, value=21.0))
    await service.record_sample(
        sample(
            point["id"],
            sample_id=newest_id,
            value=22.0,
            observed_at=BASE_TIME + timedelta(minutes=1),
        )
    )
    await session.commit()

    first = await http_client.get(history_url(point["id"]))
    second = await http_client.get(history_url(point["id"]))
    items: list[dict[str, Any]] = first.json()["items"]

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert [item["id"] for item in items] == [
        str(newest_id),
        str(higher_id),
        str(lower_id),
    ]
    assert second.json()["items"] == items
    assert items[0] == {
        "id": str(newest_id),
        "point_id": point["id"],
        "value": 22.0,
        "unit": "°C",
        "observed_at": "2026-07-29T10:06:00Z",
        "received_at": "2026-07-29T10:06:00.140000Z",
        "quality": "simulated",
    }


async def test_empty_history_and_unknown_point_have_distinct_results(
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    empty = await http_client.get(history_url(point["id"]))
    missing = await http_client.get(history_url(str(uuid4())))

    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": []}
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "point_not_found"


async def test_default_and_explicit_limit_boundaries(
    service: TelemetryService,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    for index in range(101):
        await service.record_sample(
            sample(
                point["id"],
                sample_id=UUID(int=index + 1),
                observed_at=BASE_TIME + timedelta(seconds=index),
            )
        )
    await session.commit()

    default = await http_client.get(history_url(point["id"]))
    minimum = await http_client.get(history_url(point["id"]), params={"limit": 1})
    maximum = await http_client.get(history_url(point["id"]), params={"limit": 1000})

    assert default.status_code == 200, default.text
    assert len(default.json()["items"]) == 100
    assert minimum.status_code == 200, minimum.text
    assert len(minimum.json()["items"]) == 1
    assert maximum.status_code == 200, maximum.text
    assert len(maximum.json()["items"]) == 101

    for rejected in (0, 1001):
        response = await http_client.get(history_url(point["id"]), params={"limit": rejected})
        assert response.status_code == 422, response.text


async def test_time_window_is_inclusive_and_rejects_an_inverted_range(
    service: TelemetryService,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    instants = [BASE_TIME + timedelta(minutes=index) for index in range(3)]
    for index, instant in enumerate(instants):
        await service.record_sample(
            sample(point["id"], sample_id=UUID(int=index + 1), observed_at=instant)
        )
    await session.commit()

    from_only = await http_client.get(
        history_url(point["id"]),
        params={"from": instants[1].isoformat()},
    )
    to_only = await http_client.get(
        history_url(point["id"]),
        params={"to": instants[1].isoformat()},
    )
    both = await http_client.get(
        history_url(point["id"]),
        params={"from": instants[0].isoformat(), "to": instants[2].isoformat()},
    )
    inverted = await http_client.get(
        history_url(point["id"]),
        params={"from": instants[2].isoformat(), "to": instants[0].isoformat()},
    )

    assert [item["observed_at"] for item in from_only.json()["items"]] == [
        "2026-07-29T10:07:00Z",
        "2026-07-29T10:06:00Z",
    ]
    assert [item["observed_at"] for item in to_only.json()["items"]] == [
        "2026-07-29T10:06:00Z",
        "2026-07-29T10:05:00Z",
    ]
    assert len(both.json()["items"]) == 3
    assert inverted.status_code == 422, inverted.text
    assert inverted.json()["error"]["code"] == "invalid_telemetry_window"


async def test_history_envelope_contains_no_pagination_metadata(
    service: TelemetryService,
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    point: dict[str, Any],
) -> None:
    await service.record_sample(sample(point["id"]))
    await session.commit()

    response = await http_client.get(history_url(point["id"]))

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"items"}
