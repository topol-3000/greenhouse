"""Topology business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.topology.exceptions`, which ``ai_greenhouse.api.errors``
turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or
rollback belongs to the ``get_session`` request dependency.

Assignments and the facility configuration read points as well. They read them:
this module decides which point may take part in which zone, and never what a
point is, whether its unit suits its data type, or what its state should say.
Those rules stay in :mod:`ai_greenhouse.points.service`.
"""

from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.core.exceptions import ImmutableFieldError
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.points.models import Point, PointCurrentState, PointKind
from ai_greenhouse.points.repository import PointRepository
from ai_greenhouse.topology.exceptions import (
    AssignmentExistsError,
    AssignmentNotFoundError,
    AssignmentPointArchivedError,
    AssignmentZoneArchivedError,
    ControlZoneCodeConflictError,
    ControlZoneFacilityArchivedError,
    ControlZoneNotFoundError,
    CrossFacilityAssignmentError,
    CrossSiteAssignmentError,
    FacilityCodeConflictError,
    FacilityNotFoundError,
    FacilitySiteArchivedError,
    ParentFacilityNotFoundError,
    ParentSiteNotFoundError,
    PrimaryMeasurementExistsError,
    RoleKindMismatchError,
    SiteCodeConflictError,
    SiteNotFoundError,
)
from ai_greenhouse.topology.models import (
    ControlZone,
    Facility,
    FacilityType,
    Site,
    ZonePointAssignment,
    ZonePointRole,
    ZoneType,
)
from ai_greenhouse.topology.repository import (
    ControlZoneRepository,
    FacilityConfigurationRepository,
    FacilityRepository,
    SiteRepository,
    ZonePointAssignmentRepository,
)
from ai_greenhouse.topology.schemas import (
    ConfigurationPoint,
    ConfigurationZone,
    ConfigurationZonePoint,
    ControlZoneCreate,
    ControlZoneUpdate,
    FacilityConfigurationRead,
    FacilityCreate,
    FacilityUpdate,
    SiteCreate,
    SiteUpdate,
    ZonePointAssignmentCreate,
)

SITE_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code"})
"""Site fields fixed at creation time. Naming one in a ``PATCH`` is an error."""

FACILITY_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code", "site_id"})
"""Facility fields fixed at creation time.

``site_id`` is one of them: a facility never moves between sites, because the
zones and points below it would have to move with it. The refusal is reported
under the shared ``immutable_field`` code and names the field in its details.
"""

CONTROL_ZONE_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code", "facility_id"})
"""Control zone fields fixed at creation time.

``facility_id`` is one of them: a zone belongs to exactly one facility, and the
points assigned to it are scoped by that facility too, so re-parenting a zone
would silently break those assignments.
"""

ROLE_ALLOWED_POINT_KINDS: dict[ZonePointRole, frozenset[PointKind]] = {
    ZonePointRole.PRIMARY_MEASUREMENT: frozenset({PointKind.MEASUREMENT, PointKind.DERIVED}),
    ZonePointRole.SECONDARY_MEASUREMENT: frozenset({PointKind.MEASUREMENT, PointKind.DERIVED}),
    ZonePointRole.CONTROL_OUTPUT: frozenset({PointKind.CONTROL}),
    ZonePointRole.STATUS_FEEDBACK: frozenset({PointKind.STATUS}),
    ZonePointRole.SAFETY_INTERLOCK: frozenset(PointKind),
    ZonePointRole.DERIVED_INDICATOR: frozenset(PointKind),
}
"""Which kind of point each role accepts, declared once and in one place.

A control loop reads this table to know what it is looking at: the process
variable of a zone has to be something that measures, and its output has to be
something that acts. Spreading the same knowledge across conditionals in the
service is how the two drift apart.

``safety_interlock`` and ``derived_indicator`` accept every kind on purpose. A
safety interlock can be a measured limit, a hardware trip signal or a computed
condition, and none of the three is more legitimate than the others. Narrowing
either role would be a domain decision that has not been made, and one that
cannot be reversed without invalidating assignments that already exist.

Every role is listed. A role absent from this table would be silently
unassignable, so the mapping is exhaustive and a unit test keeps it that way.
"""


