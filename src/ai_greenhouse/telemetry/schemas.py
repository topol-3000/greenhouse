"""The write contract of the telemetry module.

This is not an HTTP body. Milestone 2 exposes no ingestion endpoint, and
:class:`TelemetrySampleRecord` is the in-process argument
:meth:`~ai_greenhouse.telemetry.service.TelemetryService.record_sample` accepts
from the simulator today and from a device adapter later. It is a schema rather
than a plain dataclass so that every producer is held to the same shape by the
same validator, whether or not a request ever crosses the network.

What the schema does *not* decide is whether ``value`` fits the point. That rule
needs the point's ``data_type``, which only the service can read, so the service
applies it and reports it as ``telemetry_value_type_mismatch``.
"""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ai_greenhouse.core.types import UnitStr
from ai_greenhouse.points.models import DataQuality


class TelemetrySampleRecord(BaseModel):
    """One measurement offered to the telemetry write boundary.

    Both instants are required to be timezone-aware. The columns behind them are
    ``timestamptz``, the comparison against the stored ``observed_at`` decides
    whether a sample is current, and a naive datetime would make that comparison
    raise instead of answer.

    Attributes:
        id: Identifier chosen by the producer, and the whole of the idempotency
            mechanism. A producer that can replay — the simulator derives it
            from ``run_id``, ``step_index`` and ``point_id`` — must derive the
            same id for the same measurement.
        point_id: The point being measured.
        value: The measured value. Judged against the point's ``data_type`` by
            the service; ``null`` is not a value.
        observed_at: When the measurement was taken at the source.
        received_at: When this system took it in.
        quality: How far the value can be trusted.
        simulation_run_id: The run that produced the sample, for a simulated
            one.
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
    simulation_run_id: UUID | None = None
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
