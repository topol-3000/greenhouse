"""Data access for the topology module.

This layer holds SQLAlchemy statements and nothing else. Invariants, error
translation and the decision to commit belong to
:mod:`ai_greenhouse.topology.service`.

The assignment and configuration queries read the ``points`` tables as well.
They select from them; they never apply a point's rules, which stay in
:mod:`ai_greenhouse.points.service`.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point, PointCurrentState
from ai_greenhouse.topology.models import (
    ControlZone,
    Facility,
    FacilityType,
    Site,
    ZonePointAssignment,
    ZonePointRole,
    ZoneType,
)


class SiteRepository:
    """Queries over the ``sites`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, site: Site) -> None:
        """Stage a new site for insertion on the next flush.

        Args:
            site: The instance to persist.
        """
        self._session.add(site)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, site_id: UUID) -> Site | None:
        """Load a site by primary key.

        Args:
            site_id: Identifier to look up.

        Returns:
            The matching site, or ``None`` when no row exists.
        """
        return await self._session.get(Site, site_id)

    async def get_by_code(self, code: str) -> Site | None:
        """Load a site by its globally unique code.

        Args:
            code: The slug to look up.

        Returns:
            The matching site, or ``None`` when the code is free.
        """
        return await self._session.scalar(select(Site).where(Site.code == code))

    async def list_page(
        self,
        params: PageParams,
        *,
        status: StatusEnum | None = None,
    ) -> tuple[list[Site], int]:
        """Return one page of sites together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's sites, ordered by ``created_at ASC, id ASC``,
            and the total number of sites matching the filter.
        """
        statement: Select[tuple[Site]] = select(Site)
        if status is not None:
            statement = statement.where(Site.status == status)
        return await paginate(self._session, statement, Site, params)


class FacilityRepository:
    """Queries over the ``facilities`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, facility: Facility) -> None:
        """Stage a new facility for insertion on the next flush.

        Args:
            facility: The instance to persist.
        """
        self._session.add(facility)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, facility_id: UUID) -> Facility | None:
        """Load a facility by primary key.

        Args:
            facility_id: Identifier to look up.

        Returns:
            The matching facility, or ``None`` when no row exists.
        """
        return await self._session.get(Facility, facility_id)

    async def get_by_code(self, site_id: UUID, code: str) -> Facility | None:
        """Load a facility by its code within one site.

        Args:
            site_id: The site the code is scoped to.
            code: The slug to look up.

        Returns:
            The matching facility, or ``None`` when the code is free on that
            site. The same code on another site is a different facility and is
            not returned here.
        """
        return await self._session.scalar(
            select(Facility).where(Facility.site_id == site_id, Facility.code == code)
        )

    async def list_page(
        self,
        params: PageParams,
        *,
        site_id: UUID | None = None,
        facility_type: FacilityType | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[Facility], int]:
        """Return one page of facilities together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            site_id: Restricts the result to one site when given.
            facility_type: Restricts the result to one kind of object when given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's facilities, ordered by
            ``created_at ASC, id ASC``, and the total number of facilities
            matching the filters.
        """
        statement: Select[tuple[Facility]] = select(Facility)
        if site_id is not None:
            statement = statement.where(Facility.site_id == site_id)
        if facility_type is not None:
            statement = statement.where(Facility.facility_type == facility_type)
        if status is not None:
            statement = statement.where(Facility.status == status)
        return await paginate(self._session, statement, Facility, params)


