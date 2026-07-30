"""Administrative gateway management-plane HTTP operations.

These routes provision identities and point authorization. They are separate
from the closed Cloud ↔ Edge v1 data-plane routes under ``/edge`` and are kept
as an explicit module boundary for future administrative authentication.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.core.types import CodeStr
from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.schemas import (
    GatewayAuthorizedPointsRead,
    GatewayConfigurationRead,
    GatewayCreate,
    GatewayErrorResponse,
    GatewayPointAuthorizationOutcome,
    GatewayPointAuthorizationRead,
    GatewayPointAuthorizationRequest,
    GatewayPointAuthorizationResult,
    GatewayProvisioningOutcome,
    GatewayProvisioningRead,
    GatewayRead,
)
from ai_greenhouse.gateways.service import GatewayConfigurationService

router = APIRouter(prefix="/gateways", tags=["gateway-management"])

ERROR_RESPONSES = {
    404: {"model": GatewayErrorResponse},
    409: {"model": GatewayErrorResponse},
    422: {"model": GatewayErrorResponse},
}


async def get_gateway_configuration_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GatewayConfigurationService:
    """Build the application service on the request-scoped transaction."""
    return GatewayConfigurationService(session)


GatewayConfigurationServiceDep = Annotated[
    GatewayConfigurationService,
    Depends(get_gateway_configuration_service),
]


def gateway_configuration(gateway: Gateway) -> GatewayConfigurationRead:
    """Build the normalized functional configuration representation."""
    return GatewayConfigurationRead(
        gateway_id=gateway.id,
        code=gateway.code,
        site_id=gateway.site_id,
    )


@router.post(
    "",
    response_model=GatewayProvisioningRead,
    responses={
        status.HTTP_201_CREATED: {"model": GatewayProvisioningRead},
        **ERROR_RESPONSES,
    },
)
async def provision_gateway(
    payload: GatewayCreate,
    response: Response,
    service: GatewayConfigurationServiceDep,
) -> GatewayProvisioningRead:
    """Create a gateway or resolve an equivalent request by stable code."""
    gateway, created = await service.resolve_or_create(payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return GatewayProvisioningRead(
        outcome=(
            GatewayProvisioningOutcome.CREATED if created else GatewayProvisioningOutcome.EXISTING
        ),
        gateway=GatewayRead.model_validate(gateway),
        configuration=gateway_configuration(gateway),
    )


@router.get(
    "/by-code/{code}",
    response_model=GatewayRead,
    responses=ERROR_RESPONSES,
)
async def get_gateway_by_code(
    code: CodeStr,
    service: GatewayConfigurationServiceDep,
) -> GatewayRead:
    """Resolve a gateway by its stable provisioning code."""
    return GatewayRead.model_validate(await service.get_gateway_by_code(code))


@router.get(
    "/{gateway_id}",
    response_model=GatewayRead,
    responses=ERROR_RESPONSES,
)
async def get_gateway(
    gateway_id: UUID,
    service: GatewayConfigurationServiceDep,
) -> GatewayRead:
    """Read a gateway by the UUID used by the Edge data plane."""
    return GatewayRead.model_validate(await service.get_gateway(gateway_id))


@router.get(
    "/{gateway_id}/configuration",
    response_model=GatewayConfigurationRead,
    responses=ERROR_RESPONSES,
)
async def get_gateway_configuration(
    gateway_id: UUID,
    service: GatewayConfigurationServiceDep,
) -> GatewayConfigurationRead:
    """Read the gateway's normalized functional configuration."""
    return gateway_configuration(await service.get_gateway(gateway_id))


@router.get(
    "/{gateway_id}/points",
    response_model=GatewayAuthorizedPointsRead,
    responses=ERROR_RESPONSES,
)
async def get_gateway_points(
    gateway_id: UUID,
    service: GatewayConfigurationServiceDep,
) -> GatewayAuthorizedPointsRead:
    """Read the gateway's complete current logical-point authorization."""
    return GatewayAuthorizedPointsRead(
        gateway_id=gateway_id,
        point_ids=await service.list_authorized_points(gateway_id),
    )


@router.post(
    "/{gateway_id}/points",
    response_model=GatewayPointAuthorizationResult,
    responses=ERROR_RESPONSES,
)
async def authorize_gateway_points(
    gateway_id: UUID,
    payload: GatewayPointAuthorizationRequest,
    service: GatewayConfigurationServiceDep,
) -> GatewayPointAuthorizationResult:
    """Add logical points without removing any existing authorization."""
    results = await service.authorize_points(gateway_id, payload.point_ids)
    return GatewayPointAuthorizationResult(
        gateway_id=gateway_id,
        points=[
            GatewayPointAuthorizationRead(
                point_id=result.point_id,
                outcome=(
                    GatewayPointAuthorizationOutcome.AUTHORIZED
                    if result.newly_authorized
                    else GatewayPointAuthorizationOutcome.ALREADY_AUTHORIZED
                ),
            )
            for result in results
        ],
    )
