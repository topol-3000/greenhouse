"""Create the grow cycle lifecycle tables.

Revision ID: 20260801_0017
Revises: 20260801_0016
Create Date: 2026-08-01

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entities rather than in mixin resolution order.
``status``, ``role`` and ``metric_type`` are stored as ``VARCHAR`` with a
``CHECK`` constraint, following the shared enum convention; the constraint and
index names come from the metadata naming convention.

The four tables are created by one migration because none of them is useful
alone: a cycle without its zone assignment could never be activated, and a stage
instance or a runtime target without the cycle that opened it belongs to nothing.
They are introduced and removed together.

This is the one place in the schema where the generic agronomy catalog and the
concrete topology meet. The catalog itself is untouched: no column is added to
``crops``, ``growing_recipes``, ``recipe_versions``, ``recipe_stages`` or
``target_requirements``, and nothing about ``control_loops`` or ``commands``
changes. In particular there is no ``commands.runtime_target_id``: nothing
consumes a runtime target yet.

Every foreign key uses ``ON DELETE RESTRICT``. A recipe version a cycle ran
against, the stage it ran, the requirement its band was copied from and the loop
that band addressed all stay reachable for as long as the cycle exists, not even
removable by a direct ``DELETE`` against the database.

What the schema enforces on its own, rather than trusting the service to:

- a grow cycle code is unique across the installation;
- ``ck_grow_cycles_lifecycle_timestamps`` ties the timestamps to the status — a
  planned cycle has neither, an active one has a start and no end, a completed
  one has both in order, and an aborted one has an end whether it ever started
  or not. It is also what refuses the four statuses' every other combination;
- exactly one zone assignment per cycle, and that its role is ``climate``;
- at most one stage instance per cycle, which is the M5 limit, and that a stage
  cannot end before it started;
- a runtime target's metric is ``air_temperature`` and its unit is ``°C``;
- ``ck_runtime_targets_values_ordered_and_finite`` makes a target a band:
  ``lower < upper`` rejects a ``NaN`` lower bound, and the explicit infinity
  comparisons reject an infinite or ``NaN`` upper one, since PostgreSQL orders
  ``NaN`` above every other ``numeric``;
- ``effective_to`` never precedes ``effective_from``;
- ``uq_runtime_targets_active_control_loop_id``, a partial unique index over
  ``control_loop_id WHERE effective_to IS NULL``, allows at most one active
  target per control loop. This is the final authority behind
  ``grow_cycle_target_conflict``: two concurrent activations resolving to one
  loop both pass any application check, and only one of them can insert.

There is no trigger and no JSON target document: every rule above is a
constraint or an index PostgreSQL applies to ordinary columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0017"
down_revision: str | Sequence[str] | None = "20260801_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROW_CYCLE_LIFECYCLE: str = (
    "(status = 'planned' AND started_at IS NULL AND ended_at IS NULL)"
    " OR (status = 'active' AND started_at IS NOT NULL AND ended_at IS NULL)"
    " OR (status = 'completed' AND started_at IS NOT NULL"
    " AND ended_at IS NOT NULL AND started_at <= ended_at)"
    " OR (status = 'aborted' AND ended_at IS NOT NULL"
    " AND (started_at IS NULL OR started_at <= ended_at))"
)
"""The rule that gives a cycle's status its meaning."""

RUNTIME_TARGET_VALUES: str = (
    "lower_value < upper_value"
    " AND lower_value > '-Infinity'::numeric"
    " AND upper_value < 'Infinity'::numeric"
)
"""The rule that makes an immutable snapshot a usable band."""

ACTIVE_RUNTIME_TARGET_INDEX: str = "uq_runtime_targets_active_control_loop_id"
RUNTIME_TARGET_HISTORY_INDEX: str = "ix_runtime_targets_control_loop_id_created_at_id"
ACTIVE_RUNTIME_TARGET_PREDICATE: str = "effective_to IS NULL"