class ControlZoneRepository:
    """Queries over the ``control_zones`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, control_zone: ControlZone) -> None:
        """Stage a new control zone for insertion on the next flush.

        Args:
            control_zone: The instance to persist.
        """
        self._session.add(control_zone)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, control_zone_id: UUID) -> ControlZone | None:
        """Load a control zone by primary key.

        Args:
            control_zone_id: Identifier to look up.

        Returns:
            The matching zone, or ``None`` when no row exists.
        """
        return await self._session.get(ControlZone, control_zone_id)

    async def get_by_code(self, facility_id: UUID, code: str) -> ControlZone | None:
        """Load a control zone by its code within one facility.

        Args:
            facility_id: The facility the code is scoped to.
            code: The slug to look up.

        Returns:
            The matching zone, or ``None`` when the code is free in that
            facility. The same code in another facility is a different zone and
            is not returned here.
        """
        return await self._session.scalar(
            select(ControlZone).where(
                ControlZone.facility_id == facility_id,
                ControlZone.code == code,
            )
        )

    async def list_page(
        self,
        params: PageParams,
        *,
        facility_id: UUID | None = None,
        zone_type: ZoneType | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[ControlZone], int]:
        """Return one page of control zones together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            facility_id: Restricts the result to one facility when given.
            zone_type: Restricts the result to one kind of zone when given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's zones, ordered by ``created_at ASC, id ASC``,
            and the total number of zones matching the filters.
        """
        statement: Select[tuple[ControlZone]] = select(ControlZone)
        if facility_id is not None:
            statement = statement.where(ControlZone.facility_id == facility_id)
        if zone_type is not None:
            statement = statement.where(ControlZone.zone_type == zone_type)
        if status is not None:
            statement = statement.where(ControlZone.status == status)
        return await paginate(self._session, statement, ControlZone, params)


class ZonePointAssignmentRepository:
    """Queries over the ``zone_point_assignments`` table.

    Every read that returns assignments to a caller returns the matching point
    with them. Assignments are only ever useful together with the point they
    name, and loading the two in one statement is what keeps a zone's
    composition a single query instead of one per link.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, assignment: ZonePointAssignment) -> None:
        """Stage a new assignment for insertion on the next flush.

        Args:
            assignment: The instance to persist.
        """
        self._session.add(assignment)

    async def delete(self, assignment: ZonePointAssignment) -> None:
        """Remove an assignment for real.

        This is the only physical delete in the domain. An assignment is a
        statement about a zone's current composition, not a record with a
        history, so there is nothing to archive.

        Args:
            assignment: The link to remove.
        """
        await self._session.delete(assignment)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, assignment_id: UUID) -> ZonePointAssignment | None:
        """Load an assignment by primary key.

        Args:
            assignment_id: Identifier to look up.

        Returns:
            The matching assignment, or ``None`` when no row exists.
        """
        return await self._session.get(ZonePointAssignment, assignment_id)

    async def get_by_zone_point_role(
        self,
        control_zone_id: UUID,
        point_id: UUID,
        role: ZonePointRole,
    ) -> ZonePointAssignment | None:
        """Load the assignment of one point to one zone under one role.

        Args:
            control_zone_id: The zone the link belongs to.
            point_id: The point the link names.
            role: The role held there.

        Returns:
            The matching assignment, or ``None`` when the combination is free.
            The same point in the same zone under another role is a different
            link and is not returned here.
        """
        return await self._session.scalar(
            select(ZonePointAssignment).where(
                ZonePointAssignment.control_zone_id == control_zone_id,
                ZonePointAssignment.point_id == point_id,
                ZonePointAssignment.role == role,
            )
        )

    async def get_by_role(
        self,
        control_zone_id: UUID,
        role: ZonePointRole,
    ) -> ZonePointAssignment | None:
        """Load one assignment holding a role in a zone, whatever its point.

        Args:
            control_zone_id: The zone to look in.
            role: The role to look for.

        Returns:
            The first matching assignment in creation order, or ``None`` when
            the role is unheld in that zone.
        """
        return await self._session.scalar(
            select(ZonePointAssignment)
            .where(
                ZonePointAssignment.control_zone_id == control_zone_id,
                ZonePointAssignment.role == role,
            )
            .order_by(ZonePointAssignment.created_at.asc(), ZonePointAssignment.id.asc())
            .limit(1)
        )

    async def list_page(
        self,
        params: PageParams,
        *,
        control_zone_id: UUID,
    ) -> tuple[list[Row[tuple[ZonePointAssignment, Point]]], int]:
        """Return one page of a zone's assignments with the points they name.

        Args:
            params: The resolved ``limit``/``offset`` window.
            control_zone_id: The zone whose composition to read.

        Returns:
            A tuple of ``(assignment, point)`` rows, ordered by the assignment's
            ``created_at ASC, id ASC``, and the total number of assignments in
            the zone.

        Archived points are not filtered out. The link exists as long as it has
        not been removed, and hiding it would make a zone's composition depend
        on the lifecycle of something the link does not own.
        """
        total: int | None = await self._session.scalar(
            select(func.count())
            .select_from(ZonePointAssignment)
            .where(ZonePointAssignment.control_zone_id == control_zone_id)
        )
        window: Select[tuple[ZonePointAssignment, Point]] = (
            select(ZonePointAssignment, Point)
            .join(Point, Point.id == ZonePointAssignment.point_id)
            .where(ZonePointAssignment.control_zone_id == control_zone_id)
            .order_by(ZonePointAssignment.created_at.asc(), ZonePointAssignment.id.asc())
            .limit(params.limit)
            .offset(params.offset)
        )
        rows: list[Row[tuple[ZonePointAssignment, Point]]] = list(
            (await self._session.execute(window)).all()
        )
        return rows, int(total or 0)


