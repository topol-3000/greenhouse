"""The automation flow against real PostgreSQL, from a sample to an applied fan.

The ingestion path has no endpoint on purpose, so these tests call it the way
every in-process producer does: on a session of their own, which stands in for
the one the request dependency or a simulation step would have opened.

The decision itself is covered in ``unit/test_hysteresis_policy.py``. What is
asserted here is everything the policy cannot decide alone — that a command is
written once, that both result samples land with it or not at all, and that a
measurement which changed no current state starts nothing.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.control.actuator import ActuationRequest
from ai_greenhouse.control.automation import IngestionResult, TelemetryIngestionService
from ai_greenhouse.points.models import DataQuality
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord, TelemetryWriteOutcome
from tests.integration.factories import (
    AutomationGrowbox,
    count_rows,
    create_automation_growbox,
    create_control_loop,
)

OBSERVED_AT: datetime = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
"""Measurement time of the first sample of every test."""

EXECUTED_AT: datetime = datetime(2026, 7, 30, 12, 0, 5, tzinfo=UTC)
"""First instant the injected clock answers, kept apart from every ``observed_at``."""


class AdvancingClock:
    """A clock that moves one second per reading, without one passing.

    A frozen clock would be the one thing a real one never is, and the result
    samples of two commands would then share an ``observed_at`` — leaving the
    second command's state unable to replace the first one's. Stating the time
    is what makes the test deterministic; standing still is not part of it.
    """

    def __init__(self, start: datetime) -> None:
        """Start the clock.

        Args:
            start: The instant the first reading returns.
        """
        self._next: datetime = start

    def __call__(self) -> datetime:
        """Return the current instant and step one second forward.

        Returns:
            The instant of this reading.
        """
        now: datetime = self._next
        self._next = now + timedelta(seconds=1)
        return now


class FailingActuator:
    """An adapter that refuses every command, for the rollback tests."""

    def __init__(self) -> None:
        """Start with no recorded attempt."""
        self.attempts: int = 0

    async def apply(self, request: ActuationRequest) -> None:
        """Record the attempt and fail before anything is written.

        Args:
            request: The command that will not be applied.

        Raises:
            RuntimeError: Always.
        """
        self.attempts += 1
        raise RuntimeError("actuator is unavailable")


def temperature(
    growbox: AutomationGrowbox,
    value: float,
    **overrides: Any,
) -> TelemetrySampleRecord:
    """Build one temperature measurement for a wired growbox.

    Args:
        growbox: The growbox whose measurement point is being read.
        value: The measured temperature in ``°C``.
        **overrides: Fields replacing the defaults.

    Returns:
        The record, ready to be offered to the ingestion path.
    """
    return TelemetrySampleRecord(
        **{
            "id": uuid4(),
            "point_id": UUID(growbox.points["air_temperature"]["id"]),
            "value": value,
            "observed_at": OBSERVED_AT,
            "received_at": OBSERVED_AT,
            "quality": DataQuality.SIMULATED,
        }
        | overrides
    )


def ingestion(session: AsyncSession, **overrides: Any) -> TelemetryIngestionService:
    """Build the ingestion path with a stated clock instead of a real one.

    Args:
        session: The session standing in for a request or simulation step.
        **overrides: Keyword arguments replacing the defaults.

    Returns:
        The service under test.
    """
    return TelemetryIngestionService(session, **{"clock": AdvancingClock(EXECUTED_AT)} | overrides)


async def state_value(connection: AsyncConnection, point_id: str) -> object:
    """Read one point's current value out of the test transaction.

    Args:
        connection: The connection the test transaction runs on.
        point_id: The point whose projection to read.

    Returns:
        The stored value, or ``None`` when the point has never been written.
    """
    return await connection.scalar(
        text("SELECT value FROM point_current_states WHERE point_id = :point_id"),
        {"point_id": UUID(point_id)},
    )


@pytest.fixture
async def wired(http_client: httpx.AsyncClient) -> AutomationGrowbox:
    """Build a growbox whose climate zone already has its control loop.

    Args:
        http_client: The client the topology is created through.

    Returns:
        The growbox, ready to receive temperature measurements.
    """
    growbox = await create_automation_growbox(http_client)
    await create_control_loop(http_client, growbox)
    return growbox


async def test_a_temperature_above_the_band_switches_the_fan_on(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """One accepted measurement produces one command and two matching result samples."""
    result: IngestionResult = await ingestion(session).ingest(temperature(wired, 27.0))
    await session.commit()

    assert result.outcome is TelemetryWriteOutcome.RECORDED_CURRENT
    assert result.command is not None
    assert result.command.desired_value is True
    assert result.command.executed_at == EXECUTED_AT
    assert await state_value(connection, wired.points["fan_power"]["id"]) is True
    assert await state_value(connection, wired.points["fan_running"]["id"]) is True
    assert await count_rows(connection, "commands") == 1


async def test_the_documented_scenario_switches_on_holds_and_switches_off(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """``27 → 25 → 23`` is exactly one ON, no command, and one OFF."""
    service = ingestion(session)
    outcomes: list[bool | None] = []
    for offset, value in enumerate((27.0, 25.0, 23.0)):
        result = await service.ingest(
            temperature(
                wired,
                value,
                observed_at=OBSERVED_AT + timedelta(minutes=offset),
                received_at=OBSERVED_AT + timedelta(minutes=offset),
            )
        )
        outcomes.append(None if result.command is None else result.command.desired_value)
    await session.commit()

    assert outcomes == [True, None, False]
    assert await count_rows(connection, "commands") == 2
    assert await state_value(connection, wired.points["fan_power"]["id"]) is False
    assert await state_value(connection, wired.points["fan_running"]["id"]) is False


async def test_replaying_one_trigger_sample_acts_only_once(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """A producer that re-delivers a measurement must not switch the fan twice."""
    service = ingestion(session)
    record = temperature(wired, 27.0)
    first = await service.ingest(record)
    second = await service.ingest(record)
    await session.commit()

    assert first.command is not None
    assert second.outcome is TelemetryWriteOutcome.DUPLICATE
    assert second.command is None
    assert await count_rows(connection, "commands") == 1
    assert await count_rows(connection, "telemetry_samples") == 3


async def test_a_late_temperature_is_stored_but_decides_nothing(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """A buffered reading that did not replace the current state is not new information."""
    service = ingestion(session)
    await service.ingest(temperature(wired, 23.0, observed_at=OBSERVED_AT, received_at=OBSERVED_AT))
    late = await service.ingest(
        temperature(
            wired,
            27.0,
            observed_at=OBSERVED_AT - timedelta(minutes=1),
            received_at=OBSERVED_AT,
        )
    )
    await session.commit()

    assert late.outcome is TelemetryWriteOutcome.OUT_OF_ORDER
    assert late.command is None
    assert await count_rows(connection, "commands") == 0
    assert await state_value(connection, wired.points["fan_power"]["id"]) is None


async def test_an_actuator_failure_leaves_the_temperature_and_nothing_else(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """The measurement is history; the command and its results are one fact or none."""
    actuator = FailingActuator()

    result = await ingestion(session, actuator=actuator).ingest(temperature(wired, 27.0))
    await session.commit()

    assert actuator.attempts == 1
    assert result.outcome is TelemetryWriteOutcome.RECORDED_CURRENT
    assert result.automation_failed is True
    assert result.command is None
    assert await count_rows(connection, "commands") == 0
    assert await count_rows(connection, "telemetry_samples") == 1
    assert await state_value(connection, wired.points["fan_power"]["id"]) is None


async def test_the_database_refuses_a_second_command_for_one_decision(
    wired: AutomationGrowbox,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """Two transactions can both decide; the unique key is what stops both acting.

    The service checks for an existing key before applying, but that check and
    the insert are not one statement. What actually bounds a race is the
    constraint, so it is asserted against PostgreSQL directly.
    """
    result = await ingestion(session).ingest(temperature(wired, 27.0))
    await session.commit()
    assert result.command is not None

    with pytest.raises(IntegrityError):
        await connection.execute(
            text(
                "INSERT INTO commands (id, idempotency_key, control_loop_id, trigger_sample_id, "
                "target_point_id, desired_value, result_control_sample_id, "
                "result_status_sample_id, executed_at, created_at) "
                "SELECT :id, idempotency_key, control_loop_id, trigger_sample_id, "
                "target_point_id, desired_value, result_control_sample_id, "
                "result_status_sample_id, executed_at, created_at FROM commands WHERE id = :source"
            ),
            {"id": uuid4(), "source": result.command.id},
        )


async def test_a_measured_point_that_drives_no_loop_starts_nothing(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """Automation is opt-in: a zone without a loop keeps behaving as it did before."""
    growbox = await create_automation_growbox(http_client)

    result = await ingestion(session).ingest(temperature(growbox, 27.0))
    await session.commit()

    assert result.outcome is TelemetryWriteOutcome.RECORDED_CURRENT
    assert result.command is None
    assert await count_rows(connection, "commands") == 0
    assert await state_value(connection, growbox.points["fan_power"]["id"]) is None