class SiteService:
    """Applies the site invariants over a single request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repository around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: SiteRepository = SiteRepository(session)

    async def create_site(self, payload: SiteCreate) -> Site:
        """Register a new site.

        The code is checked before the insert so the common case returns a
        precise failure, and the unique index is relied on as well so two
        concurrent requests cannot both succeed.

        Args:
            payload: The validated request body.

        Returns:
            The persisted site, with its generated id and timestamps.

        Raises:
            SiteCodeConflictError: If the code is already taken.
        """
        if await self._repository.get_by_code(payload.code) is not None:
            raise SiteCodeConflictError(payload.code)

        site = Site(name=payload.name, code=payload.code, timezone=payload.timezone)
        self._repository.add(site)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise SiteCodeConflictError(payload.code) from error
        return site

    async def get_site(self, site_id: UUID) -> Site:
        """Load one site, archived ones included.

        Args:
            site_id: Identifier to look up.

        Returns:
            The requested site.

        Raises:
            SiteNotFoundError: If no site has that identifier.
        """
        site: Site | None = await self._repository.get_by_id(site_id)
        if site is None:
            raise SiteNotFoundError(site_id)
        return site

    async def list_sites(
        self,
        params: PageParams,
        *,
        status: StatusEnum | None = None,
    ) -> tuple[list[Site], int]:
        """Return one page of sites with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's sites and the total matching the filter.
        """
        return await self._repository.list_page(params, status=status)

    async def update_site(self, site_id: UUID, payload: SiteUpdate) -> Site:
        """Apply a partial update to a site.

        Fields absent from the request body are left alone. An explicit
        ``null`` is treated the same way, because no site field is nullable.

        Args:
            site_id: Identifier of the site to update.
            payload: The validated request body.

        Returns:
            The updated site.

        Raises:
            SiteNotFoundError: If no site has that identifier.
            ImmutableFieldError: If the body names a field fixed at creation.
        """
        site: Site = await self.get_site(site_id)

        submitted: set[str] = payload.model_fields_set
        immutable: set[str] = submitted & SITE_IMMUTABLE_FIELDS
        if immutable:
            raise ImmutableFieldError(
                "Site code cannot be changed after creation",
                details={"fields": sorted(immutable), "site_id": str(site_id)},
            )

        if payload.name is not None:
            site.name = payload.name
        if payload.timezone is not None:
            site.timezone = payload.timezone
        if payload.status is not None:
            site.status = payload.status

        await self._repository.flush()
        return site


class FacilityService:
    """Applies the facility invariants over a single request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        The site repository is held as well: creating a facility has to resolve
        its parent, and that read belongs in the data-access layer.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: FacilityRepository = FacilityRepository(session)
        self._sites: SiteRepository = SiteRepository(session)

    async def create_facility(self, payload: FacilityCreate) -> Facility:
        """Register a new facility inside an existing, active site.

        The code is checked before the insert so the common case returns a
        precise failure, and the ``(site_id, code)`` unique constraint is relied
        on as well so two concurrent requests cannot both succeed.

        Args:
            payload: The validated request body.

        Returns:
            The persisted facility, with its generated id and timestamps.

        Raises:
            ParentSiteNotFoundError: If the referenced site does not exist.
            FacilitySiteArchivedError: If the referenced site is archived.
            FacilityCodeConflictError: If the code is taken on that site.
        """
        site: Site | None = await self._sites.get_by_id(payload.site_id)
        if site is None:
            raise ParentSiteNotFoundError(payload.site_id)
        if site.status is not StatusEnum.ACTIVE:
            raise FacilitySiteArchivedError(payload.site_id)

        existing: Facility | None = await self._repository.get_by_code(
            payload.site_id,
            payload.code,
        )
        if existing is not None:
            raise FacilityCodeConflictError(payload.site_id, payload.code)

        facility = Facility(
            site_id=payload.site_id,
            name=payload.name,
            code=payload.code,
            facility_type=payload.facility_type,
        )
        self._repository.add(facility)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise FacilityCodeConflictError(payload.site_id, payload.code) from error
        return facility

    async def get_facility(self, facility_id: UUID) -> Facility:
        """Load one facility, archived ones included.

        Args:
            facility_id: Identifier to look up.

        Returns:
            The requested facility.

        Raises:
            FacilityNotFoundError: If no facility has that identifier.
        """
        facility: Facility | None = await self._repository.get_by_id(facility_id)
        if facility is None:
            raise FacilityNotFoundError(facility_id)
        return facility

    async def list_facilities(
        self,
        params: PageParams,
        *,
        site_id: UUID | None = None,
        facility_type: FacilityType | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[Facility], int]:
        """Return one page of facilities with the unpaged total.

        An unknown ``site_id`` is not an error here: a filter matching nothing
        yields an empty page rather than a failure.

        Args:
            params: The resolved ``limit``/``offset`` window.
            site_id: Restricts the result to one site when given.
            facility_type: Restricts the result to one kind of object when given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's facilities and the total matching the filters.
        """
        return await self._repository.list_page(
            params,
            site_id=site_id,
            facility_type=facility_type,
            status=status,
        )

    async def update_facility(self, facility_id: UUID, payload: FacilityUpdate) -> Facility:
        """Apply a partial update to a facility.

        Fields absent from the request body are left alone. An explicit ``null``
        is treated the same way, because no facility field is nullable.

        Args:
            facility_id: Identifier of the facility to update.
            payload: The validated request body.

        Returns:
            The updated facility.

        Raises:
            FacilityNotFoundError: If no facility has that identifier.
            ImmutableFieldError: If the body names a field fixed at creation.
        """
        facility: Facility = await self.get_facility(facility_id)

        submitted: set[str] = payload.model_fields_set
        immutable: set[str] = submitted & FACILITY_IMMUTABLE_FIELDS
        if immutable:
            raise ImmutableFieldError(
                "A facility's code and site cannot be changed after creation",
                details={"fields": sorted(immutable), "facility_id": str(facility_id)},
            )

        if payload.name is not None:
            facility.name = payload.name
        if payload.facility_type is not None:
            facility.facility_type = payload.facility_type
        if payload.status is not None:
            facility.status = payload.status

        await self._repository.flush()
        return facility


