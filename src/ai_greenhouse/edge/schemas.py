"""Exact v1.0 public Cloud ↔ Edge request and response representations."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from ai_greenhouse.control.models import CommandState
from ai_greenhouse.control.schemas import CommandRejectionReason
from ai_greenhouse.points.models import PointDataType
from ai_greenhouse.telemetry.schemas import TelemetryWriteOutcome

CONTRACT_VERSION = "1.0"
ContractVersion = Literal["1.0"]
SourceId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


def omit_schema_default(schema: dict[str, Any]) -> None:
    """Describe an optional closed-contract field without a JSON null default."""
    schema.pop("default", None)


class EdgeSourceKind(StrEnum):
    """Kinds of producer represented by contract v1."""

    SENSOR = "sensor"
    ACTUATOR = "actuator"
    CONTROLLER = "controller"
    OPERATOR = "operator"
    DERIVED = "derived"


class EdgeTelemetryQuality(StrEnum):
    """Submitted qualities; ``no_data`` is deliberately absent."""

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"
    OUT_OF_RANGE = "out_of_range"
    SENSOR_FAULT = "sensor_fault"
    SIMULATED = "simulated"
    MANUALLY_ENTERED = "manually_entered"


class EdgeTelemetrySource(BaseModel):
    """Generic provenance metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EdgeSourceKind
    id: SourceId


def value_matches(data_type: PointDataType, value: Any) -> bool:
    """Apply the contract's exact JSON value typing rules."""
    match data_type:
        case PointDataType.FLOAT:
            return isinstance(value, int | float) and not isinstance(value, bool)
        case PointDataType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        case PointDataType.BOOLEAN:
            return isinstance(value, bool)
        case PointDataType.STRING:
            return isinstance(value, str)


class EdgeTelemetryMessage(BaseModel):
    """One producer-owned telemetry message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    point_id: UUID
    data_type: PointDataType
    value: Any
    observed_at: AwareDatetime
    quality: EdgeTelemetryQuality
    source: EdgeTelemetrySource

    @model_validator(mode="after")
    def check_value_type(self) -> Self:
        """Reject a value that contradicts the explicit data type."""
        if not value_matches(self.data_type, self.value):
            raise ValueError("value does not match data_type")
        return self


class EdgeTelemetryEnvelope(BaseModel):
    """One to 100 messages submitted by one gateway."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: ContractVersion
    gateway_id: UUID
    batch_id: UUID | SkipJsonSchema[None] = Field(
        default=None,
        json_schema_extra=omit_schema_default,
    )
    messages: Annotated[list[EdgeTelemetryMessage], Field(min_length=1, max_length=100)]

    @model_validator(mode="before")
    @classmethod
    def reject_null_batch_id(cls, value: Any) -> Any:
        """The closed contract permits an absent batch ID, never JSON null."""
        if isinstance(value, dict) and "batch_id" in value and value["batch_id"] is None:
            raise ValueError("batch_id must be a UUID when provided")
        return value


class EdgeTelemetryResultItem(BaseModel):
    """Stored identity and outcome of one submitted message."""

    model_config = ConfigDict(frozen=True)

    message_id: UUID
    sample_id: UUID
    outcome: TelemetryWriteOutcome
    received_at: datetime


class EdgeTelemetryIngestionResult(BaseModel):
    """Ordered result for one telemetry envelope."""

    model_config = ConfigDict(frozen=True)

    contract_version: ContractVersion = CONTRACT_VERSION
    gateway_id: UUID
    batch_id: UUID | SkipJsonSchema[None] = Field(
        default=None,
        json_schema_extra=omit_schema_default,
    )
    results: list[EdgeTelemetryResultItem]


class EdgePendingCommand(BaseModel):
    """One pending command in the exact public representation."""

    model_config = ConfigDict(frozen=True)

    command_id: UUID
    point_id: UUID
    reported_point_id: UUID
    data_type: PointDataType
    desired_value: Any
    issued_at: datetime
    state: Literal[CommandState.PENDING] = CommandState.PENDING


class EdgeCommandList(BaseModel):
    """Pending commands visible to one gateway."""

    model_config = ConfigDict(frozen=True)

    contract_version: ContractVersion = CONTRACT_VERSION
    gateway_id: UUID
    commands: list[EdgePendingCommand]


class EdgeCommandOutcome(StrEnum):
    """Terminal command outcomes."""

    APPLIED = "applied"
    REJECTED = "rejected"


EdgeRejectionReason = CommandRejectionReason
"""The v1 rejection reason is the domain's one rejection representation.

Aliased rather than redeclared: the gateway writes the code and message, the
public command read model returns them, and a second class here would let the
two published shapes drift apart while both claimed to describe one rejection.
"""


class EdgeCommandAcknowledgement(BaseModel):
    """Terminal acknowledgement resource identified by gateway and command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: ContractVersion
    gateway_id: UUID
    command_id: UUID
    outcome: EdgeCommandOutcome
    acknowledged_at: AwareDatetime
    reason: EdgeRejectionReason | SkipJsonSchema[None] = Field(
        default=None,
        json_schema_extra=omit_schema_default,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_reason(cls, value: Any) -> Any:
        """An optional reason is absent or an object, never JSON null."""
        if isinstance(value, dict) and "reason" in value and value["reason"] is None:
            raise ValueError("reason must be an object when provided")
        return value

    @model_validator(mode="after")
    def check_reason(self) -> Self:
        """Require a reason only for rejection."""
        if self.outcome is EdgeCommandOutcome.REJECTED and self.reason is None:
            raise ValueError("reason is required when outcome is rejected")
        if self.outcome is EdgeCommandOutcome.APPLIED and self.reason is not None:
            raise ValueError("reason is not allowed when outcome is applied")
        return self


class EdgeErrorDetail(BaseModel):
    """Safe field-level validation detail."""

    location: list[str | int]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class EdgeErrorValue(BaseModel):
    """Public Edge error value."""

    code: Literal[
        "gateway_not_found",
        "gateway_inactive",
        "point_not_found",
        "point_inactive",
        "gateway_point_forbidden",
        "telemetry_message_conflict",
        "command_not_found",
        "command_not_pending",
        "command_acknowledgement_conflict",
        "validation_error",
    ]
    message: Annotated[str, Field(min_length=1, max_length=500)]
    details: list[EdgeErrorDetail] | SkipJsonSchema[None] = Field(
        default=None,
        json_schema_extra=omit_schema_default,
    )


class EdgeErrorResponse(BaseModel):
    """Error envelope published by contract v1."""

    contract_version: ContractVersion = CONTRACT_VERSION
    error: EdgeErrorValue
