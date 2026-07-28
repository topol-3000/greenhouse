"""HTTP endpoints for facilities.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.topology`.

There is no ``DELETE``: a facility is retired with
``PATCH {"status": "archived"}``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import Facility, FacilityType
from ai_greenhouse.topology.schemas import (
    FacilityConfigurationRead,
    FacilityCreate,
    FacilityRead,
    FacilityUpdate,
)
from ai_greenhouse.topology.service import FacilityConfigurationService, FacilityService

router: APIRouter = APIRouter(prefix="/facilities", tags=["facilities"])


async def get_facility_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FacilityService:
    """Build the facility service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``FacilityService`` sharing the request's transaction.
    """
    return FacilityService(session)


FacilityServiceDep = Annotated[FacilityService, Depends(get_facility_service)]


async def get_configuration_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FacilityConfigurationService:
    """Build the configuration read service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``FacilityConfigurationService`` sharing the request's transaction.
    """
    return FacilityConfigurationService(session)


ConfigurationServiceDep = Annotated[
    FacilityConfigurationService,
    Depends(get_configuration_service),
]


@router.post("", response_model=FacilityRead, status_code=status.HTTP_201_CREATED)
async def create_facility(
    payload: FacilityCreate,
    service: FacilityServiceDep,
) -> FacilityRead:
    """Register a facility inside a site.

    Args:
        payload: The facility to create.
        service: The facility service for this request.

    Returns:
        The created facility, with HTTP 201.
    """
    facility: Facility = await service.create_facility(payload)
    return FacilityRead.model_validate(facility)


@router.get("", response_model=Page[FacilityRead])
async def list_facilities(
    service: FacilityServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    site_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the facilities of one site."),
    ] = None,
    facility_type: Annotated[
        FacilityType | None,
        Query(description="Restrict the result to one kind of facility."),
    ] = None,
    facility_status: Annotated[
        StatusEnum | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[FacilityRead]:
    """List facilities, oldest first.

    Args:
        service: The facility service for this request.
        params: The requested collection window.
        site_id: Optional ``site_id`` filter from the query string.
        facility_type: Optional ``facility_type`` filter from the query string.
        facility_status: Optional ``status`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    facilities, total = await service.list_facilities(
        params,
        site_id=site_id,
        facility_type=facility_type,
        status=facility_status,
    )
    return Page[FacilityRead](
        items=[FacilityRead.model_validate(facility) for facility in facilities],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{facility_id}", response_model=FacilityRead)
async def get_facility(facility_id: UUID, service: FacilityServiceDep) -> FacilityRead:
    """Read one facility, archived ones included.

    Args:
        facility_id: Identifier of the facility to read.
        service: The facility service for this request.

    Returns:
        The requested facility.
    """
    facility: Facility = await service.get_facility(facility_id)
    return FacilityRead.model_validate(facility)


@router.patch("/{facility_id}", response_model=FacilityRead)
async def update_facility(
    facility_id: UUID,
    payload: FacilityUpdate,
    service: FacilityServiceDep,
) -> FacilityRead:
    """Update a facility's name, type or status.

    Args:
        facility_id: Identifier of the facility to update.
        payload: The fields to change.
        service: The facility service for this request.

    Returns:
        The updated facility.
    """
    facility: Facility = await service.update_facility(facility_id, payload)
    return FacilityRead.model_validate(facility)


@router.get("/{facility_id}/configuration", response_model=FacilityConfigurationRead)
async def get_facility_configuration(
    facility_id: UUID,
    service: ConfigurationServiceDep,
    include_archived: Annotated[
        bool,
        Query(description="Include archived zones and points in the document."),
    ] = False,
) -> FacilityConfigurationRead:
    """Read a facility's whole configuration in one request.

    The facility, its site, its zones with their composition and its points with
    their last known state, assembled from a fixed number of queries.

    Args:
        facility_id: Identifier of the facility to describe.
        service: The configuration read service for this request.
        include_archived: Whether archived zones and points are kept in the
            document; they are left out by default.

    Returns:
        The configuration document.
    """
    return await service.get_configuration(facility_id, include_archived=include_archived)
