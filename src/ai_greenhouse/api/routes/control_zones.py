"""HTTP endpoints for control zones.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.topology`.

A zone itself has no ``DELETE``: it is retired with
``PATCH {"status": "archived"}``. Its point assignments do, and they are the
only resource in the API that does — an assignment is a link, not a record with
a history, so there is nothing about it to archive.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.models import ControlZone, ZonePointAssignment, ZoneType
from ai_greenhouse.topology.schemas import (
    ControlZoneCreate,
    ControlZoneRead,
    ControlZoneUpdate,
    ZonePointAssignmentCreate,
    ZonePointAssignmentRead,
)
from ai_greenhouse.topology.service import ControlZoneService, ZonePointAssignmentService

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


async def get_assignment_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ZonePointAssignmentService:
    """Build the zone-point assignment service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``ZonePointAssignmentService`` sharing the request's transaction.
    """
    return ZonePointAssignmentService(session)


AssignmentServiceDep = Annotated[ZonePointAssignmentService, Depends(get_assignment_service)]


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


@router.post(
    "/{zone_id}/points",
    response_model=ZonePointAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_zone_point_assignment(
    zone_id: UUID,
    payload: ZonePointAssignmentCreate,
    service: AssignmentServiceDep,
) -> ZonePointAssignmentRead:
    """Assign a point to a control zone under one role.

    Args:
        zone_id: Identifier of the zone to assign the point to.
        payload: The point and the role it plays in the zone.
        service: The assignment service for this request.

    Returns:
        The created assignment enriched with the point's metadata, with
        HTTP 201.
    """
    assignment, point = await service.create_assignment(zone_id, payload)
    return ZonePointAssignmentRead.from_assignment(assignment, point)


@router.get("/{zone_id}/points", response_model=Page[ZonePointAssignmentRead])
async def list_zone_point_assignments(
    zone_id: UUID,
    service: AssignmentServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
) -> Page[ZonePointAssignmentRead]:
    """List a control zone's composition, oldest assignment first.

    Args:
        zone_id: Identifier of the zone whose composition to read.
        service: The assignment service for this request.
        params: The requested collection window.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    rows: list[Row[tuple[ZonePointAssignment, Point]]]
    rows, total = await service.list_assignments(zone_id, params)
    return Page[ZonePointAssignmentRead](
        items=[
            ZonePointAssignmentRead.from_assignment(assignment, point) for assignment, point in rows
        ],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.delete("/{zone_id}/points/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone_point_assignment(
    zone_id: UUID,
    assignment_id: UUID,
    service: AssignmentServiceDep,
) -> Response:
    """Remove one point from a control zone's composition.

    The point itself is untouched; only the link is removed. This is the one
    real delete in the domain, and it is allowed precisely because an
    assignment carries no history worth keeping.

    Args:
        zone_id: Identifier of the zone to remove the point from.
        assignment_id: Identifier of the assignment to remove.
        service: The assignment service for this request.

    Returns:
        An empty response with HTTP 204.
    """
    await service.delete_assignment(zone_id, assignment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
