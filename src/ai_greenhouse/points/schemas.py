"""Request and response schemas of the points module.

No schema here carries a device, a channel or a physical address, and none
carries a current value. A point's value is read from its state projection
through ``GET /api/v1/points/{point_id}/state`` and, in Milestone 1, is always
absent.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PlainSerializer, model_validator

from ai_greenhouse.core.types import CodeStr, NameStr, UnitStr
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import DataQuality, PointDataType, PointKind

RangeBound = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
]
"""One end of a point's plausible range.

Held as ``Decimal`` so the value written to the ``numeric`` column is the one
the client sent, and serialised back as a JSON number rather than as the string
Pydantic would otherwise produce.
"""


class PointCreate(BaseModel):
    """Body accepted by ``POST /api/v1/points``.

    The relationship between ``data_type``, ``unit`` and the range is not
    checked here. Those rules produce their own error codes — ``unit_required``,
    ``unit_not_allowed``, ``range_not_allowed`` — which a schema validator
    cannot express, so the service applies them. The one rule kept at this level
    is the ordering of the range, because both ends are always visible in a
    creation body.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: UUID
    facility_id: UUID | None = None
    code: CodeStr
    name: NameStr
    point_kind: PointKind
    metric_type: CodeStr
    data_type: PointDataType
    unit: UnitStr | None = None
    min_value: RangeBound | None = None
    max_value: RangeBound | None = None

    @model_validator(mode="after")
    def check_range_is_ordered(self) -> Self:
        """Reject a range whose lower end is above its upper end.

        Returns:
            The validated body, unchanged.

        Raises:
            ValueError: If both ends are given and ``min_value`` is the larger
                one. Pydantic turns this into HTTP 422 ``validation_error``.
        """
        if self.min_value is not None and self.max_value is not None:
            if self.min_value > self.max_value:
                raise ValueError("min_value must not be greater than max_value")
        return self


class PointUpdate(BaseModel):
    """Body accepted by ``PATCH /api/v1/points/{point_id}``.

    Only the fields present in the request are applied. Unlike the topology
    schemas, an explicit ``null`` is *not* the same as omitting a field here:
    ``unit``, ``min_value`` and ``max_value`` are nullable columns, so sending
    ``{"min_value": null}`` clears the lower bound while leaving it out keeps
    it. The service reads ``model_fields_set`` to tell the two apart.

    Every field the point's meaning depends on is declared even though none can
    be changed, and declared with a permissive type on purpose. Accepting
    ``{"point_kind": "vibes"}`` as a string is what lets the service answer with
    HTTP 409 ``immutable_field`` — the honest reason for the refusal — instead
    of the HTTP 422 an enum would produce for a value that was never going to be
    applied anyway.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    unit: UnitStr | None = None
    min_value: RangeBound | None = None
    max_value: RangeBound | None = None
    status: StatusEnum | None = None
    code: str | None = None
    site_id: UUID | None = None
    facility_id: UUID | None = None
    point_kind: str | None = None
    metric_type: str | None = None
    data_type: str | None = None


class PointRead(BaseModel):
    """Representation of a point returned by every point endpoint.

    Carries no value and no physical address. Both omissions are the point of
    the entity: the value lives in :class:`PointStateRead`, and the hardware
    behind it arrives in Milestone 6 without this representation changing.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    site_id: UUID
    facility_id: UUID | None
    code: str
    name: str
    point_kind: PointKind
    metric_type: str
    data_type: PointDataType
    unit: str | None
    min_value: RangeBound | None
    max_value: RangeBound | None
    status: StatusEnum
    created_at: datetime
    updated_at: datetime


class PointStateRead(BaseModel):
    """Representation returned by ``GET /api/v1/points/{point_id}/state``.

    In Milestone 1 every point answers with ``value = null`` and
    ``quality = "no_data"``: nothing writes to the projection yet. The shape is
    already the final one, so the clients written against it keep working when
    telemetry starts filling it in.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    point_id: UUID
    value: Any = None
    observed_at: datetime | None
    received_at: datetime | None
    quality: DataQuality
    revision: int
    updated_at: datetime
