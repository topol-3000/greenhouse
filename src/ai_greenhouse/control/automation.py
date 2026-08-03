"""The automation flow: accepted telemetry in, an applied command out.

This module is where the milestone's one product claim lives — that the flow is
the same whichever producer supplied the measurement. It therefore knows nothing
about gateways, drivers or HTTP: it is handed a sample that the telemetry
boundary has already accepted as the point's current state, and everything it
does from there depends on that sample and the loop's own immutable thresholds.

There is one source of the band, and it is the ``ControlLoop`` the sample's
point drives. No other persisted configuration can override it, so a command
never has to record which of several sources decided it.

Transactions
------------
:class:`TelemetryIngestionService` is the seam every producer reaches the system
through. It records the sample first and applies the command afterwards, inside
a savepoint, because the two have different failure rules:

* a measurement that was accepted is history, and stays recorded whatever
  happens next;
* a command and its two result samples are one fact, and a failure anywhere in
  applying them must leave none of the three behind.

The savepoint is what makes both true at once. The surrounding transaction — the
request session — still owns the commit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.control.actuator import ActuationRequest, Actuator, LoopbackActuator
from ai_greenhouse.control.models import Command, CommandSource, CommandState, ControlLoop
from ai_greenhouse.control.policy import FanAction, evaluate_hysteresis
from ai_greenhouse.control.repository import CommandRepository, ControlLoopRepository
from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.repository import GatewayRepository
from ai_greenhouse.infrastructure.database.base import utc_now
from ai_greenhouse.points.models import PointCurrentState
from ai_greenhouse.points.repository import PointCurrentStateRepository
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord, TelemetryWriteOutcome
from ai_greenhouse.telemetry.service import TelemetryService

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

Clock = Callable[[], datetime]

POLICY_KEY_PREFIX: str = "hysteresis-v1"
"""First segment of the idempotency key, naming the policy that decided.

Part of the key rather than implied by the loop: a second policy evaluating the
same loop and the same trigger would otherwise derive a key that collides with a
decision it did not make.
"""


def idempotency_key(control_loop_id: UUID, trigger_sample_id: UUID, action: FanAction) -> str:
    """Build the stable key identifying one decision.

    Args:
        control_loop_id: The loop that decided.
        trigger_sample_id: The sample it decided on.
        action: What it decided.

    Returns:
        The key, in the documented ``policy:loop:sample:action`` form.
    """
    return f"{POLICY_KEY_PREFIX}:{control_loop_id}:{trigger_sample_id}:{action.value}"


def command_id_for(control_loop_id: UUID, trigger_sample_id: UUID, action: FanAction) -> UUID:
    """Derive the identifier one decision always gets.

    Derived rather than random so that a re-processed trigger rebuilds the same
    command and, through it, the same two result-sample identifiers. The insert
    then collides with the row it would have duplicated, instead of writing a
    second set of samples that look like a second actuation.

    Args:
        control_loop_id: The loop that decided, used as the UUID namespace.
        trigger_sample_id: The sample it decided on.
        action: What it decided.

    Returns:
        The command's identifier.
    """
    return uuid5(control_loop_id, f"{trigger_sample_id}:{action.value}")


def result_sample_id(command_id: UUID, point_id: UUID) -> UUID:
    """Derive the identifier of one result sample of one command.

    Args:
        command_id: The command being applied, used as the UUID namespace.
        point_id: The point the result is recorded on.

    Returns:
        The sample's identifier.
    """
    return uuid5(command_id, str(point_id))


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """What one offered measurement led to.

    Attributes:
        outcome: What the telemetry boundary did with the sample.
        command: The command applied because of it, when there was one.
        automation_failed: Whether evaluation or application was rolled back.
            The sample is stored either way; the flag is how a caller learns
            that the fan did not follow it.
    """

    outcome: TelemetryWriteOutcome
    command: Command | None = None
    automation_failed: bool = False


class AutomationService:
    """Turns one accepted current temperature into at most one applied command."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Clock = utc_now,
        actuator: Actuator | None = None,
    ) -> None:
        """Build the service around one session.

        Args:
            session: The transaction the command and its result samples share
                with the measurement that triggered them.
            clock: Source of the execution instant. Injected so a test can state
                the time instead of waiting for it. It has to move strictly
                forward between commands: the execution instant is the
                ``observed_at`` of both result samples, and the telemetry
                boundary replaces a current state only with a strictly newer
                measurement, so a clock that stood still would leave the fan
                reading the state of the command before it.
            actuator: The adapter applying the command. Defaults to the M3
                loopback; a test substitutes a failing one, and a later
                milestone substitutes a real device.
        """
        self._loops: ControlLoopRepository = ControlLoopRepository(session)
        self._commands: CommandRepository = CommandRepository(session)
        self._states: PointCurrentStateRepository = PointCurrentStateRepository(session)
        self._gateways: GatewayRepository = GatewayRepository(session)
        self._clock: Clock = clock
        self._actuator: Actuator = actuator or LoopbackActuator(TelemetryService(session))

    async def apply_for_sample(self, record: TelemetrySampleRecord) -> Command | None:
        """Evaluate one accepted current sample and apply what it calls for.

        The caller has already established that the sample is new *and* became
        the point's current state. A replay or a late arrival describes a state
        the system has reacted to, and re-deciding on either would act twice on
        one measurement.

        Args:
            record: The measurement that became the current state.

        Returns:
            The applied command, or ``None`` when the point drives no loop, the
            temperature calls for no change, or the decision had already been
            applied.

        Raises:
            IntegrityError: If a concurrent transaction applied the same
                decision first. The caller rolls the savepoint back and treats
                the decision as already made.
        """
        loop: ControlLoop | None = await self._loops.get_by_measurement_point(record.point_id)
        if loop is None:
            return None

        action: FanAction | None = evaluate_hysteresis(
            temperature=Decimal(str(record.value)),
            lower_threshold=loop.lower_threshold,
            upper_threshold=loop.upper_threshold,
            fan_is_on=await self._fan_is_on(loop.control_point_id),
        )
        if action is None:
            return None

        key: str = idempotency_key(loop.id, record.id, action)
        if await self._commands.exists_with_key(key):
            return None

        command_id: UUID = command_id_for(loop.id, record.id, action)
        issued_at: datetime = self._clock()
        desired_value = action is FanAction.TURN_ON
        gateway: Gateway | None = await self._gateways.gateway_for_command_points(
            loop.control_point_id,
            loop.status_point_id,
        )

        executed_at: datetime | None = None
        control_sample_id: UUID | None = None
        status_sample_id: UUID | None = None
        state = CommandState.PENDING
        if gateway is None:
            executed_at = issued_at
            control_sample_id = result_sample_id(command_id, loop.control_point_id)
            status_sample_id = result_sample_id(command_id, loop.status_point_id)
            request = ActuationRequest(
                command_id=command_id,
                control_point_id=loop.control_point_id,
                status_point_id=loop.status_point_id,
                desired_value=desired_value,
                executed_at=executed_at,
                control_sample_id=control_sample_id,
                status_sample_id=status_sample_id,
            )
            await self._actuator.apply(request)
            state = CommandState.APPLIED

        command = Command(
            id=command_id,
            source=CommandSource.CONTROL_LOOP,
            idempotency_key=key,
            control_zone_id=loop.control_zone_id,
            control_loop_id=loop.id,
            trigger_sample_id=record.id,
            target_point_id=loop.control_point_id,
            reported_point_id=loop.status_point_id,
            gateway_id=None if gateway is None else gateway.id,
            desired_value=desired_value,
            state=state,
            result_control_sample_id=control_sample_id,
            result_status_sample_id=status_sample_id,
            issued_at=issued_at,
            executed_at=executed_at,
            acknowledged_at=executed_at,
        )
        self._commands.add(command)
        await self._commands.flush()
        return command

    async def _fan_is_on(self, control_point_id: UUID) -> bool:
        """Read the effective state of the control point, for the decision only.

        The row is locked, which is what serialises two evaluations of the same
        loop: the second one waits and then reads the state the first one left.

        A point that has never been written is off. That is a rule about
        *deciding*, not about the point: nothing writes ``false`` to it to make
        the assumption true, so its state stays ``no_data`` until a command
        actually sets it.

        Args:
            control_point_id: The ``fan_power`` point of the loop.

        Returns:
            ``True`` only when the projection holds a boolean ``true``.
        """
        state: PointCurrentState | None = await self._states.get_for_update(control_point_id)
        return state is not None and state.value is True


