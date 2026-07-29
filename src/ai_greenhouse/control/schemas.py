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

from ai_greenhouse.control.models import ControlPolicyType

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
