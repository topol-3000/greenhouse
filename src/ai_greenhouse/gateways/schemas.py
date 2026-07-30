"""Transport schemas for administrative gateway provisioning."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_greenhouse.core.types import CodeStr
from ai_greenhouse.infrastructure.database.base import StatusEnum


class GatewayCreate(BaseModel):
    """Configuration accepted by the idempotent gateway provisioning operation."""

    model_config = ConfigDict(extra="forbid")

    code: CodeStr
    site_id: UUID


class GatewayRead(BaseModel):
    """Administrative representation of one gateway resource."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    code: str
    site_id: UUID
    status: StatusEnum
    created_at: datetime
    updated_at: datetime


class GatewayConfigurationRead(BaseModel):
    """Normalized fields that define a gateway's functional configuration."""

    model_config = ConfigDict(frozen=True)

    gateway_id: UUID
    code: str
    site_id: UUID


class GatewayProvisioningOutcome(StrEnum):
    """Whether an idempotent provisioning request created or resolved a gateway."""

    CREATED = "created"
    EXISTING = "existing"


class GatewayProvisioningRead(BaseModel):
    """Result of create-or-resolve gateway provisioning."""

    model_config = ConfigDict(frozen=True)

    outcome: GatewayProvisioningOutcome
    gateway: GatewayRead
    configuration: GatewayConfigurationRead


class GatewayPointAuthorizationRequest(BaseModel):
    """Points to add to a gateway's authorization set."""

    model_config = ConfigDict(extra="forbid")

    point_ids: list[UUID] = Field(min_length=1, max_length=1000)


class GatewayPointAuthorizationOutcome(StrEnum):
    """Effect of one requested point authorization."""

    AUTHORIZED = "authorized"
    ALREADY_AUTHORIZED = "already_authorized"


class GatewayPointAuthorizationRead(BaseModel):
    """Outcome for one distinct point in an additive authorization request."""

    model_config = ConfigDict(frozen=True)

    point_id: UUID
    outcome: GatewayPointAuthorizationOutcome


class GatewayPointAuthorizationResult(BaseModel):
    """Result of applying an additive point-authorization request."""

    model_config = ConfigDict(frozen=True)

    gateway_id: UUID
    points: list[GatewayPointAuthorizationRead]


class GatewayAuthorizedPointsRead(BaseModel):
    """Normalized current point authorization for one gateway."""

    model_config = ConfigDict(frozen=True)

    gateway_id: UUID
    point_ids: list[UUID]


class GatewayErrorValue(BaseModel):
    """One stable error in the common API envelope."""

    code: str
    message: str
    details: dict[str, Any]


class GatewayErrorResponse(BaseModel):
    """Documented management-plane error response."""

    error: GatewayErrorValue
