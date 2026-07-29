"""Create control_loops.

Revision ID: 20260730_0009
Revises: 20260729_0008
Create Date: 2026-07-30

The table carries no ``updated_at`` and no ``status``: a loop is immutable, and
the API offers neither ``PATCH`` nor ``DELETE``. The unique constraint on
``control_zone_id`` is what enforces "one loop per zone" in the schema rather
than in the service, and its index also serves the ``?control_zone_id=`` list
filter.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0009"
down_revision: str | Sequence[str] | None = "20260729_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the immutable control-loop configuration table."""
    op.create_table(
        "control_loops",
        sa.Column("control_zone_id", sa.Uuid(), nullable=False),
        sa.Column("measurement_point_id", sa.Uuid(), nullable=False),
        sa.Column("control_point_id", sa.Uuid(), nullable=False),
        sa.Column("status_point_id", sa.Uuid(), nullable=False),
        sa.Column(
            "policy_type",
            sa.Enum(
                "hysteresis-v1",
                name="policy_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("lower_threshold", sa.Numeric(), nullable=False),
        sa.Column("upper_threshold", sa.Numeric(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_zone_id"],
            ["control_zones.id"],
            name=op.f("fk_control_loops_control_zone_id_control_zones"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["measurement_point_id"],
            ["points.id"],
            name=op.f("fk_control_loops_measurement_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_point_id"],
            ["points.id"],
            name=op.f("fk_control_loops_control_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["status_point_id"],
            ["points.id"],
            name=op.f("fk_control_loops_status_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_loops")),
        sa.UniqueConstraint("control_zone_id", name=op.f("uq_control_loops_control_zone_id")),
    )


def downgrade() -> None:
    """Remove the control-loop configuration table."""
    op.drop_table("control_loops")
