"""ORM models of the cultivation module.

Three rules govern these tables and outrank any convenience:

1. A cycle *references* a recipe version; it never copies one. There is no
   temperature, humidity or photoperiod column on :class:`GrowCycle`, because a
   duplicated band is a band that can disagree with the version it came from.
2. A :class:`RuntimeTarget` is the one exception, and it is an exception on
   purpose: it is an immutable snapshot taken at activation, so a cycle that ran
   last month still says what it was actually run against even if the crop, the
   recipe or the zone around it is archived later. Its values and its source
   links never change; the single permitted mutation is closing ``effective_to``
   once.
3. The zone a cycle runs in is a row of its own rather than a column on the
   cycle. A second aspect — irrigation, lighting — is a second assignment, and
   ``uq_grow_cycle_zone_assignments_grow_cycle_id`` is what keeps M5 to exactly
   one climate zone without the table having to be rebuilt to allow a second
   role later.

What the schema enforces on its own is listed in the migration that creates
these tables. The service repeats some of those checks to produce a precise
error; the database is what makes them true.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    desc,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import MAX_UNIT_LENGTH
from ai_greenhouse.infrastructure.database.base import (
    Base,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_now,
)

MAX_GROW_CYCLE_CODE_LENGTH: int = 80
MAX_GROW_CYCLE_NAME_LENGTH: int = 160

RUNTIME_TARGET_UNIT: str = "°C"
"""The only unit a temperature runtime target is expressed in.

The same constant the control module compares its thresholds in. A target in
``°F`` beside a loop measuring ``°C`` would be two numbers nothing converts
between, so the unit is fixed in the column, in the check constraint and in the
service rather than trusted to a caller.
"""

GROW_CYCLE_LIFECYCLE_CONSTRAINT: str = "lifecycle_timestamps"
"""Name of the check that ties a cycle's timestamps to its status.

The rule lives in the database and not only in the service because it is the
whole meaning of the lifecycle: an ``active`` cycle without a start, or a
``completed`` one without an end, is a row nobody can interpret.
"""

RUNTIME_TARGET_VALUE_CONSTRAINT: str = "values_ordered_and_finite"
"""Name of the check that makes a snapshot a band.

``lower < upper`` also rejects ``NaN`` on the lower end, and the explicit
infinity bounds reject it on the upper one: PostgreSQL orders ``NaN`` above
every other ``numeric``, so ``upper_value < 'Infinity'`` is false for it.
"""

ACTIVE_RUNTIME_TARGET_INDEX_NAME: str = "uq_runtime_targets_active_control_loop_id"
"""Partial unique index behind "one active target per control loop".

This is the final authority on the rule, not the service's pre-check. Two
concurrent activations resolving to the same loop both pass any application
check; only one of them can insert a row with ``effective_to IS NULL``.
"""

RUNTIME_TARGET_HISTORY_INDEX_NAME: str = "ix_runtime_targets_control_loop_id_created_at_id"
"""The runtime-target list read of one loop, newest first, named after its query.

``id DESC`` breaks ties between targets written in one instant, so repeated
calls return the same order.
"""


class GrowCycleStatus(StrEnum):
    """Lifecycle of a grow cycle.

    The four members are the whole lifecycle of M5. There is no ``paused`` and
    no way back: a cycle is planned, then run, then finished or given up on.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"


class GrowCycleZoneRole(StrEnum):
    """Aspect of a cycle a zone assignment covers.

    One member. It is an enum rather than a constant so the stored value carries
    a ``CHECK`` like every other enum column, and so an irrigation or lighting
    assignment arrives as a member instead of as free text nothing validates.
    """

    CLIMATE = "climate"


class RuntimeTargetMetric(StrEnum):
    """Metric a runtime target constrains.

    One member: M5 materializes the temperature band and nothing else. Humidity
    and photoperiod remain display-only properties of the recipe version.
    """

    AIR_TEMPERATURE = "air_temperature"


