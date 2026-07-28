"""Data access for the topology module.

This layer holds SQLAlchemy statements and nothing else. Invariants, error
translation and the decision to commit belong to
:mod:`ai_greenhouse.topology.service`.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import Facility, FacilityType, Site


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
