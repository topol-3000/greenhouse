"""HTTP endpoints for control zones.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.topology`.

There is no ``DELETE``: a zone is retired with
``PATCH {"status": "archived"}``.

The zone-point assignment endpoints under ``/control-zones/{zone_id}/points``
belong to Milestone 1.6 and are deliberately absent here.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import ControlZone, ZoneType
from ai_greenhouse.topology.schemas import (
    ControlZoneCreate,
    ControlZoneRead,
    ControlZoneUpdate,
)
from ai_greenhouse.topology.service import ControlZoneService

router: APIRouter = APIRouter(prefix="/control-zones", tags=["control-zones"])


async def get_control_zone_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ControlZoneService:
    """Build the control zone service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``ControlZoneService`` sharing the request's transaction.
    """
    return ControlZoneService(session)


ControlZoneServiceDep = Annotated[ControlZoneService, Depends(get_control_zone_service)]


@router.post("", response_model=ControlZoneRead, status_code=status.HTTP_201_CREATED)
async def create_control_zone(
    payload: ControlZoneCreate,
    service: ControlZoneServiceDep,
) -> ControlZoneRead:
    """Register a control zone inside a facility.

    Args:
        payload: The zone to create.
        service: The control zone service for this request.

    Returns:
        The created zone, with HTTP 201.
    """
    control_zone: ControlZone = await service.create_control_zone(payload)
    return ControlZoneRead.model_validate(control_zone)


@router.get("", response_model=Page[ControlZoneRead])
async def list_control_zones(
    service: ControlZoneServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    facility_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the zones of one facility."),
    ] = None,
    zone_type: Annotated[
        ZoneType | None,
        Query(description="Restrict the result to one kind of zone."),
    ] = None,
    zone_status: Annotated[
        StatusEnum | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[ControlZoneRead]:
    """List control zones, oldest first.

    Args:
        service: The control zone service for this request.
        params: The requested collection window.
        facility_id: Optional ``facility_id`` filter from the query string.
        zone_type: Optional ``zone_type`` filter from the query string.
        zone_status: Optional ``status`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    control_zones, total = await service.list_control_zones(
        params,
        facility_id=facility_id,
        zone_type=zone_type,
        status=zone_status,
    )
    return Page[ControlZoneRead](
        items=[ControlZoneRead.model_validate(zone) for zone in control_zones],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{zone_id}", response_model=ControlZoneRead)
async def get_control_zone(zone_id: UUID, service: ControlZoneServiceDep) -> ControlZoneRead:
    """Read one control zone, archived ones included.

    Args:
        zone_id: Identifier of the zone to read.
        service: The control zone service for this request.

    Returns:
        The requested zone.
    """
    control_zone: ControlZone = await service.get_control_zone(zone_id)
    return ControlZoneRead.model_validate(control_zone)


@router.patch("/{zone_id}", response_model=ControlZoneRead)
async def update_control_zone(
    zone_id: UUID,
    payload: ControlZoneUpdate,
    service: ControlZoneServiceDep,
) -> ControlZoneRead:
    """Update a control zone's name, type or status.

    Args:
        zone_id: Identifier of the zone to update.
        payload: The fields to change.
        service: The control zone service for this request.

    Returns:
        The updated zone.
    """
    control_zone: ControlZone = await service.update_control_zone(zone_id, payload)
    return ControlZoneRead.model_validate(control_zone)
