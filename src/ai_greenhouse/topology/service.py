"""Site business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.topology.exceptions`, which ``ai_greenhouse.api.errors``
turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or
rollback belongs to the ``get_session`` request dependency.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.core.exceptions import ImmutableFieldError
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.exceptions import SiteCodeConflictError, SiteNotFoundError
from ai_greenhouse.topology.models import Site
from ai_greenhouse.topology.repository import SiteRepository
from ai_greenhouse.topology.schemas import SiteCreate, SiteUpdate

IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code"})
"""Fields fixed at creation time. Naming one in a ``PATCH`` is an error."""


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
        immutable: set[str] = submitted & IMMUTABLE_FIELDS
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