class GrowCycle(UUIDPrimaryKeyMixin, Base):
    """One run of one published recipe version in one facility.

    The cycle points at a :class:`~ai_greenhouse.agronomy.models.RecipeVersion`
    and not at the stable ``GrowingRecipe`` identity. A recipe identity carries
    no agronomic value, so a cycle that named only the identity would not say
    what it was actually grown against once a second version existed.

    There is no ``updated_at``: every mutation a cycle allows is a lifecycle
    transition, and each one writes the instant it happened into a column of its
    own.

    Attributes:
        code: Stable slug, unique across the installation and immutable.
        name: Human-readable label, 1-160 characters after stripping.
        facility_id: The facility the cycle runs in. ``ON DELETE RESTRICT``.
        recipe_version_id: The published version being grown. ``ON DELETE
            RESTRICT``: a version a cycle ran against cannot be removed
            underneath it.
        current_stage_id: The version's single stage, derived by the service
            rather than supplied by a caller. ``ON DELETE RESTRICT``.
        status: Lifecycle state; see :class:`GrowCycleStatus`.
        planned_start_at: When the operator intends to start, if they said.
            Advisory only: nothing schedules an activation from it.
        started_at: Instant the cycle was activated; ``NULL`` until it is.
        ended_at: Instant the cycle became terminal; ``NULL`` until it does.
        created_at: Instant the planned cycle was registered.
    """

    __tablename__ = "grow_cycles"
    __table_args__ = (
        CheckConstraint(
            "(status = 'planned' AND started_at IS NULL AND ended_at IS NULL)"
            " OR (status = 'active' AND started_at IS NOT NULL AND ended_at IS NULL)"
            " OR (status = 'completed' AND started_at IS NOT NULL"
            " AND ended_at IS NOT NULL AND started_at <= ended_at)"
            " OR (status = 'aborted' AND ended_at IS NOT NULL"
            " AND (started_at IS NULL OR started_at <= ended_at))",
            name=GROW_CYCLE_LIFECYCLE_CONSTRAINT,
        ),
    )

    code: Mapped[str] = mapped_column(
        String(MAX_GROW_CYCLE_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(MAX_GROW_CYCLE_NAME_LENGTH), nullable=False)
    facility_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("facilities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    recipe_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("recipe_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_stage_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("recipe_stages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[GrowCycleStatus] = enum_column(
        GrowCycleStatus,
        constraint_name="status",
        nullable=False,
        default=GrowCycleStatus.PLANNED,
        index=True,
    )
    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"GrowCycle(id={self.id!r}, code={self.code!r}, status={self.status!r})"


class GrowCycleZoneAssignment(UUIDPrimaryKeyMixin, Base):
    """The link that says a cycle is run in one zone, and for what aspect.

    Held apart from :class:`GrowCycle` rather than as a ``control_zone_id``
    column on it: a cycle covering climate *and* irrigation is a second row and
    not a second column, and a schema that had to be rebuilt for that would make
    the eventual change bigger than the feature.

    The uniqueness on ``grow_cycle_id`` alone is what limits M5 to one
    assignment per cycle. Widening it to ``(grow_cycle_id, role)`` is the whole
    of what a second aspect needs.

    Attributes:
        grow_cycle_id: The cycle being placed. ``ON DELETE RESTRICT``.
        control_zone_id: The zone it runs in. Held by identifier and not by an
            ORM relationship, because the zone belongs to another aggregate.
        role: Aspect the zone covers, fixed at ``climate``.
        created_at: Instant the link was made.
    """

    __tablename__ = "grow_cycle_zone_assignments"
    __table_args__ = (
        UniqueConstraint(
            "grow_cycle_id",
            name="uq_grow_cycle_zone_assignments_grow_cycle_id",
        ),
    )

    grow_cycle_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("grow_cycles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    control_zone_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_zones.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[GrowCycleZoneRole] = enum_column(
        GrowCycleZoneRole,
        constraint_name="role",
        nullable=False,
        default=GrowCycleZoneRole.CLIMATE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"GrowCycleZoneAssignment(id={self.id!r}, "
            f"grow_cycle_id={self.grow_cycle_id!r}, "
            f"control_zone_id={self.control_zone_id!r})"
        )


class GrowStageInstance(UUIDPrimaryKeyMixin, Base):
    """One stage of a recipe version as it was actually run.

    The recipe's :class:`~ai_greenhouse.agronomy.models.RecipeStage` says what a
    stage asks for and carries no duration; this row says when that stage was
    entered and left. Keeping the two apart is what lets one version drive
    cycles of different lengths.

    M5 creates at most one instance per cycle, which
    ``uq_grow_stage_instances_grow_cycle_id`` enforces. Stage advancement drops
    that constraint; nothing else about the table changes.

    Attributes:
        grow_cycle_id: The cycle this stage belongs to. ``ON DELETE RESTRICT``.
        recipe_stage_id: The stage of the version that was entered. ``ON DELETE
            RESTRICT``.
        started_at: The activation instant, shared with the cycle and its
            runtime target.
        ended_at: The terminal instant, shared with them as well; ``NULL`` while
            the cycle runs.
    """

    __tablename__ = "grow_stage_instances"
    __table_args__ = (
        UniqueConstraint(
            "grow_cycle_id",
            name="uq_grow_stage_instances_grow_cycle_id",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ended_at_not_before_started_at",
        ),
    )

    grow_cycle_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("grow_cycles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recipe_stage_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("recipe_stages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"GrowStageInstance(id={self.id!r}, "
            f"grow_cycle_id={self.grow_cycle_id!r}, "
            f"recipe_stage_id={self.recipe_stage_id!r})"
        )


class RuntimeTarget(UUIDPrimaryKeyMixin, Base):
    """The temperature band one control loop is being grown against.

    An immutable snapshot of a
    :class:`~ai_greenhouse.agronomy.models.TargetRequirement`, taken once when a
    cycle is activated. The values are copied rather than read through the
    foreign key so that archiving the crop, the recipe or the zone afterwards
    cannot change what a finished cycle says it ran against; the foreign keys are
    kept anyway, so the snapshot can still be traced back to the requirement it
    came from.

    While this row is active, the existing automation flow uses its snapshot
    bounds before falling back to the control loop's immutable thresholds. A
    command decided from it retains its identifier as provenance.

    Attributes:
        control_loop_id: The loop the band applies to. ``ON DELETE RESTRICT``.
        grow_cycle_id: The cycle that materialized it. ``ON DELETE RESTRICT``.
        target_requirement_id: The requirement the values were copied from.
            ``ON DELETE RESTRICT``.
        metric_type: Fixed at ``air_temperature``; see
            :class:`RuntimeTargetMetric`.
        lower_value: The requirement's ``min_value``, exactly as stored.
        upper_value: The requirement's ``max_value``, exactly as stored.
        unit: Fixed at ``°C``; see :data:`RUNTIME_TARGET_UNIT`.
        effective_from: The activation instant, shared with the cycle and its
            stage instance.
        effective_to: The terminal instant, shared with them as well; ``NULL``
            while the target is the active one for its loop.
        created_at: Instant the row was written.
    """

    __tablename__ = "runtime_targets"
    __table_args__ = (
        CheckConstraint(
            "lower_value < upper_value"
            " AND lower_value > '-Infinity'::numeric"
            " AND upper_value < 'Infinity'::numeric",
            name=RUNTIME_TARGET_VALUE_CONSTRAINT,
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_to_not_before_effective_from",
        ),
        CheckConstraint(f"unit = '{RUNTIME_TARGET_UNIT}'", name="unit"),
        Index(
            ACTIVE_RUNTIME_TARGET_INDEX_NAME,
            "control_loop_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index(
            RUNTIME_TARGET_HISTORY_INDEX_NAME,
            "control_loop_id",
            desc("created_at"),
            desc("id"),
        ),
    )

    control_loop_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_loops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grow_cycle_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("grow_cycles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_requirement_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("target_requirements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric_type: Mapped[RuntimeTargetMetric] = enum_column(
        RuntimeTargetMetric,
        constraint_name="metric_type",
        nullable=False,
        default=RuntimeTargetMetric.AIR_TEMPERATURE,
    )
    lower_value: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    upper_value: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    unit: Mapped[str] = mapped_column(
        String(MAX_UNIT_LENGTH),
        nullable=False,
        default=RUNTIME_TARGET_UNIT,
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"RuntimeTarget(id={self.id!r}, "
            f"control_loop_id={self.control_loop_id!r}, "
            f"effective_to={self.effective_to!r})"
        )
