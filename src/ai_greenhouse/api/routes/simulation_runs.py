"""Create and read persisted simulation runs."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams
from ai_greenhouse.simulation.models import SimulationRun, SimulationStatus
from ai_greenhouse.simulation.runtime import SimulationRuntime
from ai_greenhouse.simulation.schemas import SimulationRunCreate, SimulationRunRead
from ai_greenhouse.simulation.service import SimulationRunService

router: APIRouter = APIRouter(prefix="/simulation-runs", tags=["simulation"])


async def get_simulation_run_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SimulationRunService:
    """Build the simulation service on the request-scoped session."""
    return SimulationRunService(session)


SimulationRunServiceDep = Annotated[
    SimulationRunService,
    Depends(get_simulation_run_service),
]


async def get_simulation_runtime(request: Request) -> SimulationRuntime:
    """Return the application-scoped task registry."""
    return cast(SimulationRuntime, request.app.state.simulation_runtime)


SimulationRuntimeDep = Annotated[
    SimulationRuntime,
    Depends(get_simulation_runtime),
]


@router.post("", response_model=SimulationRunRead, status_code=status.HTTP_201_CREATED)
async def create_simulation_run(
    payload: SimulationRunCreate,
    service: SimulationRunServiceDep,
) -> SimulationRunRead:
    """Create a configured run without starting it."""
    run = await service.create_run(payload)
    return SimulationRunRead.model_validate(run)


@router.get("", response_model=Page[SimulationRunRead])
async def list_simulation_runs(
    service: SimulationRunServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    control_zone_id: UUID | None = None,
    run_status: Annotated[
        SimulationStatus | None,
        Query(alias="status", description="Restrict the result to one lifecycle state."),
    ] = None,
) -> Page[SimulationRunRead]:
    """List runs newest first with optional zone and status filters."""
    runs, total = await service.list_runs(
        params,
        control_zone_id=control_zone_id,
        status=run_status,
    )
    return Page[SimulationRunRead](
        items=[SimulationRunRead.model_validate(run) for run in runs],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{run_id}", response_model=SimulationRunRead)
async def get_simulation_run(
    run_id: UUID,
    service: SimulationRunServiceDep,
) -> SimulationRunRead:
    """Read one simulation run."""
    run: SimulationRun = await service.get_run(run_id)
    return SimulationRunRead.model_validate(run)


@router.post(
    "/{run_id}/start",
    response_model=SimulationRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_simulation_run(
    run_id: UUID,
    runtime: SimulationRuntimeDep,
) -> SimulationRunRead:
    """Start a run after committing its immediate initial step."""
    run = await runtime.start(run_id)
    return SimulationRunRead.model_validate(run)


@router.post("/{run_id}/stop", response_model=SimulationRunRead)
async def stop_simulation_run(
    run_id: UUID,
    runtime: SimulationRuntimeDep,
) -> SimulationRunRead:
    """Stop a run after its background task has ended."""
    run = await runtime.stop(run_id)
    return SimulationRunRead.model_validate(run)
