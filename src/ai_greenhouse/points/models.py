"""ORM models of the points module.

Two rules govern the ``points`` table and outrank any convenience:

1. It holds no physical address. ``device_id``, ``channel``, ``gpio``,
   ``register`` and ``modbus_address`` must never appear here.
2. It holds no current value. ``value``, ``last_value`` and ``last_reading``
   live on :class:`PointCurrentState`, and the history behind them lives in the
   telemetry stream.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import MAX_CODE_LENGTH, MAX_NAME_LENGTH, MAX_UNIT_LENGTH
from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_now,
)

DEFAULT_REVISION: int = 0
"""Revision every state row starts at, before any telemetry has replaced it."""


class PointKind(StrEnum):
    """Role a point plays in the system.

    One table with this column, rather than separate tables per kind: a
    measurement and a control point differ in how they are used, not in what
    they are, and every rule that refers to points has to span all four.
    """

    MEASUREMENT = "measurement"
    CONTROL = "control"
    STATUS = "status"
    DERIVED = "derived"


class PointDataType(StrEnum):
    """Type of the values a point carries.

    Governs how :attr:`PointCurrentState.value` is interpreted, which is why
    one ``jsonb`` column can serve numeric, boolean and textual points without
    a table per type.
    """

    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"


class DataQuality(StrEnum):
    """Trustworthiness of a value, carried by the value rather than logged apart.

    ``NO_DATA`` is the state every point starts in; the rest are the vocabulary
    telemetry writes with.
    """

    NO_DATA = "no_data"
    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"
    OUT_OF_RANGE = "out_of_range"
    SENSOR_FAULT = "sensor_fault"
    SIMULATED = "simulated"
    MANUALLY_ENTERED = "manually_entered"


class Point(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A stable logical point of measurement, control or status.

    The point is what everything else refers to: telemetry, control loops,
    alerts and recipes all address a point id. That reference only stays valid
    because the point says nothing about hardware — see the module docstring.

    ``site_id`` is mandatory and ``facility_id`` is not. A point can belong to
    the site as a whole (outdoor temperature, mains water pressure) while still
    being scoped, named and made unique within exactly one site.

    Attributes:
        site_id: Owning site, fixed at creation.
        facility_id: Owning facility when the point belongs to one, fixed at
            creation. Always inside ``site_id`` when set.
        code: Stable slug, unique within the site and immutable afterwards.
            Changing it would silently detach the point's history, so the API
            refuses to.
        name: Human-readable label, 1-200 characters after stripping.
        point_kind: Role of the point, from :class:`PointKind`.
        metric_type: What is being measured, as a slug such as
            ``air_temperature``. Shared across sites so that recipes and rules
            can speak about a metric without naming a specific point.
        data_type: Type of the values, from :class:`PointDataType`.
        unit: Unit of measurement. Required for numeric points and refused for
            boolean ones; the rule is applied in the service.
        min_value: Lower end of the plausible range, numeric points only.
        max_value: Upper end of the plausible range, numeric points only.
    """

    __tablename__ = "points"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_points_site_id_code"),)

    site_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    facility_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("facilities.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(MAX_CODE_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    point_kind: Mapped[PointKind] = enum_column(
        PointKind,
        constraint_name="point_kind",
        nullable=False,
        index=True,
    )
    metric_type: Mapped[str] = mapped_column(
        String(MAX_CODE_LENGTH),
        nullable=False,
        index=True,
    )
    data_type: Mapped[PointDataType] = enum_column(
        PointDataType,
        constraint_name="data_type",
        nullable=False,
    )
    unit: Mapped[str | None] = mapped_column(String(MAX_UNIT_LENGTH), nullable=True)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Point(id={self.id!r}, site_id={self.site_id!r}, code={self.code!r})"


class PointCurrentState(Base):
    """The last known state of one point, kept for fast reads.

    Exactly one row exists per point, created in the same transaction as the
    point itself. It is a projection and not the historical truth: it can be
    rebuilt from the telemetry stream, which is where a value's history actually
    lives.

    There is no ``created_at``. The row is created with its point and never
    re-created, so its own creation instant carries no information the point
    does not already have.

    Attributes:
        point_id: The point this state belongs to, and the primary key. Making
            it the primary key is what enforces "exactly one row per point" in
            the schema rather than in the service.
        value: Last known value, ``NULL`` until telemetry arrives. Stored as
            ``jsonb`` so one projection serves numeric, boolean and textual
            points; the point's ``data_type`` says how to read it.
        observed_at: When the value was measured at the source.
        received_at: When the system received it. Kept apart from
            ``observed_at`` on purpose — a buffered edge gateway makes the two
            differ by hours.
        quality: Trustworthiness of the value, from :class:`DataQuality`.
        revision: Update counter. It starts at :data:`DEFAULT_REVISION` and
            counts the replacements the projection has actually taken, so a
            re-delivered or out-of-order sample leaves it alone.
        updated_at: Instant this projection last changed.
    """

    __tablename__ = "point_current_states"

    point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    value: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    quality: Mapped[DataQuality] = enum_column(
        DataQuality,
        constraint_name="quality",
        nullable=False,
        default=DataQuality.NO_DATA,
    )
    revision: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        default=DEFAULT_REVISION,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"PointCurrentState(point_id={self.point_id!r}, quality={self.quality!r})"
