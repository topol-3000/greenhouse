"""HTTP endpoints for control-loop configuration.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.control`.

There is no ``PATCH`` and no ``DELETE``. A loop is the configuration a command
was decided under, so it is created once and read afterwards.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.control.models import ControlLoop
from ai_greenhouse.control.schemas import ControlLoopCreate, ControlLoopRead
from ai_greenhouse.control.service import ControlLoopService

router: APIRouter = APIRouter(prefix="/control-loops", tags=["control"])


async def get_control_loop_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ControlLoopService:
    """Build the control-loop service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``ControlLoopService`` sharing the request's transaction.
    """
    return ControlLoopService(session)


ControlLoopServiceDep = Annotated[ControlLoopService, Depends(get_control_loop_service)]


@router.post("", response_model=ControlLoopRead, status_code=status.HTTP_201_CREATED)
async def create_control_loop(
    payload: ControlLoopCreate,
    service: ControlLoopServiceDep,
) -> ControlLoopRead:
    """Configure the one hysteresis loop of a climate zone.

    Args:
        payload: The loop to configure.
        service: The control-loop service for this request.

    Returns:
        The created loop, with HTTP 201.
    """
    loop: ControlLoop = await service.create_loop(payload)
    return ControlLoopRead.model_validate(loop)


@router.get("", response_model=Page[ControlLoopRead])
async def list_control_loops(
    service: ControlLoopServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    control_zone_id: UUID | None = None,
) -> Page[ControlLoopRead]:
    """List configured loops, optionally restricted to one zone.

    Args:
        service: The control-loop service for this request.
        params: The requested collection window.
        control_zone_id: Restricts the result to one zone when given.

    Returns:
        One page of loops in the shared collection envelope.
    """
    loops, total = await service.list_loops(params, control_zone_id=control_zone_id)
    return Page[ControlLoopRead](
        items=[ControlLoopRead.model_validate(loop) for loop in loops],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{control_loop_id}", response_model=ControlLoopRead)
async def get_control_loop(
    control_loop_id: UUID,
    service: ControlLoopServiceDep,
) -> ControlLoopRead:
    """Read one control loop.

    Args:
        control_loop_id: The loop to read.
        service: The control-loop service for this request.

    Returns:
        The stored loop.
    """
    loop: ControlLoop = await service.get_loop(control_loop_id)
    return ControlLoopRead.model_validate(loop)
