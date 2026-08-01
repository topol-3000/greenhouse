"""Create the agronomy catalog tables.

Revision ID: 20260801_0016
Revises: 20260731_0015
Create Date: 2026-08-01

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entities rather than in mixin resolution order.
``status`` and ``requirement_kind`` are stored as ``VARCHAR`` with a ``CHECK``
constraint, following the shared enum convention; the constraint and index
names come from the metadata naming convention.

The five tables are created by one migration because none of them is useful
alone: a recipe identity carries no agronomic value, a version without its stage
and requirements says nothing, and the API creates the whole graph in one
transaction. They are introduced and removed together.

None of these tables has a ``facility_id``, ``control_zone_id``, ``point_id``,
``gateway_id`` or ``device_id`` column, and none names a command or a
simulation. A recipe describes an environment, never the equipment that
produces it, and a column tying generic agronomy to one installation's hardware
is exactly what these entities exist to avoid.

Every foreign key uses ``ON DELETE RESTRICT``: a crop with recipes, a recipe
with versions, a version with a stage and a stage with requirements cannot be
removed, not even by a direct ``DELETE`` against the database.

What the schema enforces on its own, rather than trusting the service to:

- a crop code and a recipe code are unique across the catalog;
- a version number is positive and unique within its recipe;
- a stage code and a stage position are unique within their version;
- one requirement exists per ``(stage, metric)``;
- ``ck_target_requirements_value_shape`` ties a requirement's values to its
  kind — a ``range`` has ordered bounds and no target, a ``duration_per_day``
  has a target inside ``(0, 24]`` and no bounds. The check also refuses the
  ``NaN`` and ``Infinity`` that a ``numeric`` column would otherwise accept for
  a duration.

There is no trigger and no JSON requirement document: every rule above is a
constraint PostgreSQL applies to ordinary columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0016"
down_revision: str | Sequence[str] | None = "20260731_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIREMENT_VALUE_SHAPE: str = (
    "(requirement_kind = 'range'"
    " AND min_value IS NOT NULL AND max_value IS NOT NULL"
    " AND target_value IS NULL AND min_value < max_value)"
    " OR (requirement_kind = 'duration_per_day'"
    " AND target_value IS NOT NULL"
    " AND min_value IS NULL AND max_value IS NULL"
    " AND target_value > 0 AND target_value <= 24)"
)
"""The one rule that gives ``requirement_kind`` its meaning."""


def upgrade() -> None:
    """Create the crop, recipe, version, stage and requirement tables."""
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


def downgrade() -> None:
    """Remove the agronomy catalog, deepest table first."""
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
