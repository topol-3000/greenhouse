"""Create the points and point_current_states tables.

Revision ID: 20260728_0005
Revises: 20260728_0004
Create Date: 2026-07-28

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entities rather than in mixin resolution order.
``point_kind``, ``data_type``, ``status`` and ``quality`` are stored as
``VARCHAR`` with a ``CHECK`` constraint, following the shared enum
convention; the constraint and index names come from the metadata naming
convention.

Both tables are created by one migration because neither is useful alone: a
point without its state projection violates the invariant the projection
exists to carry, so they are introduced and removed together.

``points`` deliberately has **no** ``device_id``, ``channel``, ``gpio``,
``register`` or ``modbus_address`` column, and **no** ``value``,
``last_value`` or ``last_reading`` column. A physical binding would arrive on
its own table, and the value lives in
``point_current_states``. Adding either kind of column here would tie a point's
identity to hardware or to a moment in time, which is exactly what the entity
exists to avoid.

``facility_id`` is nullable: a point may belong to the site as a whole, such as
an outdoor temperature shared by every facility on it. ``site_id`` is not,
which is what scopes the unique code.

Both foreign keys use ``ON DELETE RESTRICT``: neither a site nor a facility
that still has points can be removed, not even by a direct ``DELETE`` against
the database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_0005"
down_revision: str | Sequence[str] | None = "20260728_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``points`` and ``point_current_states`` with their indexes."""
    op.create_table(
        "points",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "point_kind",
            sa.Enum(
                "measurement",
                "control",
                "status",
                "derived",
                name="point_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("metric_type", sa.String(length=63), nullable=False),
        sa.Column(
            "data_type",
            sa.Enum(
                "float",
                "integer",
                "boolean",
                "string",
                name="data_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("min_value", sa.Numeric(), nullable=True),
        sa.Column("max_value", sa.Numeric(), nullable=True),
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
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["facility_id"],
            ["facilities.id"],
            name=op.f("fk_points_facility_id_facilities"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_points_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points")),
        sa.UniqueConstraint("site_id", "code", name="uq_points_site_id_code"),
    )
    op.create_index(op.f("ix_points_facility_id"), "points", ["facility_id"], unique=False)
    op.create_index(op.f("ix_points_metric_type"), "points", ["metric_type"], unique=False)
    op.create_index(op.f("ix_points_point_kind"), "points", ["point_kind"], unique=False)
    op.create_index(op.f("ix_points_site_id"), "points", ["site_id"], unique=False)
    op.create_index(op.f("ix_points_status"), "points", ["status"], unique=False)

    op.create_table(
        "point_current_states",
        sa.Column("point_id", sa.Uuid(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "quality",
            sa.Enum(
                "no_data",
                "good",
                "uncertain",
                "bad",
                "stale",
                "out_of_range",
                "sensor_fault",
                "simulated",
                "manually_entered",
                name="quality",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["point_id"],
            ["points.id"],
            name=op.f("fk_point_current_states_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("point_id", name=op.f("pk_point_current_states")),
    )


def downgrade() -> None:
    """Drop both tables, the projection first so its foreign key goes with it."""
    op.drop_table("point_current_states")
    op.drop_index(op.f("ix_points_status"), table_name="points")
    op.drop_index(op.f("ix_points_site_id"), table_name="points")
    op.drop_index(op.f("ix_points_point_kind"), table_name="points")
    op.drop_index(op.f("ix_points_metric_type"), table_name="points")
    op.drop_index(op.f("ix_points_facility_id"), table_name="points")
    op.drop_table("points")
