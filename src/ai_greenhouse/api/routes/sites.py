"""HTTP endpoints for sites.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.topology`.

There is no ``DELETE``: a site is retired with ``PATCH {"status": "archived"}``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import Site
from ai_greenhouse.topology.schemas import SiteCreate, SiteRead, SiteUpdate
from ai_greenhouse.topology.service import SiteService

router: APIRouter = APIRouter(prefix="/sites", tags=["sites"])


async def get_site_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SiteService:
    """Build the site service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``SiteService`` sharing the request's transaction.
    """
    return SiteService(session)


SiteServiceDep = Annotated[SiteService, Depends(get_site_service)]


@router.post("", response_model=SiteRead, status_code=status.HTTP_201_CREATED)
async def create_site(payload: SiteCreate, service: SiteServiceDep) -> SiteRead:
    """Register a site.

    Args:
        payload: The site to create.
        service: The site service for this request.

    Returns:
        The created site, with HTTP 201.
    """
    site: Site = await service.create_site(payload)
    return SiteRead.model_validate(site)


@router.get("", response_model=Page[SiteRead])
async def list_sites(
    service: SiteServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    site_status: Annotated[
        StatusEnum | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[SiteRead]:
    """List sites, oldest first.

    Args:
        service: The site service for this request.
        params: The requested collection window.
        site_status: Optional ``status`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    sites, total = await service.list_sites(params, status=site_status)
    return Page[SiteRead](
        items=[SiteRead.model_validate(site) for site in sites],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{site_id}", response_model=SiteRead)
async def get_site(site_id: UUID, service: SiteServiceDep) -> SiteRead:
    """Read one site, archived ones included.

    Args:
        site_id: Identifier of the site to read.
        service: The site service for this request.

    Returns:
        The requested site.
    """
    site: Site = await service.get_site(site_id)
    return SiteRead.model_validate(site)


@router.patch("/{site_id}", response_model=SiteRead)
async def update_site(
    site_id: UUID,
    payload: SiteUpdate,
    service: SiteServiceDep,
) -> SiteRead:
    """Update a site's name, timezone or status.

    Args:
        site_id: Identifier of the site to update.
        payload: The fields to change.
        service: The site service for this request.

    Returns:
        The updated site.
    """
    site: Site = await service.update_site(site_id, payload)
    return SiteRead.model_validate(site)
