"""ORM model of the telemetry module.

One rule governs the ``telemetry_samples`` table and outranks any convenience:
a row, once written, is never changed. There is therefore no ``updated_at``, no
soft-delete flag and no ORM relationship that could cascade a delete into it.
The only timestamps are the two the measurement itself has — when it happened
and when this system heard about it — and they are separate columns because a
buffered gateway makes them differ by hours.

The primary key is supplied by the producer rather than generated here. That is
what makes re-delivery detectable, so
:class:`~ai_greenhouse.infrastructure.database.base.UUIDPrimaryKeyMixin` is
deliberately not used: its ``uuid4`` default would turn a re-delivered sample
into a new row and silently defeat the idempotency this table is shaped for.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import MAX_UNIT_LENGTH
from ai_greenhouse.infrastructure.database.base import Base, enum_column
from ai_greenhouse.points.models import DataQuality

HISTORY_INDEX_NAME: str = "ix_telemetry_samples_point_id_observed_at_id"
"""The one index on the table, named after the query it exists for.

``(point_id, observed_at DESC, id DESC)`` is the history read of one point,
newest first, with ``id`` breaking ties between samples sharing an instant. No
standalone index on ``point_id`` accompanies it: that index would be a prefix
of this one, and a foreign key does not need an index of its own when a
composite one leads with it.
"""


class TelemetrySample(Base):
    """One value a point carried at one instant, kept forever.

    The row is the historical truth behind
    :class:`~ai_greenhouse.points.models.PointCurrentState`; the projection can
    be rebuilt from these rows, never the other way around.

    Attributes:
        id: Primary key, chosen by the producer. Re-recording the same id is a
            no-op rather than a duplicate, which is what makes an at-least-once
            producer safe.
        point_id: The point measured. ``ON DELETE RESTRICT``, so no point that
            has ever been measured can be removed out from under its history.
        simulation_run_id: The simulation run that produced the sample, when one
            did. Real measurements leave it ``NULL``. ``ON DELETE RESTRICT``
            keeps a persisted run from disappearing underneath its samples.
        value: The measured value, as ``jsonb`` so one table serves numeric,
            boolean and textual points. Never ``NULL``: absence of data is the
            ``no_data`` state of a point, not a sample carrying nothing.
        unit: The point's unit as it stood at measurement time. Snapshotted
            rather than joined, so re-labelling a point later cannot silently
            reinterpret values already recorded under the old unit.
        observed_at: When the value was measured at the source. Virtual time
            for a simulated sample.
        received_at: When this system took the value in. Never defaulted from
            ``observed_at``, and never merged with it.
        quality: Trustworthiness of the value, from
            :class:`~ai_greenhouse.points.models.DataQuality`. The simulator
            records ``simulated``.
    """

    __tablename__ = "telemetry_samples"
    __table_args__ = (
        Index(
            HISTORY_INDEX_NAME,
            "point_id",
            desc("observed_at"),
            desc("id"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    simulation_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("simulation_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(MAX_UNIT_LENGTH), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[DataQuality] = enum_column(
        DataQuality,
        constraint_name="quality",
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the value itself."""
        return f"TelemetrySample(id={self.id!r}, point_id={self.point_id!r})"
