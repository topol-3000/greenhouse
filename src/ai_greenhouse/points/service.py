"""Points business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.points.exceptions` and, for a missing site or facility,
those of :mod:`ai_greenhouse.topology.exceptions`, which
``ai_greenhouse.api.errors`` turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or
rollback belongs to the ``get_session`` request dependency. A point and its
state projection are created inside one such transaction — here, in the
service, and not by a database trigger.

Nothing in this module writes a point's value. There is no telemetry in
Milestone 1, and the projection stays at ``quality = no_data`` until there is.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.core.exceptions import ImmutableFieldError
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import (
    FacilityNotInSiteError,
    InvalidValueRangeError,
    PointCodeConflictError,
    PointFacilityArchivedError,
    PointNotFoundError,
    PointSiteArchivedError,
    PointStateNotFoundError,
    UnitNotAllowedError,
    ValueRangeNotAllowedError,
)
from ai_greenhouse.points.models import (
    DEFAULT_REVISION,
    DataQuality,
    Point,
    PointCurrentState,
    PointDataType,
    PointKind,
)
from ai_greenhouse.points.repository import PointCurrentStateRepository, PointRepository
from ai_greenhouse.points.schemas import PointCreate, PointUpdate
from ai_greenhouse.topology.exceptions import (
    ParentFacilityNotFoundError,
    ParentSiteNotFoundError,
)
from ai_greenhouse.topology.models import Facility, Site
from ai_greenhouse.topology.repository import FacilityRepository, SiteRepository

POINT_IMMUTABLE_FIELDS: frozenset[str] = frozenset(
    {"code", "site_id", "facility_id", "point_kind", "metric_type", "data_type"}
)
"""Point fields fixed at creation time. Naming one in a ``PATCH`` is an error.

Every one of them defines what the point *means*. Changing ``metric_type`` from
``air_temperature`` to ``relative_humidity``, or ``data_type`` from ``float`` to
``boolean``, would reinterpret every value already recorded against the point
without touching a single one of them. Unlike a facility or a zone, a point
therefore refuses to move between parents under the same generic code as the
rest: the refusal has one reason, so it has one code.
"""

UNIT_FORBIDDEN_DATA_TYPES: frozenset[PointDataType] = frozenset({PointDataType.BOOLEAN})
"""Data types a unit cannot describe. A ``string`` point may carry one.

