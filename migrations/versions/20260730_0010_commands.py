"""Create commands and index the loop lookup automation runs.

Revision ID: 20260730_0010
Revises: 20260730_0009
Create Date: 2026-07-30

The unique key on ``idempotency_key`` is the concurrency guarantee of M3: two
transactions evaluating one trigger sample can both decide, but only one can
insert. The index on ``control_loops.measurement_point_id`` serves the lookup
automation performs for every accepted current sample.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0010"
down_revision: str | Sequence[str] | None = "20260730_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEASUREMENT_POINT_INDEX_NAME: str = "ix_control_loops_measurement_point_id"


def upgrade() -> None:
    """Create applied commands and the index behind the automation lookup."""
    op.create_table(
        "commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("control_loop_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_sample_id", sa.Uuid(), nullable=False),
        sa.Column("target_point_id", sa.Uuid(), nullable=False),
        sa.Column("desired_value", sa.Boolean(), nullable=False),
        sa.Column("result_control_sample_id", sa.Uuid(), nullable=False),
        sa.Column("result_status_sample_id", sa.Uuid(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_loop_id"],
            ["control_loops.id"],
            name=op.f("fk_commands_control_loop_id_control_loops"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_sample_id"],
            ["telemetry_samples.id"],
            name=op.f("fk_commands_trigger_sample_id_telemetry_samples"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_point_id"],
            ["points.id"],
            name=op.f("fk_commands_target_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_control_sample_id"],
            ["telemetry_samples.id"],
            name=op.f("fk_commands_result_control_sample_id_telemetry_samples"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_status_sample_id"],
            ["telemetry_samples.id"],
            name=op.f("fk_commands_result_status_sample_id_telemetry_samples"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commands")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_commands_idempotency_key")),
    )
    op.create_index(
        MEASUREMENT_POINT_INDEX_NAME,
        "control_loops",
        ["measurement_point_id"],
    )


def downgrade() -> None:
    """Remove the automation lookup index and the command table."""
    op.drop_index(MEASUREMENT_POINT_INDEX_NAME, table_name="control_loops")
    op.drop_table("commands")