class FacilityConfigurationRepository:
    """The read side of ``GET /facilities/{facility_id}/configuration``.

    Three statements, whatever the size of the facility: its zones, the
    assignments of those zones, and its points with their state. Nothing here
    walks an ORM relationship, because walking one would turn the document into
    a query per zone and then a query per point.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    async def list_control_zones(
        self,
        facility_id: UUID,
        *,
        include_archived: bool,
    ) -> list[ControlZone]:
        """Load every zone of one facility in one statement.

        Args:
            facility_id: The facility whose zones to read.
            include_archived: Keeps archived zones in the result when true.

        Returns:
            The zones, ordered by ``created_at ASC, id ASC``.
        """
        statement: Select[tuple[ControlZone]] = select(ControlZone).where(
            ControlZone.facility_id == facility_id
        )
        if not include_archived:
            statement = statement.where(ControlZone.status == StatusEnum.ACTIVE)
        statement = statement.order_by(ControlZone.created_at.asc(), ControlZone.id.asc())
        return list(await self._session.scalars(statement))

    async def list_assignments(
        self,
        control_zone_ids: Sequence[UUID],
        *,
        include_archived: bool,
    ) -> list[Row[tuple[ZonePointAssignment, str]]]:
        """Load the assignments of several zones in one statement.

        Args:
            control_zone_ids: The zones whose links to read. An empty sequence
                short-circuits without touching the database.
            include_archived: Keeps links to archived points in the result when
                true. Excluding them by default is what keeps every
                ``point_id`` in a zone answerable from the document's own
                ``points`` list.

        Returns:
            ``(assignment, point code)`` rows, ordered by the zone and then by
            the assignment's ``created_at ASC, id ASC``.
        """
        if not control_zone_ids:
            return []
        statement: Select[tuple[ZonePointAssignment, str]] = (
            select(ZonePointAssignment, Point.code)
            .join(Point, Point.id == ZonePointAssignment.point_id)
            .where(ZonePointAssignment.control_zone_id.in_(control_zone_ids))
        )
        if not include_archived:
            statement = statement.where(Point.status == StatusEnum.ACTIVE)
        statement = statement.order_by(
            ZonePointAssignment.control_zone_id.asc(),
            ZonePointAssignment.created_at.asc(),
            ZonePointAssignment.id.asc(),
        )
        return list((await self._session.execute(statement)).all())

    async def list_points(
        self,
        facility_id: UUID,
        *,
        also_point_ids: Sequence[UUID],
        include_archived: bool,
    ) -> list[Row[tuple[Point, PointCurrentState | None]]]:
        """Load a facility's points with their state in one statement.

        Args:
            facility_id: The facility whose points to read.
            also_point_ids: Further points to include whatever their facility.
                The caller passes the points its zones refer to, so that a
                site-level point taking part in a zone is described by the
                document instead of being referred to and left unexplained.
            include_archived: Keeps archived points in the result when true.

        Returns:
            ``(point, state)`` rows, ordered by ``created_at ASC, id ASC``. The
            state is ``None`` only if the projection row is missing, which the
            creation path rules out.
        """
        belongs_here = Point.facility_id == facility_id
        statement: Select[tuple[Point, PointCurrentState | None]] = select(
            Point, PointCurrentState
        ).outerjoin(PointCurrentState, PointCurrentState.point_id == Point.id)
        if also_point_ids:
            statement = statement.where(belongs_here | Point.id.in_(also_point_ids))
        else:
            statement = statement.where(belongs_here)
        if not include_archived:
            statement = statement.where(Point.status == StatusEnum.ACTIVE)
        statement = statement.order_by(Point.created_at.asc(), Point.id.asc())
        return list((await self._session.execute(statement)).all())
