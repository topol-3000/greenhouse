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
from ai_greenhouse.topology.models import Site


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