Numeric points are not required to carry one. Plenty of real quantities are
dimensionless — pH, EC ratio, a light-utilisation index — and refusing them a
point would force a fake unit into the data, which is worse than an absent one.
"""

RANGE_ALLOWED_DATA_TYPES: frozenset[PointDataType] = frozenset(
    {PointDataType.FLOAT, PointDataType.INTEGER}
)
"""Data types that can be bounded. The others have no ordering to bound."""


class PointService:
    """Applies the point invariants over a single request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        The site and facility repositories are held as well: creating a point
        has to resolve its parents, and those reads belong in the data-access
        layer.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: PointRepository = PointRepository(session)
        self._states: PointCurrentStateRepository = PointCurrentStateRepository(session)
        self._sites: SiteRepository = SiteRepository(session)
        self._facilities: FacilityRepository = FacilityRepository(session)

    async def create_point(self, payload: PointCreate) -> Point:
        """Register a new point together with its empty state projection.

        Both rows are written inside the request's single transaction. The
        point is flushed first because its identifier is generated on that
        flush and the projection's primary key is that identifier; if either
        insert fails, the request dependency rolls back the whole transaction
        and no orphan projection survives.

        The code is checked before the insert so the common case returns a
        precise failure, and the ``(site_id, code)`` unique constraint is relied
        on as well so two concurrent requests cannot both succeed.

        Args:
            payload: The validated request body.

        Returns:
            The persisted point, with its generated id and timestamps.

        Raises:
            ParentSiteNotFoundError: If the referenced site does not exist.
            PointSiteArchivedError: If the referenced site is archived.
            ParentFacilityNotFoundError: If the referenced facility is missing.
            FacilityNotInSiteError: If that facility belongs to another site.
            PointFacilityArchivedError: If that facility is archived.
            UnitNotAllowedError: If a boolean point carries a unit.
            ValueRangeNotAllowedError: If a non-numeric point is bounded.
            PointCodeConflictError: If the code is taken on that site.
        """
        site: Site | None = await self._sites.get_by_id(payload.site_id)
        if site is None:
            raise ParentSiteNotFoundError(payload.site_id)
        if site.status is not StatusEnum.ACTIVE:
            raise PointSiteArchivedError(payload.site_id)

        if payload.facility_id is not None:
            facility: Facility | None = await self._facilities.get_by_id(payload.facility_id)
            if facility is None:
                raise ParentFacilityNotFoundError(payload.facility_id)
            if facility.site_id != payload.site_id:
                raise FacilityNotInSiteError(payload.site_id, payload.facility_id)
            if facility.status is not StatusEnum.ACTIVE:
                raise PointFacilityArchivedError(payload.facility_id)

        self._check_value_shape(
            data_type=payload.data_type,
            unit=payload.unit,
            min_value=payload.min_value,
            max_value=payload.max_value,
        )

        existing: Point | None = await self._repository.get_by_code(payload.site_id, payload.code)
        if existing is not None:
            raise PointCodeConflictError(payload.site_id, payload.code)

        point = Point(
            site_id=payload.site_id,
            facility_id=payload.facility_id,
            code=payload.code,
            name=payload.name,
            point_kind=payload.point_kind,
            metric_type=payload.metric_type,
            data_type=payload.data_type,
            unit=payload.unit,
            min_value=payload.min_value,
            max_value=payload.max_value,
        )
        self._repository.add(point)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise PointCodeConflictError(payload.site_id, payload.code) from error

        self._states.add(
            PointCurrentState(
                point_id=point.id,
                value=None,
                observed_at=None,
                received_at=None,
                quality=DataQuality.NO_DATA,
                revision=DEFAULT_REVISION,
            )
        )
        await self._states.flush()
        return point

    async def get_point(self, point_id: UUID) -> Point:
        """Load one point, archived ones included.

        Args:
            point_id: Identifier to look up.

        Returns:
            The requested point.

        Raises:
            PointNotFoundError: If no point has that identifier.
        """
        point: Point | None = await self._repository.get_by_id(point_id)
        if point is None:
            raise PointNotFoundError(point_id)
        return point

    async def get_point_state(self, point_id: UUID) -> PointCurrentState:
        """Load the current-state projection of one point.

        The point is resolved first so that an unknown identifier is reported
        as a missing *point*, which is what the caller asked about.

        Args:
            point_id: The point whose state to read.

        Returns:
            The point's state projection. Throughout Milestone 1 this carries
            no value and ``quality = no_data``.

        Raises:
            PointNotFoundError: If no point has that identifier.
            PointStateNotFoundError: If the point exists without a projection,
                which the schema and the creation path together rule out.
        """
        await self.get_point(point_id)
        state: PointCurrentState | None = await self._states.get_by_point_id(point_id)
        if state is None:
            raise PointStateNotFoundError(point_id)
        return state

    async def list_points(
        self,
        params: PageParams,
        *,
        site_id: UUID | None = None,
        facility_id: UUID | None = None,
        point_kind: PointKind | None = None,
        metric_type: str | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[Point], int]:
        """Return one page of points with the unpaged total.

        An unknown ``site_id`` or ``facility_id`` is not an error here: a filter
        matching nothing yields an empty page rather than a failure.

        Args:
            params: The resolved ``limit``/``offset`` window.
            site_id: Restricts the result to one site when given.
            facility_id: Restricts the result to one facility when given.
            point_kind: Restricts the result to one role of point when given.
            metric_type: Restricts the result to one measured quantity when
                given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's points and the total matching the filters.
        """
        return await self._repository.list_page(
            params,
            site_id=site_id,
            facility_id=facility_id,
            point_kind=point_kind,
            metric_type=metric_type,
            status=status,
        )

    async def update_point(self, point_id: UUID, payload: PointUpdate) -> Point:
        """Apply a partial update to a point.

        Fields absent from the request body are left alone. ``unit``,
        ``min_value`` and ``max_value`` are nullable, so submitting an explicit
        ``null`` for one of them clears it — which is why submission is decided
        by ``model_fields_set`` and not by the value being ``None``.

        The resulting combination of unit and range is validated against the
        point's stored ``data_type``, not against the request alone: a range
        left in place is judged against the type that has to carry it.

        Args:
            point_id: Identifier of the point to update.
            payload: The validated request body.

        Returns:
            The updated point.

        Raises:
            PointNotFoundError: If no point has that identifier.
            ImmutableFieldError: If the body names a field fixed at creation.
            UnitNotAllowedError: If the result would give a boolean point a
                unit.
            ValueRangeNotAllowedError: If the result would bound a non-numeric
                point.
            InvalidValueRangeError: If the resulting lower end is above the
                resulting upper end.
        """
        point: Point = await self.get_point(point_id)

        submitted: set[str] = payload.model_fields_set
        immutable: set[str] = submitted & POINT_IMMUTABLE_FIELDS
        if immutable:
            raise ImmutableFieldError(
                "A point's identity and meaning cannot be changed after creation",
                details={"fields": sorted(immutable), "point_id": str(point_id)},
            )

        unit: str | None = payload.unit if "unit" in submitted else point.unit
        min_value: Decimal | None = (
            payload.min_value if "min_value" in submitted else point.min_value
        )
        max_value: Decimal | None = (
            payload.max_value if "max_value" in submitted else point.max_value
        )
        self._check_value_shape(
            data_type=point.data_type,
            unit=unit,
            min_value=min_value,
            max_value=max_value,
        )
        if min_value is not None and max_value is not None and min_value > max_value:
            raise InvalidValueRangeError(min_value, max_value)

        if payload.name is not None:
            point.name = payload.name
        if payload.status is not None:
            point.status = payload.status
        if "unit" in submitted:
            point.unit = unit
        if "min_value" in submitted:
            point.min_value = min_value
        if "max_value" in submitted:
            point.max_value = max_value

        await self._repository.flush()
        return point

    @staticmethod
    def _check_value_shape(
        *,
        data_type: PointDataType,
        unit: str | None,
        min_value: Decimal | None,
        max_value: Decimal | None,
    ) -> None:
        """Check a unit and range against the data type they describe.

        A numeric point without a unit is accepted: it is dimensionless.

        Args:
            data_type: The point's data type, which decides both rules.
            unit: The resulting unit, or ``None``.
            min_value: The resulting lower bound, or ``None``.
            max_value: The resulting upper bound, or ``None``.

        Raises:
            UnitNotAllowedError: If a boolean point has a unit.
            ValueRangeNotAllowedError: If a non-numeric point is bounded.
        """
        if unit is not None and data_type in UNIT_FORBIDDEN_DATA_TYPES:
            raise UnitNotAllowedError(data_type.value)
        bounded: bool = min_value is not None or max_value is not None
        if bounded and data_type not in RANGE_ALLOWED_DATA_TYPES:
            raise ValueRangeNotAllowedError(data_type.value)
