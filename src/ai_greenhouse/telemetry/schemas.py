"""The write contract of the telemetry module.

This is not an HTTP body. :class:`TelemetrySampleRecord` is the argument
:meth:`~ai_greenhouse.telemetry.service.TelemetryService.record_sample` accepts,
and the public Edge adapter builds one per accepted message. It is a schema
rather than a plain dataclass so that every producer is held to the same shape
by the same validator, whichever boundary the measurement arrived on.

What the schema does *not* decide is whether ``value`` fits the point. That rule
needs the point's ``data_type``, which only the service can read, so the service
applies it and reports it as ``telemetry_value_type_mismatch``.
"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ai_greenhouse.core.types import UnitStr
from ai_greenhouse.points.models import DataQuality


class TelemetryWriteOutcome(StrEnum):
    """What the write boundary did with an offered sample.

    A producer used to be told nothing: the sample was accepted either way, and
    "accepted" covered three different things. Automation needs them apart,
    because only the first one is new information about the point — a replay and
    a late arrival describe a state the system has already reacted to, and
    re-deciding on either would act twice on one measurement.

    Attributes:
        RECORDED_CURRENT: The sample was new and became the current state.
        DUPLICATE: The identifier was already recorded; nothing changed.
        OUT_OF_ORDER: The sample was recorded in history, but an equal or newer
            measurement already holds the current state.
    """

    RECORDED_CURRENT = "recorded_current"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"


class TelemetrySampleRecord(BaseModel):
    """One measurement offered to the telemetry write boundary.

    Both instants are required to be timezone-aware. The columns behind them are
    ``timestamptz``, the comparison against the stored ``observed_at`` decides
    whether a sample is current, and a naive datetime would make that comparison
    raise instead of answer.

    Attributes:
        id: Identifier chosen by the producer, and the whole of the idempotency
            mechanism. A producer that can replay must derive the same id for
            the same measurement.
        point_id: The point being measured.
        value: The measured value. Judged against the point's ``data_type`` by
            the service; ``null`` is not a value.
        observed_at: When the measurement was taken at the source.
        received_at: When this system took it in.
        quality: How far the value can be trusted.
        unit: Accepted so that a producer which states its own unit is not
            rejected, and then ignored. The unit stored on the sample is always
            the point's, because a producer that disagrees with the point about
            what it is measuring in is reporting a configuration error, not a
            second opinion worth recording.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    point_id: UUID
    value: Any
    observed_at: AwareDatetime
    received_at: AwareDatetime
    quality: DataQuality
    unit: UnitStr | None = None


class TelemetrySampleRead(BaseModel):
    """One stored measurement returned unchanged by the history API."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    point_id: UUID
    value: Any
    unit: UnitStr | None
    observed_at: AwareDatetime
    received_at: AwareDatetime
    quality: DataQuality


class TelemetryHistoryRead(BaseModel):
    """A count-free collection of telemetry samples."""

    model_config = ConfigDict(frozen=True)

    items: list[TelemetrySampleRead]