class ControlZoneService:
    """Applies the control zone invariants over a single request's session.

    Two rules a naive implementation would add are deliberately absent: zones of
    different types may overlap, and so may two zones of the *same* type in one
    facility. Overlap is a feature of the domain model — the conflict between
    overlapping zones is resolved later by priority and policy, not by refusing
    the second zone here.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        The facility repository is held as well: creating a zone has to resolve
        its parent, and that read belongs in the data-access layer.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: ControlZoneRepository = ControlZoneRepository(session)
        self._facilities: FacilityRepository = FacilityRepository(session)

    async def create_control_zone(self, payload: ControlZoneCreate) -> ControlZone:
        """Register a new zone inside an existing, active facility.

        The code is checked before the insert so the common case returns a
        precise failure, and the ``(facility_id, code)`` unique constraint is
        relied on as well so two concurrent requests cannot both succeed.

        The site is not consulted at all: a zone reaches it through the
        facility, which is what keeps the zone inside one facility's boundary.

        Args:
            payload: The validated request body.

        Returns:
            The persisted zone, with its generated id and timestamps.

        Raises:
            ParentFacilityNotFoundError: If the referenced facility is missing.
            ControlZoneFacilityArchivedError: If that facility is archived.
            ControlZoneCodeConflictError: If the code is taken in that facility.
        """
        facility: Facility | None = await self._facilities.get_by_id(payload.facility_id)
        if facility is None:
            raise ParentFacilityNotFoundError(payload.facility_id)
        if facility.status is not StatusEnum.ACTIVE:
            raise ControlZoneFacilityArchivedError(payload.facility_id)

        existing: ControlZone | None = await self._repository.get_by_code(
            payload.facility_id,
            payload.code,
        )
        if existing is not None:
            raise ControlZoneCodeConflictError(payload.facility_id, payload.code)

        control_zone = ControlZone(
            facility_id=payload.facility_id,
            name=payload.name,
            code=payload.code,
            zone_type=payload.zone_type,
        )
        self._repository.add(control_zone)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise ControlZoneCodeConflictError(payload.facility_id, payload.code) from error
        return control_zone

    async def get_control_zone(self, control_zone_id: UUID) -> ControlZone:
        """Load one zone, archived ones included.

        Args:
            control_zone_id: Identifier to look up.

        Returns:
            The requested zone.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
        """
        control_zone: ControlZone | None = await self._repository.get_by_id(control_zone_id)
        if control_zone is None:
            raise ControlZoneNotFoundError(control_zone_id)
        return control_zone

    async def list_control_zones(
        self,
        params: PageParams,
        *,
        facility_id: UUID | None = None,
        zone_type: ZoneType | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[ControlZone], int]:
        """Return one page of zones with the unpaged total.

        An unknown ``facility_id`` is not an error here: a filter matching
        nothing yields an empty page rather than a failure.

        Args:
            params: The resolved ``limit``/``offset`` window.
            facility_id: Restricts the result to one facility when given.
            zone_type: Restricts the result to one kind of zone when given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's zones and the total matching the filters.
        """
        return await self._repository.list_page(
            params,
            facility_id=facility_id,
            zone_type=zone_type,
            status=status,
        )

    async def update_control_zone(
        self,
        control_zone_id: UUID,
        payload: ControlZoneUpdate,
    ) -> ControlZone:
        """Apply a partial update to a control zone.

        Fields absent from the request body are left alone. An explicit ``null``
        is treated the same way, because no zone field is nullable.

        Args:
            control_zone_id: Identifier of the zone to update.
            payload: The validated request body.

        Returns:
            The updated zone.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            ImmutableFieldError: If the body names a field fixed at creation.
        """
        control_zone: ControlZone = await self.get_control_zone(control_zone_id)

        submitted: set[str] = payload.model_fields_set
        immutable: set[str] = submitted & CONTROL_ZONE_IMMUTABLE_FIELDS
        if immutable:
            raise ImmutableFieldError(
                "A control zone's code and facility cannot be changed after creation",
                details={
                    "fields": sorted(immutable),
                    "control_zone_id": str(control_zone_id),
                },
            )

        if payload.name is not None:
            control_zone.name = payload.name
        if payload.zone_type is not None:
            control_zone.zone_type = payload.zone_type
        if payload.status is not None:
            control_zone.status = payload.status

        await self._repository.flush()
        return control_zone


class ZonePointAssignmentService:
    """Applies the zone-point assignment invariants over one request's session.

    This is where a growbox stops being three independent lists. Every rule here
    exists to keep the resulting configuration one a control loop can act on: a
    zone whose primary measurement is a control point, or whose output belongs to
    a facility on the other side of the site, describes a growbox that cannot be
    driven.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: ZonePointAssignmentRepository = ZonePointAssignmentRepository(session)
        self._control_zones: ControlZoneRepository = ControlZoneRepository(session)
        self._facilities: FacilityRepository = FacilityRepository(session)
        self._points: PointRepository = PointRepository(session)

    async def create_assignment(
        self,
        control_zone_id: UUID,
        payload: ZonePointAssignmentCreate,
    ) -> tuple[ZonePointAssignment, Point]:
        """Assign a point to a zone under one role.

        The checks run from the widest failure to the narrowest, so that a point
        belonging to another site is reported as exactly that rather than as
        whichever narrower rule it happens to break on the way.

        Args:
            control_zone_id: The zone from the request path.
            payload: The validated request body.

        Returns:
            The persisted assignment together with the point it names, so the
            caller can answer with the enriched representation without a second
            read.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            ReferencedPointNotFoundError: If the body names a missing point.
            AssignmentZoneArchivedError: If the zone is archived.
            AssignmentPointArchivedError: If the point is archived.
            CrossSiteAssignmentError: If the two belong to different sites.
            CrossFacilityAssignmentError: If the point belongs to another
                facility of the same site.
            RoleKindMismatchError: If the role does not suit the point's kind.
            PrimaryMeasurementExistsError: If the zone already has a primary
                measurement.
            AssignmentExistsError: If that exact link already exists.
        """
        control_zone: ControlZone | None = await self._control_zones.get_by_id(control_zone_id)
        if control_zone is None:
            raise ControlZoneNotFoundError(control_zone_id)

        point: Point | None = await self._points.get_by_id(payload.point_id)
        if point is None:
            raise ReferencedPointNotFoundError(payload.point_id)

        if control_zone.status is not StatusEnum.ACTIVE:
            raise AssignmentZoneArchivedError(control_zone_id)
        if point.status is not StatusEnum.ACTIVE:
            raise AssignmentPointArchivedError(point.id)

        facility: Facility | None = await self._facilities.get_by_id(control_zone.facility_id)
        if facility is None:
            raise ParentFacilityNotFoundError(control_zone.facility_id)

        if point.site_id != facility.site_id:
            raise CrossSiteAssignmentError(facility.site_id, point.site_id)
        if point.facility_id is not None and point.facility_id != control_zone.facility_id:
            raise CrossFacilityAssignmentError(control_zone.facility_id, point.facility_id)

        allowed_kinds: frozenset[PointKind] = ROLE_ALLOWED_POINT_KINDS[payload.role]
        if point.point_kind not in allowed_kinds:
            raise RoleKindMismatchError(
                payload.role.value,
                point.point_kind.value,
                sorted(kind.value for kind in allowed_kinds),
            )

        if payload.role is ZonePointRole.PRIMARY_MEASUREMENT:
            held: ZonePointAssignment | None = await self._repository.get_by_role(
                control_zone_id,
                ZonePointRole.PRIMARY_MEASUREMENT,
            )
            if held is not None:
                raise PrimaryMeasurementExistsError(control_zone_id, held.point_id)

        duplicate: ZonePointAssignment | None = await self._repository.get_by_zone_point_role(
            control_zone_id,
            payload.point_id,
            payload.role,
        )
        if duplicate is not None:
            raise AssignmentExistsError(control_zone_id, payload.point_id, payload.role.value)

        assignment = ZonePointAssignment(
            control_zone_id=control_zone_id,
            point_id=payload.point_id,
            role=payload.role,
        )
        self._repository.add(assignment)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise AssignmentExistsError(
                control_zone_id,
                payload.point_id,
                payload.role.value,
            ) from error
        return assignment, point

    async def list_assignments(
        self,
        control_zone_id: UUID,
        params: PageParams,
    ) -> tuple[list[Row[tuple[ZonePointAssignment, Point]]], int]:
        """Return one page of a zone's composition with the unpaged total.

        Unlike the collection filters elsewhere, an unknown zone is an error
        here: the zone comes from the path, so the client asked about a resource
        that does not exist rather than for a filter that matches nothing.

        Args:
            control_zone_id: The zone from the request path.
            params: The resolved ``limit``/``offset`` window.

        Returns:
            A tuple of ``(assignment, point)`` rows and the total number of
            assignments in the zone.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
        """
        if await self._control_zones.get_by_id(control_zone_id) is None:
            raise ControlZoneNotFoundError(control_zone_id)
        return await self._repository.list_page(params, control_zone_id=control_zone_id)

    async def delete_assignment(self, control_zone_id: UUID, assignment_id: UUID) -> None:
        """Remove one point from a zone's composition.

        The point itself is untouched: this deletes the link, and the point
        keeps its identity, its code and its telemetry history.

        Args:
            control_zone_id: The zone from the request path.
            assignment_id: The link to remove.

        Raises:
            ControlZoneNotFoundError: If no zone has that identifier.
            AssignmentNotFoundError: If the link does not exist, or exists in
                another zone.
        """
        if await self._control_zones.get_by_id(control_zone_id) is None:
            raise ControlZoneNotFoundError(control_zone_id)

        assignment: ZonePointAssignment | None = await self._repository.get_by_id(assignment_id)
        if assignment is None or assignment.control_zone_id != control_zone_id:
            raise AssignmentNotFoundError(control_zone_id, assignment_id)

        await self._repository.delete(assignment)
        await self._repository.flush()


