"""Read-only HTTP access to one point's telemetry history.

The route owns HTTP parsing and serialisation only. It deliberately does not
reuse the generic page helper: history reads have no total count, offset, page
number or next-page metadata.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.telemetry.models import TelemetrySample
from ai_greenhouse.telemetry.schemas import TelemetryHistoryRead, TelemetrySampleRead
from ai_greenhouse.telemetry.service import TelemetryService

DEFAULT_TELEMETRY_LIMIT: int = 100
MIN_TELEMETRY_LIMIT: int = 1
MAX_TELEMETRY_LIMIT: int = 1000

router: APIRouter = APIRouter(prefix="/points", tags=["telemetry"])


async def get_telemetry_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TelemetryService:
    """Build the telemetry service on the request-scoped session."""
    return TelemetryService(session)


TelemetryServiceDep = Annotated[TelemetryService, Depends(get_telemetry_service)]


@router.get("/{point_id}/telemetry", response_model=TelemetryHistoryRead)
async def list_point_telemetry(
    point_id: UUID,
    service: TelemetryServiceDep,
    observed_from: Annotated[
        AwareDatetime | None,
        Query(alias="from", description="Inclusive lower bound on observed_at."),
    ] = None,
    observed_to: Annotated[
        AwareDatetime | None,
        Query(alias="to", description="Inclusive upper bound on observed_at."),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=MIN_TELEMETRY_LIMIT,
            le=MAX_TELEMETRY_LIMIT,
            description="Maximum number of samples to return.",
        ),
    ] = DEFAULT_TELEMETRY_LIMIT,
) -> TelemetryHistoryRead:
    """Return a bounded, deterministic history for one logical point."""
    samples: list[TelemetrySample] = await service.list_history(
        point_id,
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
    )
    return TelemetryHistoryRead(
        items=[TelemetrySampleRead.model_validate(sample) for sample in samples]
    )