def upgrade() -> None:
    """Create the cycle, assignment, stage instance and runtime target tables."""
    op.create_table(
        "grow_cycles",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_stage_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "active",
                "completed",
                "aborted",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            GROW_CYCLE_LIFECYCLE,
            name=op.f("ck_grow_cycles_lifecycle_timestamps"),
        ),
        sa.ForeignKeyConstraint(
            ["current_stage_id"],
            ["recipe_stages.id"],
            name=op.f("fk_grow_cycles_current_stage_id_recipe_stages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name=op.f("fk_grow_cycles_facility_id_facilities"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_version_id"],
            ["recipe_versions.id"],
            name=op.f("fk_grow_cycles_recipe_version_id_recipe_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grow_cycles")),
    )
    op.create_index(op.f("ix_grow_cycles_code"), "grow_cycles", ["code"], unique=True)
    op.create_index(
        op.f("ix_grow_cycles_current_stage_id"),
        "grow_cycles",
        ["current_stage_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grow_cycles_facility_id"),
        "grow_cycles",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grow_cycles_recipe_version_id"),
        "grow_cycles",
        ["recipe_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_grow_cycles_status"), "grow_cycles", ["status"], unique=False)

    op.create_table(
        "grow_cycle_zone_assignments",
        sa.Column("grow_cycle_id", sa.Uuid(), nullable=False),
        sa.Column("control_zone_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "climate",
                name="role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_zone_id"],
            ["control_zones.id"],
            name=op.f("fk_grow_cycle_zone_assignments_control_zone_id_control_zones"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grow_cycle_id"],
            ["grow_cycles.id"],
            name=op.f("fk_grow_cycle_zone_assignments_grow_cycle_id_grow_cycles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grow_cycle_zone_assignments")),
        sa.UniqueConstraint(
            "grow_cycle_id",
            name="uq_grow_cycle_zone_assignments_grow_cycle_id",
        ),
    )
    op.create_index(
        op.f("ix_grow_cycle_zone_assignments_control_zone_id"),
        "grow_cycle_zone_assignments",
        ["control_zone_id"],
        unique=False,
    )

    op.create_table(
        "grow_stage_instances",
        sa.Column("grow_cycle_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_stage_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_grow_stage_instances_ended_at_not_before_started_at"),
        ),
        sa.ForeignKeyConstraint(
            ["grow_cycle_id"],
            ["grow_cycles.id"],
            name=op.f("fk_grow_stage_instances_grow_cycle_id_grow_cycles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_stage_id"],
            ["recipe_stages.id"],
            name=op.f("fk_grow_stage_instances_recipe_stage_id_recipe_stages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grow_stage_instances")),
        sa.UniqueConstraint("grow_cycle_id", name="uq_grow_stage_instances_grow_cycle_id"),
    )
    op.create_index(
        op.f("ix_grow_stage_instances_recipe_stage_id"),
        "grow_stage_instances",
        ["recipe_stage_id"],
        unique=False,
    )

    op.create_table(
        "runtime_targets",
        sa.Column("control_loop_id", sa.Uuid(), nullable=False),
        sa.Column("grow_cycle_id", sa.Uuid(), nullable=False),
        sa.Column("target_requirement_id", sa.Uuid(), nullable=False),
        sa.Column(
            "metric_type",
            sa.Enum(
                "air_temperature",
                name="metric_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("lower_value", sa.Numeric(), nullable=False),
        sa.Column("upper_value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            RUNTIME_TARGET_VALUES,
            name=op.f("ck_runtime_targets_values_ordered_and_finite"),
        ),
        sa.CheckConstraint("unit = '°C'", name=op.f("ck_runtime_targets_unit")),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_runtime_targets_effective_to_not_before_effective_from"),
        ),
        sa.ForeignKeyConstraint(
            ["control_loop_id"],
            ["control_loops.id"],
            name=op.f("fk_runtime_targets_control_loop_id_control_loops"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grow_cycle_id"],
            ["grow_cycles.id"],
            name=op.f("fk_runtime_targets_grow_cycle_id_grow_cycles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_requirement_id"],
            ["target_requirements.id"],
            name=op.f("fk_runtime_targets_target_requirement_id_target_requirements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_targets")),
    )
    op.create_index(
        RUNTIME_TARGET_HISTORY_INDEX,
        "runtime_targets",
        ["control_loop_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_targets_grow_cycle_id"),
        "runtime_targets",
        ["grow_cycle_id"],
        unique=False,
    )
    op.create_index(
        ACTIVE_RUNTIME_TARGET_INDEX,
        "runtime_targets",
        ["control_loop_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RUNTIME_TARGET_PREDICATE),
    )


def downgrade() -> None:
    """Remove the grow cycle lifecycle, deepest table first."""
    op.drop_index(
        ACTIVE_RUNTIME_TARGET_INDEX,
        table_name="runtime_targets",
        postgresql_where=sa.text(ACTIVE_RUNTIME_TARGET_PREDICATE),
    )
    op.drop_index(op.f("ix_runtime_targets_grow_cycle_id"), table_name="runtime_targets")
    op.drop_index(RUNTIME_TARGET_HISTORY_INDEX, table_name="runtime_targets")
    op.drop_table("runtime_targets")
    op.drop_index(
        op.f("ix_grow_stage_instances_recipe_stage_id"),
        table_name="grow_stage_instances",
    )
    op.drop_table("grow_stage_instances")
    op.drop_index(
        op.f("ix_grow_cycle_zone_assignments_control_zone_id"),
        table_name="grow_cycle_zone_assignments",
    )
    op.drop_table("grow_cycle_zone_assignments")
    op.drop_index(op.f("ix_grow_cycles_status"), table_name="grow_cycles")
    op.drop_index(op.f("ix_grow_cycles_recipe_version_id"), table_name="grow_cycles")
    op.drop_index(op.f("ix_grow_cycles_facility_id"), table_name="grow_cycles")
    op.drop_index(op.f("ix_grow_cycles_current_stage_id"), table_name="grow_cycles")
    op.drop_index(op.f("ix_grow_cycles_code"), table_name="grow_cycles")
    op.drop_table("grow_cycles")
