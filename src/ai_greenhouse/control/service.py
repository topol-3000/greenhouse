"""Control-loop business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.control.exceptions` and, for a missing referenced zone or
point, the ones owned by the topology and points modules, which
``ai_greenhouse.api.errors`` turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or
rollback belongs to the ``get_session`` request dependency.

What a loop may be wired to is decided here and nowhere else. The rules read
points and assignments; they never decide what a point is or which role a zone
may hold it in — those stay in :mod:`ai_greenhouse.points.service` and
:mod:`ai_greenhouse.topology.service`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.control.exceptions import (
    CommandNotFoundError,
    ControlLoopExistsError,
    ControlLoopNotFoundError,
    IdempotencyKeyConflictError,
    InvalidControlLoopPointError,
    InvalidControlLoopZoneError,
    InvalidManualCommandTargetError,
)
from ai_greenhouse.control.models import (
    Command,
    CommandSource,
    CommandState,
    ControlLoop,
    ControlPolicyType,
)
from ai_greenhouse.control.repository import CommandRepository, ControlLoopRepository
from ai_greenhouse.control.schemas import ControlLoopCreate, ManualCommandCreate
from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.repository import GatewayRepository
from ai_greenhouse.infrastructure.database.base import StatusEnum, utc_now
from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.points.models import Point, PointDataType, PointKind
from ai_greenhouse.points.repository import PointRepository
from ai_greenhouse.topology.exceptions import ControlZoneNotFoundError
from ai_greenhouse.topology.models import ControlZone, Facility, ZonePointRole, ZoneType

Clock = Callable[[], datetime]

MEASUREMENT_UNIT: str = "°C"
"""Unit the hysteresis thresholds are expressed in.

