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
from ai_greenhouse.cultivation.repository import RuntimeTargetRepository
from ai_greenhouse.points.models import DataQuality
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord, TelemetryWriteOutcome
from tests.integration.factories import (
    GROW_CYCLES_URL,
    AutomationGrowbox,
    CycleEnvironment,
    activate_grow_cycle,
    count_rows,
    create_automation_growbox,
    create_control_loop,
    create_cycle_environment,
    create_grow_cycle,
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


@pytest.fixture
async def recipe_driven(
    http_client: httpx.AsyncClient,
) -> tuple[CycleEnvironment, dict[str, Any]]:
    """Activate a 22–26 °C target over deliberately different 20–30 °C legacy bounds."""
    environment = await create_cycle_environment(
        http_client,
        lower_threshold=20,
        upper_threshold=30,
    )
    cycle = await create_grow_cycle(http_client, environment)
    activated = await activate_grow_cycle(http_client, cycle["id"])
    return environment, activated["active_runtime_target"]


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
    assert result.command.runtime_target_id is None
    assert result.command.executed_at == EXECUTED_AT
    assert await state_value(connection, wired.points["fan_power"]["id"]) is True
    assert await state_value(connection, wired.points["fan_running"]["id"]) is True
    assert await count_rows(connection, "commands") == 1


async def test_active_target_precedence_drives_the_complete_recipe_sequence(
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
    http_client: httpx.AsyncClient,
) -> None:
    """Different legacy bounds prove ``27 → 24 → 21`` used the 22–26 target."""
    environment, target = recipe_driven
    service = ingestion(session)
    decisions: list[bool | None] = []
    provenance: list[UUID | None] = []

    for offset, value in enumerate((27.0, 24.0, 21.0)):
        result = await service.ingest(
            temperature(
                environment.growbox,
                value,
                observed_at=OBSERVED_AT + timedelta(minutes=offset),
                received_at=OBSERVED_AT + timedelta(minutes=offset),
            )
        )
        decisions.append(None if result.command is None else result.command.desired_value)
        provenance.append(None if result.command is None else result.command.runtime_target_id)
    await session.commit()

    target_id = UUID(target["id"])
    commands = (
        await http_client.get(
            "/api/v1/commands",
            params={"control_loop_id": environment.control_loop["id"]},
        )
    ).json()["items"]

    assert decisions == [True, None, False]
    assert provenance == [target_id, None, target_id]
    assert [command["desired_value"] for command in commands] == [False, True]
    assert {command["runtime_target_id"] for command in commands} == {target["id"]}
    assert await count_rows(connection, "commands") == 2


@pytest.mark.parametrize(
    ("value", "prime_fan_on"),
    [
        pytest.param(22.0, True, id="lower bound keeps an on fan on"),
        pytest.param(26.0, False, id="upper bound keeps an off fan off"),
    ],
)
async def test_exact_runtime_target_boundaries_create_no_command(
    value: float,
    prime_fan_on: bool,
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """Both Decimal boundaries belong to the no-op band."""
    environment, _ = recipe_driven
    service = ingestion(session)
    baseline = 0
    if prime_fan_on:
        priming = await service.ingest(temperature(environment.growbox, 27.0))
        assert priming.command is not None
        baseline = 1

    result = await service.ingest(
        temperature(
            environment.growbox,
            value,
            observed_at=OBSERVED_AT + timedelta(minutes=1),
            received_at=OBSERVED_AT + timedelta(minutes=1),
        )
    )
    await session.commit()

    assert result.command is None
    assert await count_rows(connection, "commands") == baseline


async def test_closing_a_target_restores_legacy_bounds_without_rewriting_history(
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
    http_client: httpx.AsyncClient,
) -> None:
    """A closed 22–26 target is ignored; later decisions use 20–30 with null provenance."""
    environment, target = recipe_driven
    service = ingestion(session)
    target_decision = await service.ingest(temperature(environment.growbox, 27.0))
    await session.commit()
    assert target_decision.command is not None
    target_command_id = target_decision.command.id

    cycles = await http_client.get(GROW_CYCLES_URL, params={"code": "basil-demo-cycle"})
    cycle_id = cycles.json()["items"][0]["id"]
    completed = await http_client.post(f"{GROW_CYCLES_URL}/{cycle_id}/complete")
    assert completed.status_code == 200, completed.text

    inside_legacy = await service.ingest(
        temperature(
            environment.growbox,
            21.0,
            observed_at=OBSERVED_AT + timedelta(minutes=1),
            received_at=OBSERVED_AT + timedelta(minutes=1),
        )
    )
    fallback = await service.ingest(
        temperature(
            environment.growbox,
            19.0,
            observed_at=OBSERVED_AT + timedelta(minutes=2),
            received_at=OBSERVED_AT + timedelta(minutes=2),
        )
    )
    await session.commit()

    historical = (await http_client.get(f"/api/v1/commands/{target_command_id}")).json()
    closed_target = (await http_client.get(f"/api/v1/runtime-targets/{target['id']}")).json()

    assert inside_legacy.command is None
    assert fallback.command is not None
    assert fallback.command.desired_value is False
    assert fallback.command.runtime_target_id is None
    assert historical["runtime_target_id"] == target["id"]
    assert closed_target["effective_to"] is not None
    assert await count_rows(connection, "commands") == 2


async def test_target_driven_duplicate_and_late_samples_do_not_decide_again(
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """The existing trigger identity and current-state gate remain authoritative."""
    environment, target = recipe_driven
    service = ingestion(session)
    trigger = temperature(environment.growbox, 27.0)

    first = await service.ingest(trigger)
    duplicate = await service.ingest(trigger)
    late = await service.ingest(
        temperature(
            environment.growbox,
            19.0,
            observed_at=OBSERVED_AT - timedelta(minutes=1),
        )
    )
    await session.commit()

    assert first.command is not None
    assert first.command.runtime_target_id == UUID(target["id"])
    assert duplicate.outcome is TelemetryWriteOutcome.DUPLICATE
    assert duplicate.command is None
    assert late.outcome is TelemetryWriteOutcome.OUT_OF_ORDER
    assert late.command is None
    assert await count_rows(connection, "commands") == 1


async def test_command_provenance_restricts_deleting_its_runtime_target(
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """PostgreSQL preserves a target once command history points to it."""
    environment, target = recipe_driven
    result = await ingestion(session).ingest(temperature(environment.growbox, 27.0))
    await session.commit()
    assert result.command is not None

    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(
                text("DELETE FROM runtime_targets WHERE id = :target_id"),
                {"target_id": UUID(target["id"])},
            )


async def test_invalid_active_target_fails_without_legacy_fallback(
    recipe_driven: tuple[CycleEnvironment, dict[str, Any]],
    session: AsyncSession,
    connection: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accepted temperature remains, but corrupt active bounds produce no command."""
    environment, _ = recipe_driven
    original = RuntimeTargetRepository.get_active_for_evaluation

    async def corrupt_target(
        repository: RuntimeTargetRepository,
        control_loop_id: UUID,
    ) -> object:
        target = await original(repository, control_loop_id)
        assert target is not None
        target.unit = "%"
        return target

    monkeypatch.setattr(RuntimeTargetRepository, "get_active_for_evaluation", corrupt_target)

    result = await ingestion(session).ingest(temperature(environment.growbox, 27.0))
    await session.commit()

    assert result.outcome is TelemetryWriteOutcome.RECORDED_CURRENT
    assert result.automation_failed is True
    assert result.command is None
    assert await count_rows(connection, "telemetry_samples") == 1
    assert await count_rows(connection, "commands") == 0


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
