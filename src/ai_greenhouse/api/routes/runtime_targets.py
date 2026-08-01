"""Read-only HTTP access to runtime targets.

There is deliberately no ``POST``, ``PATCH`` or ``DELETE``. A runtime target is
what activating a grow cycle produced, so offering a client a way to create or
edit one would put a second author on the band a zone is grown against and make
"the target is what the recipe said" untrue.

Like command and telemetry history, and unlike the paged collections, the list
carries no total: it is a bounded newest-first window over a table that only
grows.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.cultivation.models import RuntimeTarget
from ai_greenhouse.cultivation.schemas import RuntimeTargetListRead, RuntimeTargetRead
from ai_greenhouse.cultivation.service import RuntimeTargetService

DEFAULT_RUNTIME_TARGET_LIMIT: int = 100
MIN_RUNTIME_TARGET_LIMIT: int = 1
MAX_RUNTIME_TARGET_LIMIT: int = 1000

router: APIRouter = APIRouter(prefix="/runtime-targets", tags=["grow cycles"])


async def get_runtime_target_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RuntimeTargetService:
    """Build the runtime-target service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``RuntimeTargetService`` sharing the request's transaction.
    """
    return RuntimeTargetService(session)


RuntimeTargetServiceDep = Annotated[RuntimeTargetService, Depends(get_runtime_target_service)]


@router.get("", response_model=RuntimeTargetListRead)
async def list_runtime_targets(
    service: RuntimeTargetServiceDep,
    control_loop_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the targets of one control loop."),
    ] = None,
    active: Annotated[
        bool | None,
        Query(
            description=(
                "``true`` returns only targets that are still in effect, "
                "``false`` only closed history."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=MIN_RUNTIME_TARGET_LIMIT,
            le=MAX_RUNTIME_TARGET_LIMIT,
            description="Maximum number of runtime targets to return.",
        ),
    ] = DEFAULT_RUNTIME_TARGET_LIMIT,
) -> RuntimeTargetListRead:
    """Return a bounded, deterministic newest-first window of runtime targets.

    Args:
        service: The runtime-target service for this request.
        control_loop_id: Restricts the result to one loop when given.
        active: Restricts the result to open or to closed targets when given.
        limit: Maximum number of targets to return.

    Returns:
        The matching targets in ``created_at DESC, id DESC`` order.
    """
    targets: list[RuntimeTarget] = await service.list_targets(
        control_loop_id=control_loop_id,
        active=active,
        limit=limit,
    )
    return RuntimeTargetListRead(
        items=[RuntimeTargetRead.model_validate(target) for target in targets]
    )


@router.get("/{runtime_target_id}", response_model=RuntimeTargetRead)
async def get_runtime_target(
    runtime_target_id: UUID,
    service: RuntimeTargetServiceDep,
) -> RuntimeTargetRead:
    """Read one runtime target and the source it was snapshotted from.

    Args:
        runtime_target_id: The target to read.
        service: The runtime-target service for this request.

    Returns:
        The stored target.
    """
    target: RuntimeTarget = await service.get_target(runtime_target_id)
    return RuntimeTargetRead.model_validate(target)
