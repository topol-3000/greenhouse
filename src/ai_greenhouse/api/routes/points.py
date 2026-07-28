"""HTTP endpoints for points and their current state.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.points`.

There is no ``DELETE``: a point is retired with
``PATCH {"status": "archived"}``. There is also **no endpoint that writes a
point's value**. ``GET /points/{point_id}/state`` is read-only, and stays that
way until telemetry arrives in Milestone 2.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.core.types import CodeStr
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point, PointCurrentState, PointKind
from ai_greenhouse.points.schemas import PointCreate, PointRead, PointStateRead, PointUpdate
from ai_greenhouse.points.service import PointService

router: APIRouter = APIRouter(prefix="/points", tags=["points"])


async def get_point_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PointService:
    """Build the point service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``PointService`` sharing the request's transaction.
    """
    return PointService(session)


PointServiceDep = Annotated[PointService, Depends(get_point_service)]


@router.post("", response_model=PointRead, status_code=status.HTTP_201_CREATED)
async def create_point(payload: PointCreate, service: PointServiceDep) -> PointRead:
    """Register a point and its empty state projection.

    Args:
        payload: The point to create.
        service: The point service for this request.

    Returns:
        The created point, with HTTP 201.
    """
    point: Point = await service.create_point(payload)
    return PointRead.model_validate(point)


@router.get("", response_model=Page[PointRead])
async def list_points(
    service: PointServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    site_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the points of one site."),
    ] = None,
    facility_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the points of one facility."),
    ] = None,
    point_kind: Annotated[
        PointKind | None,
        Query(description="Restrict the result to one role of point."),
    ] = None,
    metric_type: Annotated[
        CodeStr | None,
        Query(description="Restrict the result to one measured quantity."),
    ] = None,
    point_status: Annotated[
        StatusEnum | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[PointRead]:
    """List points, oldest first.

    Args:
        service: The point service for this request.
        params: The requested collection window.
        site_id: Optional ``site_id`` filter from the query string.
        facility_id: Optional ``facility_id`` filter from the query string.
        point_kind: Optional ``point_kind`` filter from the query string.
        metric_type: Optional ``metric_type`` filter from the query string.
        point_status: Optional ``status`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    points, total = await service.list_points(
        params,
        site_id=site_id,
        facility_id=facility_id,
        point_kind=point_kind,
        metric_type=metric_type,
        status=point_status,
    )
    return Page[PointRead](
        items=[PointRead.model_validate(point) for point in points],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{point_id}", response_model=PointRead)
async def get_point(point_id: UUID, service: PointServiceDep) -> PointRead:
    """Read one point, archived ones included.

    Args:
        point_id: Identifier of the point to read.
        service: The point service for this request.

    Returns:
        The requested point.
    """
    point: Point = await service.get_point(point_id)
    return PointRead.model_validate(point)


@router.patch("/{point_id}", response_model=PointRead)
async def update_point(
    point_id: UUID,
    payload: PointUpdate,
    service: PointServiceDep,
) -> PointRead:
    """Update a point's name, unit, range or status.

    Args:
        point_id: Identifier of the point to update.
        payload: The fields to change.
        service: The point service for this request.

    Returns:
        The updated point.
    """
    point: Point = await service.update_point(point_id, payload)
    return PointRead.model_validate(point)


@router.get("/{point_id}/state", response_model=PointStateRead)
async def get_point_state(point_id: UUID, service: PointServiceDep) -> PointStateRead:
    """Read the last known state of one point.

    Throughout Milestone 1 the answer is the empty projection created with the
    point: no value, and ``quality = "no_data"``.

    Args:
        point_id: Identifier of the point whose state to read.
        service: The point service for this request.

    Returns:
        The point's current-state projection.
    """
    state: PointCurrentState = await service.get_point_state(point_id)
    return PointStateRead.model_validate(state)
