"""Remove the agronomy catalog, the grow cycle lifecycle and command provenance.

Revision ID: 20260803_0019
Revises: 20260801_0018
Create Date: 2026-08-03

Milestone 5 is rolled back out of the active product. This is a *forward*
compensating migration: ``20260801_0016``, ``20260801_0017`` and
``20260801_0018`` are published history and are not edited or deleted, so a
database that has already applied them reaches the rolled-back schema by moving
forward rather than by having its past rewritten.

Order is dictated by the dependencies the three revisions created, deepest
reference first:

1. ``commands.runtime_target_id`` — its index, then its foreign key, then the
   column. It is the only reference into M5 from a table that stays, and while
   it exists ``runtime_targets`` cannot be dropped;
2. the grow-cycle lifecycle — ``runtime_targets``, ``grow_stage_instances``,
   ``grow_cycle_zone_assignments``, ``grow_cycles``;
3. the agronomy catalog — ``target_requirements``, ``recipe_stages``,
   ``recipe_versions``, ``growing_recipes``, ``crops``.

``control_loops``, ``commands``, ``telemetry_samples``, ``points`` and the
topology are untouched. A command that recorded a RuntimeTarget loses only that
provenance column; its decision, its idempotency key, its trigger sample and
both result samples stay exactly as they were.

**Data loss is irreversible.** ``downgrade`` recreates the schema these nine
tables and the one column had — enough for a migration round trip and for
``alembic check`` at ``20260801_0018`` — but no crop, recipe, version, stage,
requirement, cycle, stage instance or runtime target row comes back, and every
``commands.runtime_target_id`` returns as ``NULL``. Restoring the rows is a
restore from backup, not a downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0019"
down_revision: str | Sequence[str] | None = "20260801_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMAND_RUNTIME_TARGET_INDEX: str = "ix_commands_runtime_target_id"
COMMAND_RUNTIME_TARGET_FK: str = "fk_commands_runtime_target_id_runtime_targets"

REQUIREMENT_VALUE_SHAPE: str = (
    "(requirement_kind = 'range'"
    " AND min_value IS NOT NULL AND max_value IS NOT NULL"
    " AND target_value IS NULL AND min_value < max_value)"
    " OR (requirement_kind = 'duration_per_day'"
    " AND target_value IS NOT NULL"
    " AND min_value IS NULL AND max_value IS NULL"
    " AND target_value > 0 AND target_value <= 24)"
)
"""The rule ``20260801_0016`` gave ``requirement_kind``, restored verbatim."""

GROW_CYCLE_LIFECYCLE: str = (
    "(status = 'planned' AND started_at IS NULL AND ended_at IS NULL)"
    " OR (status = 'active' AND started_at IS NOT NULL AND ended_at IS NULL)"
    " OR (status = 'completed' AND started_at IS NOT NULL"
    " AND ended_at IS NOT NULL AND started_at <= ended_at)"
    " OR (status = 'aborted' AND ended_at IS NOT NULL"
    " AND (started_at IS NULL OR started_at <= ended_at))"
)
"""The rule ``20260801_0017`` gave a cycle's status, restored verbatim."""

RUNTIME_TARGET_VALUES: str = (
    "lower_value < upper_value"
    " AND lower_value > '-Infinity'::numeric"
    " AND upper_value < 'Infinity'::numeric"
)
"""The rule that made an immutable snapshot a usable band, restored verbatim."""

ACTIVE_RUNTIME_TARGET_INDEX: str = "uq_runtime_targets_active_control_loop_id"
RUNTIME_TARGET_HISTORY_INDEX: str = "ix_runtime_targets_control_loop_id_created_at_id"
ACTIVE_RUNTIME_TARGET_PREDICATE: str = "effective_to IS NULL"


def upgrade() -> None:
    """Drop command provenance, then the lifecycle, then the catalog."""
    op.drop_index(COMMAND_RUNTIME_TARGET_INDEX, table_name="commands")
    op.drop_constraint(COMMAND_RUNTIME_TARGET_FK, "commands", type_="foreignkey")
    op.drop_column("commands", "runtime_target_id")

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

    op.drop_index(
        op.f("ix_target_requirements_recipe_stage_id"),
        table_name="target_requirements",
    )
    op.drop_table("target_requirements")

    op.drop_index(op.f("ix_recipe_stages_recipe_version_id"), table_name="recipe_stages")
    op.drop_table("recipe_stages")

    op.drop_index(op.f("ix_recipe_versions_recipe_id"), table_name="recipe_versions")
    op.drop_table("recipe_versions")

    op.drop_index(op.f("ix_growing_recipes_status"), table_name="growing_recipes")
    op.drop_index(op.f("ix_growing_recipes_crop_id"), table_name="growing_recipes")
    op.drop_index(op.f("ix_growing_recipes_code"), table_name="growing_recipes")
    op.drop_table("growing_recipes")

    op.drop_index(op.f("ix_crops_status"), table_name="crops")
    op.drop_index(op.f("ix_crops_code"), table_name="crops")
    op.drop_table("crops")


