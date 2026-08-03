"""Request and response schemas of the control module.

``policy_type`` is not accepted on creation. There is one policy in M3, and a
field a client can only set to a single value is a field that will be set to
something else the moment a second policy exists. It is returned, so a reader
never has to guess which rule a loop evaluates.

The command representation serves both sources truthfully. A manual command has
no control loop and no trigger sample, so both are nullable rather than filled
with a value that would describe a decision nobody made.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StringConstraints,
    model_validator,
)

from ai_greenhouse.control.models import CommandSource, CommandState, ControlPolicyType

ReasonCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
"""Stable machine-readable cause of a terminal rejection."""

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


class CommandRejectionReason(BaseModel):
    """Why a command reached its terminal failure.

    One representation, shared by the public command read model and the Cloud ↔
    Edge acknowledgement that produces it. A rejection has a stable code a
    client can branch on and a message a person can read, and never an
    open-ended object whose keys a caller has to guess.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ReasonCode
    message: Annotated[str, Field(min_length=1, max_length=500)]


class ManualCommandCreate(BaseModel):
    """Body accepted by ``POST /api/v1/commands``.

    Boolean on/off only. ``desired_value`` is a ``bool`` and not a typed value
    object, a number or free-form JSON: this boundary exists so a customer can
    switch one actuator, and a shape that could carry a dimming level would be
    a contract for behaviour the backend does not have.

    It is strictly boolean, so ``1``, ``"on"`` and ``"true"`` are refused rather
    than coerced. That is the same rule the telemetry boundary already applies
    to a boolean point's value: a ``bool`` is not an integer, and a request that
    has to be guessed at is a request that can be guessed wrong.

    The idempotency key is not here. It travels in the required
    ``Idempotency-Key`` header, where a client that retries a lost request does
    not have to rebuild the body to keep it.
    """

    model_config = ConfigDict(extra="forbid")

    control_zone_id: UUID = Field(
        description="The control zone the target point is assigned to.",
    )
    target_point_id: UUID = Field(
        description=(
            "The active boolean control point to command. It must be assigned to"
            " control_zone_id in the control_output role and must name a reported"
            " status point."
        ),
    )
    desired_value: StrictBool = Field(
        description="The state asked for. true is on. It is a request, never a reading.",
    )


class CommandRead(BaseModel):
    """One command, whatever asked for it, with every identifier it is followed by.

    All three sample identifiers are returned rather than joined into embedded
    samples: the telemetry history endpoint already reads a sample, and
    duplicating it here would give one value two representations that could
    disagree.

    ``desired_value`` is a request and never a reading. What the actuator
    actually reports is the reported point's own state, read through
    ``GET /api/v1/points/{point_id}/state``; the two are deliberately separate
    and can disagree for as long as the physical change takes, or forever if it
    never happens.

    ``state`` is the delivery lifecycle. ``pending`` is non-terminal; ``applied``
    and ``rejected`` are terminal. ``acknowledged_at`` on a pending command means
    the Edge received it, not that anything moved.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    source: CommandSource
    idempotency_key: str
    control_zone_id: UUID
    control_loop_id: UUID | None
    trigger_sample_id: UUID | None
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
    rejection_reason: CommandRejectionReason | None
    created_at: datetime


class ManualCommandOutcome(StrEnum):
    """Whether an idempotent manual request created or replayed a command."""

    CREATED = "created"
    EXISTING = "existing"


class ManualCommandAcceptanceRead(BaseModel):
    """Result of one idempotent manual command request.

    The outcome is carried in the body as well as in the status code, so a
    client behind a proxy that rewrites statuses can still tell a first creation
    from a replay. ``existing`` means the stored command is returned unchanged
    and nothing was written or enqueued a second time.

    Acceptance is an acceptance of the *request*. It does not mean the actuator
    moved: the command is pending until the Edge reports it applied or rejected.
    """

    model_config = ConfigDict(frozen=True)

    outcome: ManualCommandOutcome
    command: CommandRead


class CommandListRead(BaseModel):
    """A count-free collection of commands.

    No total, like telemetry history and unlike the paged collections. The
    command list is a bounded newest-first window over an append-only table, and
    a ``COUNT(*)`` over it would grow with the history while answering a
    question nobody asked.
    """

    model_config = ConfigDict(frozen=True)

    items: list[CommandRead]