class TelemetryIngestionService:
    """The one path a measurement is offered on.

    Callers use this instead of the telemetry service directly, which is what
    makes "the same flow whichever source supplied the value" a property of the
    code rather than a claim about it. What it adds to the write boundary is the
    savepoint described in the module docstring, and nothing else: it decides no
    domain rule of its own.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Clock = utc_now,
        actuator: Actuator | None = None,
    ) -> None:
        """Build the ingestion path around one session.

        Args:
            session: The transaction the sample and any resulting command share.
            clock: Source of the command's execution instant.
            actuator: The adapter applying commands, defaulting to the loopback.
        """
        self._session: AsyncSession = session
        self._telemetry: TelemetryService = TelemetryService(session)
        self._automation: AutomationService = AutomationService(
            session,
            clock=clock,
            actuator=actuator,
        )

    async def ingest(self, record: TelemetrySampleRecord) -> IngestionResult:
        """Record one measurement and run automation when it is new information.

        A failure inside automation is rolled back to the savepoint and logged
        rather than raised. The measurement was accepted before the savepoint
        was taken, and dropping a valid measurement because a fan could not be
        switched would lose the very reading that explains why.

        Args:
            record: The measurement offered by the producer.

        Returns:
            What the boundary did with the sample and what followed from it.

        Raises:
            DomainError: If the telemetry boundary refuses the sample. A
                rejected measurement is the producer's error and is reported,
                not swallowed.
        """
        outcome: TelemetryWriteOutcome = await self._telemetry.record_sample(record)
        if outcome is not TelemetryWriteOutcome.RECORDED_CURRENT:
            return IngestionResult(outcome)

        try:
            async with self._session.begin_nested():
                command: Command | None = await self._automation.apply_for_sample(record)
        except IntegrityError:
            logger.info(
                "automation_command_already_applied",
                point_id=str(record.point_id),
                trigger_sample_id=str(record.id),
            )
            return IngestionResult(outcome)
        except Exception as error:
            logger.exception(
                "automation_application_failed",
                point_id=str(record.point_id),
                trigger_sample_id=str(record.id),
                exception_type=type(error).__name__,
            )
            return IngestionResult(outcome, automation_failed=True)
        return IngestionResult(outcome, command=command)
