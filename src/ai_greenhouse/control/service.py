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

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.control.exceptions import (
    ControlLoopExistsError,
    ControlLoopNotFoundError,
    InvalidControlLoopPointError,
    InvalidControlLoopZoneError,
)
from ai_greenhouse.control.models import ControlLoop, ControlPolicyType
from ai_greenhouse.control.repository import ControlLoopRepository
from ai_greenhouse.control.schemas import ControlLoopCreate
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.points.models import Point, PointDataType, PointKind
from ai_greenhouse.points.repository import PointRepository
from ai_greenhouse.topology.exceptions import ControlZoneNotFoundError
from ai_greenhouse.topology.models import ZonePointRole, ZoneType

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