The policy compares two configured numbers against a measured one. Nothing in
the flow converts units, so a point measuring in ``°F`` would be compared
against Celsius thresholds and act on the answer. Requiring the unit is what
keeps that from being possible to configure.
"""

NUMERIC_DATA_TYPES: frozenset[PointDataType] = frozenset(
    {PointDataType.FLOAT, PointDataType.INTEGER}
)


@dataclass(frozen=True, slots=True)
class ControlLoopRole:
    """One of the three points a ``hysteresis-v1`` loop binds, and what it must be.

    Held as one table rather than as three blocks of conditionals: every rule
    below is the same rule asked of a different point, and a table is what
    keeps the answers from drifting apart.

    Attributes:
        field: Request field naming the point. A refusal reports it, so a
            client is told which of the three roles was rejected.
        metric_type: Metric the point has to carry.
        point_kind: Kind the point has to be.
        data_types: Data types the point may declare.
        role: Part the point has to play in the zone.
        unit: Unit the point has to be measured in, or ``None`` when the data
            type forbids one.
    """

    field: str
    metric_type: str
    point_kind: PointKind
    data_types: frozenset[PointDataType]
    role: ZonePointRole
    unit: str | None


LOOP_ROLES: tuple[ControlLoopRole, ...] = (
    ControlLoopRole(
        field="measurement_point_id",
        metric_type="air_temperature",
        point_kind=PointKind.MEASUREMENT,
        data_types=NUMERIC_DATA_TYPES,
        role=ZonePointRole.PRIMARY_MEASUREMENT,
        unit=MEASUREMENT_UNIT,
    ),
    ControlLoopRole(
        field="control_point_id",
        metric_type="fan_power",
        point_kind=PointKind.CONTROL,
        data_types=frozenset({PointDataType.BOOLEAN}),
        role=ZonePointRole.CONTROL_OUTPUT,
        unit=None,
    ),
    ControlLoopRole(
        field="status_point_id",
        metric_type="fan_running",
        point_kind=PointKind.STATUS,
        data_types=frozenset({PointDataType.BOOLEAN}),
        role=ZonePointRole.STATUS_FEEDBACK,
        unit=None,
    ),
)
"""The complete wiring contract of ``hysteresis-v1``, in request-field order."""


class ControlLoopService:
    """Configure and read the immutable automation rule of one climate zone."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        Args:
            session: The session of the request being served.
        """
        self._loops: ControlLoopRepository = ControlLoopRepository(session)
        self._points: PointRepository = PointRepository(session)

    async def create_loop(self, payload: ControlLoopCreate) -> ControlLoop:
        """Create the one loop of a climate zone.

        The zone is judged first, then each point on its own, and only then the
        zone's existing loop. Checking the points before the duplicate keeps a
        misconfigured request from being reported as a duplicate it is not.

        Args:
            payload: The loop to configure. Its threshold ordering has already
                been validated by the schema.

        Returns:
            The created loop, flushed and carrying its identifier.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            InvalidControlLoopZoneError: If the zone is archived or is not a
                climate zone.
            ReferencedPointNotFoundError: If one of the three points does not
                exist.
            InvalidControlLoopPointError: If a point is archived, is not part
                of the zone in the required role, or does not match the kind,
                data type, metric or unit its role requires.
            ControlLoopExistsError: If the zone already has a loop.
        """
        zone = await self._loops.get_zone(payload.control_zone_id)
        if zone is None:
            raise ControlZoneNotFoundError(payload.control_zone_id)
        if zone.status is not StatusEnum.ACTIVE:
            raise InvalidControlLoopZoneError(payload.control_zone_id, "zone_not_active")
        if zone.zone_type is not ZoneType.CLIMATE:
            raise InvalidControlLoopZoneError(payload.control_zone_id, "zone_not_climate")

        submitted: dict[str, UUID] = {
            role.field: getattr(payload, role.field) for role in LOOP_ROLES
        }
        for point_id in submitted.values():
            if await self._points.get_by_id(point_id) is None:
                raise ReferencedPointNotFoundError(point_id)

        assigned: dict[tuple[UUID, ZonePointRole], Point] = {
            (assignment.point_id, assignment.role): point
            for assignment, point in await self._loops.list_zone_points(payload.control_zone_id)
        }
        for role in LOOP_ROLES:
            self._check_point(role, submitted[role.field], assigned)

        if await self._loops.has_loop_for_zone(payload.control_zone_id):
            raise ControlLoopExistsError(payload.control_zone_id)

        loop = ControlLoop(
            control_zone_id=payload.control_zone_id,
            measurement_point_id=payload.measurement_point_id,
            control_point_id=payload.control_point_id,
            status_point_id=payload.status_point_id,
            policy_type=ControlPolicyType.HYSTERESIS_V1,
            lower_threshold=payload.lower_threshold,
            upper_threshold=payload.upper_threshold,
        )
        self._loops.add(loop)
        try:
            await self._loops.flush()
        except IntegrityError as error:
            raise ControlLoopExistsError(payload.control_zone_id) from error
        return loop

    async def get_loop(self, control_loop_id: UUID) -> ControlLoop:
        """Return one loop or report it missing.

        Args:
            control_loop_id: The loop requested by the path.

        Returns:
            The stored loop.

        Raises:
            ControlLoopNotFoundError: If the path names no loop.
        """
        loop = await self._loops.get_by_id(control_loop_id)
        if loop is None:
            raise ControlLoopNotFoundError(control_loop_id)
        return loop

    async def list_loops(
        self,
        params: PageParams,
        *,
        control_zone_id: UUID | None,
    ) -> tuple[list[ControlLoop], int]:
        """Return one page of loops, optionally restricted to one zone.

        Args:
            params: The resolved ``limit``/``offset`` window.
            control_zone_id: Restricts the result to one zone when given.

        Returns:
            A tuple of the page's loops and the total matching the filter.
        """
        return await self._loops.list_page(params, control_zone_id=control_zone_id)

    @staticmethod
    def _check_point(
        role: ControlLoopRole,
        point_id: UUID,
        assigned: dict[tuple[UUID, ZonePointRole], Point],
    ) -> None:
        """Check one submitted point against the role it was submitted for.

        Zone membership is checked through the ``(point, role)`` pair rather
        than through the point alone, so a temperature point that is only a
        safety interlock of the zone is refused as precisely as one belonging
        to another zone entirely.

        Args:
            role: The role's wiring contract.
            point_id: The submitted point.
            assigned: Points of the zone, keyed by point and assigned role.

        Raises:
            InvalidControlLoopPointError: If the point is not part of the zone
                in that role, is archived, or contradicts the contract.
        """
        point: Point | None = assigned.get((point_id, role.role))
        if point is None:
            raise InvalidControlLoopPointError(role.field, point_id, "point_not_assigned_to_zone")
        if point.status is not StatusEnum.ACTIVE:
            raise InvalidControlLoopPointError(role.field, point_id, "point_not_active")
        if point.point_kind is not role.point_kind:
            raise InvalidControlLoopPointError(role.field, point_id, "point_kind_mismatch")
        if point.data_type not in role.data_types:
            raise InvalidControlLoopPointError(role.field, point_id, "data_type_mismatch")
        if point.metric_type != role.metric_type:
            raise InvalidControlLoopPointError(role.field, point_id, "metric_type_mismatch")
        if point.unit != role.unit:
            raise InvalidControlLoopPointError(role.field, point_id, "unit_mismatch")


class CommandService:
    """Reads the command history and creates the one command a client may ask for.

    A control-loop command stays a consequence of accepted telemetry and is
    never created here: :mod:`ai_greenhouse.control.automation` owns that path
    and nothing about it changes. What this service adds is the manual boundary —
    a customer-facing client asking one explicitly configured actuator for one
    boolean state — and it reaches the Edge through the same pending row, the
    same gateway resolution and the same acknowledgement as an automatic one.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service around one session.

        Args:
            session: The session of the request being served.
        """
        self._commands: CommandRepository = CommandRepository(session)
        self._gateways: GatewayRepository = GatewayRepository(session)
        self._clock: Clock = utc_now

    async def create_manual_command(
        self,
        payload: ManualCommandCreate,
        *,
        idempotency_key: str,
    ) -> tuple[Command, bool]:
        """Create one manual command, or return the one the key already names.

        Eligibility is decided entirely from persisted domain data: the zone's
        composition, the assignment's role, the point's own columns and the
        explicit reported-point relationship. No code, name, unit, metric,
        substring or list position takes part in it, so a point cannot be made
        commandable by what it is called.

        Nothing is written before every rule has passed, and the write itself is
        one statement that either claims the idempotency key or does not. A
        replay therefore reads the stored command back without inserting a
        second row and without offering the Edge a second delivery.

        The command is created ``pending``. It is never marked applied here, and
        no point state or telemetry is written: what the actuator actually does
        arrives later, over the Edge acknowledgement and the telemetry boundary.

        Args:
            payload: The validated request body.
            idempotency_key: The client's key, taken from the required
                ``Idempotency-Key`` header.

        Returns:
            The stored command and whether this call created it.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            ReferencedPointNotFoundError: If no point has that identifier.
            InvalidManualCommandTargetError: If the point cannot be commanded.
            IdempotencyKeyConflictError: If the key already identifies a
                different logical request.
        """
        replayed = await self._commands.get_by_key(idempotency_key)
        if replayed is not None:
            self._ensure_same_request(replayed, payload, idempotency_key)
            return replayed, False

        target, reported = await self._resolve_manual_target(payload)
        gateway: Gateway | None = await self._gateways.gateway_for_command_points(
            target.id,
            reported.id,
        )
        if gateway is None:
            raise InvalidManualCommandTargetError(
                payload.control_zone_id,
                payload.target_point_id,
                "no_gateway_for_command_points",
            )

        issued_at: datetime = self._clock()
        candidate = Command(
            id=uuid4(),
            source=CommandSource.MANUAL,
            idempotency_key=idempotency_key,
            control_zone_id=payload.control_zone_id,
            control_loop_id=None,
            trigger_sample_id=None,
            target_point_id=target.id,
            reported_point_id=reported.id,
            gateway_id=gateway.id,
            desired_value=payload.desired_value,
            state=CommandState.PENDING,
            result_control_sample_id=None,
            result_status_sample_id=None,
            issued_at=issued_at,
            executed_at=None,
            acknowledged_at=None,
            rejection_reason=None,
            created_at=issued_at,
        )
        created: bool = await self._commands.insert_if_key_absent(candidate)

        stored = await self._commands.get_by_key(idempotency_key)
        if stored is None:
            raise RuntimeError("Idempotency key claim completed without a readable command")
        self._ensure_same_request(stored, payload, idempotency_key)
        return stored, created

    async def get_command(self, command_id: UUID) -> Command:
        """Return one command or report it missing.

        Args:
            command_id: The command requested by the path.

        Returns:
            The stored command.

        Raises:
            CommandNotFoundError: If the path names no command.
        """
        command = await self._commands.get_by_id(command_id)
        if command is None:
            raise CommandNotFoundError(command_id)
        return command

    async def list_commands(
        self,
        *,
        control_zone_id: UUID | None,
        control_loop_id: UUID | None,
        trigger_sample_id: UUID | None,
        target_point_id: UUID | None,
        source: CommandSource | None,
        idempotency_key: str | None,
        limit: int,
    ) -> list[Command]:
        """Return a bounded, newest-first window of commands.

        Args:
            control_zone_id: Restricts the result to one zone when given.
            control_loop_id: Restricts the result to one loop when given.
            trigger_sample_id: Restricts the result to the commands one
                measurement caused when given.
            target_point_id: Restricts the result to one commanded point when
                given.
            source: Restricts the result to one author when given.
            idempotency_key: Resolves the one command a key identifies when
                given, which is how a client that lost a creation response finds
                out whether its request exists.
            limit: Maximum number of commands to return.

        Returns:
            Matching commands, newest first.
        """
        return await self._commands.list_history(
            control_zone_id=control_zone_id,
            control_loop_id=control_loop_id,
            trigger_sample_id=trigger_sample_id,
            target_point_id=target_point_id,
            source=source,
            idempotency_key=idempotency_key,
            limit=limit,
        )

    async def _resolve_manual_target(
        self,
        payload: ManualCommandCreate,
    ) -> tuple[Point, Point]:
        """Apply every manual-command eligibility rule and return both points.

        Args:
            payload: The validated request body.

        Returns:
            The control point to command and the status point that reports it
            back.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            ReferencedPointNotFoundError: If no point has that identifier.
            InvalidManualCommandTargetError: If any rule refuses the point.
        """
        zone: ControlZone | None = await self._commands.get_zone(payload.control_zone_id)
        if zone is None:
            raise ControlZoneNotFoundError(payload.control_zone_id)

        target: Point | None = await self._commands.get_point(payload.target_point_id)
        if target is None:
            raise ReferencedPointNotFoundError(payload.target_point_id)

        refuse = partial(
            InvalidManualCommandTargetError,
            payload.control_zone_id,
            payload.target_point_id,
        )
        if zone.status is not StatusEnum.ACTIVE:
            raise refuse("zone_not_active")
        facility: Facility | None = await self._commands.get_facility(zone.facility_id)
        if facility is None or target.site_id != facility.site_id:
            raise refuse("point_not_in_zone_facility")
        if target.facility_id is not None and target.facility_id != zone.facility_id:
            raise refuse("point_not_in_zone_facility")

        roles = await self._commands.list_assignment_roles(
            payload.control_zone_id,
            payload.target_point_id,
        )
        if not roles:
            raise refuse("point_not_assigned_to_zone")
        if ZonePointRole.CONTROL_OUTPUT not in roles:
            raise refuse("assignment_role_not_control_output")
        if target.point_kind is not PointKind.CONTROL:
            raise refuse("point_kind_not_control")
        if target.status is not StatusEnum.ACTIVE:
            raise refuse("point_not_active")
        if target.data_type is not PointDataType.BOOLEAN:
            raise refuse("point_data_type_not_boolean")
        if target.reported_point_id is None:
            raise refuse("reported_point_not_configured")

        reported: Point | None = await self._commands.get_point(target.reported_point_id)
        if reported is None:
            raise refuse("reported_point_not_found")
        if reported.status is not StatusEnum.ACTIVE:
            raise refuse("reported_point_not_active")
        if reported.point_kind is not PointKind.STATUS:
            raise refuse("reported_point_kind_mismatch")
        if reported.data_type is not PointDataType.BOOLEAN:
            raise refuse("reported_point_data_type_not_boolean")
        return target, reported

    @staticmethod
    def _ensure_same_request(
        command: Command,
        payload: ManualCommandCreate,
        idempotency_key: str,
    ) -> None:
        """Refuse a key that already identifies a different logical request.

        The identity of a request is its source, its zone, its target and the
        value it asked for. Everything else about the stored command — its
        state, its acknowledgement, its gateway — is what happened *to* it and
        must not make a replay look like a conflict.

        Args:
            command: The command the key already identifies.
            payload: The request being replayed.
            idempotency_key: The key both claim.

        Raises:
            IdempotencyKeyConflictError: If the two are not the same request.
        """
        identical: bool = (
            command.source is CommandSource.MANUAL
            and command.control_zone_id == payload.control_zone_id
            and command.target_point_id == payload.target_point_id
            and command.desired_value == payload.desired_value
        )
        if not identical:
            raise IdempotencyKeyConflictError(idempotency_key, command.id)
