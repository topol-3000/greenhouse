"""Request and response schemas of the control module.

``policy_type`` is not accepted on creation. There is one policy in M3, and a
field a client can only set to a single value is a field that will be set to
something else the moment a second policy exists. It is returned, so a reader
never has to guess which rule a loop evaluates.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from ai_greenhouse.control.models import CommandState, ControlPolicyType

Threshold = Annotated[
    Decimal,
    Field(allow_inf_nan=False),
    PlainSerializer(float, return_type=float, when_used="json"),
]
"""One end of the hysteresis band, in the measurement point's unit.

Held as ``Decimal`` so the value written to the ``numeric`` column is the one
the client sent, and serialised back as a JSON number rather than as the string
Pydantic would otherwise produce. This mirrors ``points`` range bounds, which
describe the same kind of quantity.
"""


class ControlLoopCreate(BaseModel):
    """Body accepted by ``POST /api/v1/control-loops``.

    Only the threshold ordering is checked here: both ends are always present
    in a creation body, so the rule needs nothing the schema cannot see. Every
    other rule needs the referenced zone and points and is applied by the
    service, which reports it with its own error code.
    """

    model_config = ConfigDict(extra="forbid")

    control_zone_id: UUID
    measurement_point_id: UUID
    control_point_id: UUID
    status_point_id: UUID
    lower_threshold: Threshold
    upper_threshold: Threshold

    @model_validator(mode="after")
    def check_band_is_ordered(self) -> Self:
        """Reject a band whose lower end is not below its upper end.

        Equal thresholds are refused as well. A band of zero width has no
        hysteresis left in it: a value would switch the fan on and off around
        one point, which is the oscillation the policy exists to prevent.

        Returns:
            The validated body, unchanged.

        Raises:
            ValueError: If ``lower_threshold`` is not strictly below
                ``upper_threshold``. Pydantic turns this into HTTP 422
                ``validation_error``.
        """
        if self.lower_threshold >= self.upper_threshold:
            raise ValueError("lower_threshold must be less than upper_threshold")
        return self


class ControlLoopRead(BaseModel):
    """Representation returned by every control-loop endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    control_zone_id: UUID
    measurement_point_id: UUID
    control_point_id: UUID
    status_point_id: UUID
    policy_type: ControlPolicyType
    lower_threshold: Threshold
    upper_threshold: Threshold
    created_at: datetime


class CommandRead(BaseModel):
    """One applied command, with every identifier its chain is followed by.

    All three sample identifiers are returned rather than joined into embedded
    samples: the telemetry history endpoint already reads a sample, and
    duplicating it here would give one value two representations that could
    disagree.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    idempotency_key: str
    control_loop_id: UUID
    runtime_target_id: UUID | None
    trigger_sample_id: UUID
    target_point_id: UUID
    reported_point_id: UUID
    gateway_id: UUID | None
    desired_value: bool
    state: CommandState
    result_control_sample_id: UUID | None
    result_status_sample_id: UUID | None
    issued_at: datetime
    executed_at: datetime | None
    acknowledged_at: datetime | None
    rejection_reason: dict[str, str] | None
    created_at: datetime


class CommandListRead(BaseModel):
    """A count-free collection of commands.

    No total, like telemetry history and unlike the paged collections. The
    command list is a bounded newest-first window over an append-only table, and
    a ``COUNT(*)`` over it would grow with the history while answering a
    question nobody asked.
    """

    model_config = ConfigDict(frozen=True)

    items: list[CommandRead]
