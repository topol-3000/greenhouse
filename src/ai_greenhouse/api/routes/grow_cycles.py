"""HTTP endpoints for grow cycles.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.cultivation`.

There is no ``PATCH`` and no ``DELETE``. Everything a cycle allows after
creation is a lifecycle transition, and each one is its own action with its own
guarantees — a generic update would let a client set ``status`` to ``active``
without the stage instance and runtime target that make it true.

Each transition is idempotent when it is repeated on a cycle that already made
it: retrying a request that timed out answers with the current representation
instead of refusing it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.cultivation.models import GrowCycleStatus
from ai_greenhouse.cultivation.schemas import (
    GrowCycleCodeStr,
    GrowCycleCreate,
    GrowCycleRead,
)
from ai_greenhouse.cultivation.service import GrowCycleService

router: APIRouter = APIRouter(prefix="/grow-cycles", tags=["grow cycles"])


async def get_grow_cycle_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GrowCycleService:
    """Build the grow cycle service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``GrowCycleService`` sharing the request's transaction.
    """
    return GrowCycleService(session)


GrowCycleServiceDep = Annotated[GrowCycleService, Depends(get_grow_cycle_service)]


@router.post("", response_model=GrowCycleRead, status_code=status.HTTP_201_CREATED)
async def create_grow_cycle(
    payload: GrowCycleCreate,
    service: GrowCycleServiceDep,
) -> GrowCycleRead:
    """Plan a cycle and place it in exactly one climate zone.

    The cycle and its assignment are written in one transaction: a rejected
    placement leaves no cycle behind. Nothing operational is created — no stage
    instance, no runtime target, no command.

    Args:
        payload: The cycle to plan.
        service: The grow cycle service for this request.

    Returns:
        The created cycle, with HTTP 201.
    """
    return await service.create_cycle(payload)


@router.get("", response_model=Page[GrowCycleRead])
async def list_grow_cycles(
    service: GrowCycleServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    code: Annotated[
        GrowCycleCodeStr | None,
        Query(description="Restrict the result to the cycle with this exact code."),
    ] = None,
    facility_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the cycles of one facility."),
    ] = None,
    cycle_status: Annotated[
        GrowCycleStatus | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[GrowCycleRead]:
    """List cycles with their zone, current stage and active target, oldest first.

    Args:
        service: The grow cycle service for this request.
        params: The requested collection window.
        code: Optional ``code`` filter from the query string.
        facility_id: Optional ``facility_id`` filter from the query string.
        cycle_status: Optional ``status`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    cycles, total = await service.list_cycles(
        params,
        code=code,
        facility_id=facility_id,
        status=cycle_status,
    )
    return Page[GrowCycleRead](
        items=cycles,
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{grow_cycle_id}", response_model=GrowCycleRead)
async def get_grow_cycle(
    grow_cycle_id: UUID,
    service: GrowCycleServiceDep,
) -> GrowCycleRead:
    """Read one cycle and everything needed to understand its current state.

    Args:
        grow_cycle_id: Identifier of the cycle to read.
        service: The grow cycle service for this request.

    Returns:
        The complete current representation.
    """
    return await service.get_cycle(grow_cycle_id)


@router.post("/{grow_cycle_id}/activate", response_model=GrowCycleRead)
async def activate_grow_cycle(
    grow_cycle_id: UUID,
    service: GrowCycleServiceDep,
) -> GrowCycleRead:
    """Start a planned cycle, its stage instance and its runtime target.

    The action takes no body. There is nothing about a running cycle a caller
    decides at this moment: the stage comes from the recipe version, the band
    comes from that stage, and the instant comes from the server.

    Args:
        grow_cycle_id: The cycle to activate.
        service: The grow cycle service for this request.

    Returns:
        The complete representation of the now-active cycle.
    """
    return await service.activate_cycle(grow_cycle_id)


@router.post("/{grow_cycle_id}/complete", response_model=GrowCycleRead)
async def complete_grow_cycle(
    grow_cycle_id: UUID,
    service: GrowCycleServiceDep,
) -> GrowCycleRead:
    """Finish an active cycle, closing its stage instance and runtime target.

    Args:
        grow_cycle_id: The cycle to complete.
        service: The grow cycle service for this request.

    Returns:
        The complete representation of the finished cycle.
    """
    return await service.complete_cycle(grow_cycle_id)


@router.post("/{grow_cycle_id}/abort", response_model=GrowCycleRead)
async def abort_grow_cycle(
    grow_cycle_id: UUID,
    service: GrowCycleServiceDep,
) -> GrowCycleRead:
    """Give up a cycle, whether it was ever activated or not.

    Args:
        grow_cycle_id: The cycle to abort.
        service: The grow cycle service for this request.

    Returns:
        The complete representation of the aborted cycle.
    """
    return await service.abort_cycle(grow_cycle_id)
