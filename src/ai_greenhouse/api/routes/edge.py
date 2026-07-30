"""The three public Cloud ↔ Edge v1.0 HTTP operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.edge.schemas import (
    EdgeCommandAcknowledgement,
    EdgeCommandList,
    EdgeErrorResponse,
    EdgeTelemetryEnvelope,
    EdgeTelemetryIngestionResult,
)
from ai_greenhouse.edge.service import EdgeService

router = APIRouter(prefix="/edge", tags=["edge"])

ERROR_RESPONSES = {
    403: {"model": EdgeErrorResponse},
    404: {"model": EdgeErrorResponse},
    409: {"model": EdgeErrorResponse},
    422: {"model": EdgeErrorResponse},
}


async def get_edge_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EdgeService:
    """Build the Edge adapter on the request-scoped transaction."""
    return EdgeService(session)


EdgeServiceDep = Annotated[EdgeService, Depends(get_edge_service)]


@router.post(
    "/telemetry",
    response_model=EdgeTelemetryIngestionResult,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
async def ingest_telemetry(
    payload: EdgeTelemetryEnvelope,
    service: EdgeServiceDep,
) -> EdgeTelemetryIngestionResult:
    """Ingest one fully atomic telemetry envelope."""
    return await service.ingest(payload)


@router.get(
    "/gateways/{gateway_id}/commands",
    response_model=EdgeCommandList,
    responses=ERROR_RESPONSES,
)
async def list_pending_commands(
    gateway_id: UUID,
    service: EdgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> EdgeCommandList:
    """Poll pending commands scoped to one gateway."""
    return await service.list_commands(gateway_id, limit=limit)


@router.put(
    "/gateways/{gateway_id}/commands/{command_id}/acknowledgement",
    response_model=EdgeCommandAcknowledgement,
    response_model_exclude_none=True,
    responses=ERROR_RESPONSES,
)
async def acknowledge_command(
    gateway_id: UUID,
    command_id: UUID,
    payload: EdgeCommandAcknowledgement,
    service: EdgeServiceDep,
) -> EdgeCommandAcknowledgement:
    """Store or replay a terminal command acknowledgement."""
    return await service.acknowledge(gateway_id, command_id, payload)