class FacilityConfigurationService:
    """Assembles the configuration document of one facility.

    The document is built from a fixed number of statements — the facility, its
    site, its zones, the assignments of those zones and its points with their
    state — and never by loading an object graph. That is a documented contract
    and not an optimisation: a growbox with a hundred points must cost the same
    number of queries as one with four.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: FacilityConfigurationRepository = FacilityConfigurationRepository(session)
        self._facilities: FacilityRepository = FacilityRepository(session)
        self._sites: SiteRepository = SiteRepository(session)

    async def get_configuration(
        self,
        facility_id: UUID,
        *,
        include_archived: bool = False,
    ) -> FacilityConfigurationRead:
        """Read one facility's whole configuration.

        Args:
            facility_id: The facility from the request path.
            include_archived: Keeps archived zones and points in the document
                when true. They are left out by default because the document
                describes what the growbox *is*, and a retired zone is no longer
                part of it.

        Returns:
            The assembled configuration document.

        Raises:
            FacilityNotFoundError: If no facility has that identifier.
            ParentSiteNotFoundError: If the facility's site is missing, which
                the schema's foreign key rules out.
        """
        facility: Facility | None = await self._facilities.get_by_id(facility_id)
        if facility is None:
            raise FacilityNotFoundError(facility_id)

        site: Site | None = await self._sites.get_by_id(facility.site_id)
        if site is None:
            raise ParentSiteNotFoundError(facility.site_id)

        control_zones: list[ControlZone] = await self._repository.list_control_zones(
            facility_id,
            include_archived=include_archived,
        )
        assignment_rows: list[
            Row[tuple[ZonePointAssignment, str]]
        ] = await self._repository.list_assignments(
            [control_zone.id for control_zone in control_zones],
            include_archived=include_archived,
        )

        links_by_zone: dict[UUID, list[ConfigurationZonePoint]] = {
            control_zone.id: [] for control_zone in control_zones
        }
        assigned_point_ids: list[UUID] = []
        for assignment, point_code in assignment_rows:
            links_by_zone[assignment.control_zone_id].append(
                ConfigurationZonePoint(
                    point_id=assignment.point_id,
                    code=point_code,
                    role=assignment.role,
                )
            )
            assigned_point_ids.append(assignment.point_id)

        point_rows: list[
            Row[tuple[Point, PointCurrentState | None]]
        ] = await self._repository.list_points(
            facility_id,
            also_point_ids=assigned_point_ids,
            include_archived=include_archived,
        )

        return FacilityConfigurationRead.from_parts(
            facility,
            site,
            [
                ConfigurationZone.from_zone(control_zone, links_by_zone[control_zone.id])
                for control_zone in control_zones
            ],
            [ConfigurationPoint.from_point(point, state) for point, state in point_rows],
        )
