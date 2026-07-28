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
from ai_greenhouse.topology.exceptions import (
    FacilityCodeConflictError,
    FacilityNotFoundError,
    FacilitySiteArchivedError,
    FacilitySiteImmutableError,
    ParentSiteNotFoundError,
    SiteCodeConflictError,
    SiteNotFoundError,
)
from ai_greenhouse.topology.models import Facility, FacilityType, Site
from ai_greenhouse.topology.repository import FacilityRepository, SiteRepository
from ai_greenhouse.topology.schemas import (
    FacilityCreate,
    FacilityUpdate,
    SiteCreate,
    SiteUpdate,
)

SITE_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code"})
"""Site fields fixed at creation time. Naming one in a ``PATCH`` is an error."""

FACILITY_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code"})
"""Facility fields reported as ``immutable_field``.

``site_id`` is immutable too but is answered with its own, more specific code;
see :class:`~ai_greenhouse.topology.exceptions.FacilitySiteImmutableError`.
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

        ``code`` is checked before ``site_id``, so a body naming both is
        answered with ``immutable_field``.

        Args:
            facility_id: Identifier of the facility to update.
            payload: The validated request body.

        Returns:
            The updated facility.

        Raises:
            FacilityNotFoundError: If no facility has that identifier.
            ImmutableFieldError: If the body names a field fixed at creation.
            FacilitySiteImmutableError: If the body names ``site_id``.
        """
        facility: Facility = await self.get_facility(facility_id)

        submitted: set[str] = payload.model_fields_set
        immutable: set[str] = submitted & FACILITY_IMMUTABLE_FIELDS
        if immutable:
            raise ImmutableFieldError(
                "Facility code cannot be changed after creation",
                details={"fields": sorted(immutable), "facility_id": str(facility_id)},
            )
        if "site_id" in submitted:
            raise FacilitySiteImmutableError(facility_id)

        if payload.name is not None:
            facility.name = payload.name
        if payload.facility_type is not None:
            facility.facility_type = payload.facility_type
        if payload.status is not None:
            facility.status = payload.status

        await self._repository.flush()
        return facility