def downgrade() -> None:
    """Rebuild the M5 schema, empty.

    The tables, constraints and indexes are recreated exactly as
    ``20260801_0016``, ``20260801_0017`` and ``20260801_0018`` left them, which
    is what makes the round trip and ``alembic check`` at ``20260801_0018``
    meaningful. The rows the upgrade deleted are gone for good.
    """
    op.create_table(
        "crops",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("scientific_name", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crops")),
    )
    op.create_index(op.f("ix_crops_code"), "crops", ["code"], unique=True)
    op.create_index(op.f("ix_crops_status"), "crops", ["status"], unique=False)

    op.create_table(
        "growing_recipes",
        sa.Column("crop_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["crop_id"],
            ["crops.id"],
            name=op.f("fk_growing_recipes_crop_id_crops"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_growing_recipes")),
    )
    op.create_index(op.f("ix_growing_recipes_code"), "growing_recipes", ["code"], unique=True)
    op.create_index(
        op.f("ix_growing_recipes_crop_id"),
        "growing_recipes",
        ["crop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_growing_recipes_status"),
        "growing_recipes",
        ["status"],
        unique=False,
    )

    op.create_table(
        "recipe_versions",
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_recipe_versions_version_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["growing_recipes.id"],
            name=op.f("fk_recipe_versions_recipe_id_growing_recipes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_versions")),
        sa.UniqueConstraint(
            "recipe_id",
            "version_number",
            name="uq_recipe_versions_recipe_id_version_number",
        ),
    )
    op.create_index(
        op.f("ix_recipe_versions_recipe_id"),
        "recipe_versions",
        ["recipe_id"],
        unique=False,
    )

    op.create_table(
        "recipe_stages",
        sa.Column("recipe_version_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "sequence_number > 0",
            name=op.f("ck_recipe_stages_sequence_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["recipe_version_id"],
            ["recipe_versions.id"],
            name=op.f("fk_recipe_stages_recipe_version_id_recipe_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_stages")),
        sa.UniqueConstraint(
            "recipe_version_id",
            "code",
            name="uq_recipe_stages_recipe_version_id_code",
        ),
        sa.UniqueConstraint(
            "recipe_version_id",
            "sequence_number",
            name="uq_recipe_stages_recipe_version_id_sequence_number",
        ),
    )
    op.create_index(
        op.f("ix_recipe_stages_recipe_version_id"),
        "recipe_stages",
        ["recipe_version_id"],
        unique=False,
    )

    op.create_table(
        "target_requirements",
        sa.Column("recipe_stage_id", sa.Uuid(), nullable=False),
        sa.Column("metric_type", sa.String(length=63), nullable=False),
        sa.Column(
            "requirement_kind",
            sa.Enum(
                "range",
                "duration_per_day",
                name="requirement_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("min_value", sa.Numeric(), nullable=True),
        sa.Column("max_value", sa.Numeric(), nullable=True),
        sa.Column("target_value", sa.Numeric(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            REQUIREMENT_VALUE_SHAPE,
            name=op.f("ck_target_requirements_value_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["recipe_stage_id"],
            ["recipe_stages.id"],
            name=op.f("fk_target_requirements_recipe_stage_id_recipe_stages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_target_requirements")),
        sa.UniqueConstraint(
            "recipe_stage_id",
            "metric_type",
            name="uq_target_requirements_recipe_stage_id_metric_type",
        ),
    )
    op.create_index(
        op.f("ix_target_requirements_recipe_stage_id"),
        "target_requirements",
        ["recipe_stage_id"],
        unique=False,
    )

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

    op.add_column("commands", sa.Column("runtime_target_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        COMMAND_RUNTIME_TARGET_FK,
        "commands",
        "runtime_targets",
        ["runtime_target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(COMMAND_RUNTIME_TARGET_INDEX, "commands", ["runtime_target_id"])
