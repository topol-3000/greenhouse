"""Create simulation_runs and connect simulated telemetry.

Revision ID: 20260729_0008
Revises: 20260729_0007
Create Date: 2026-07-29

The partial unique index serves the concrete lookup for a running run in one
control zone and closes the race between two concurrent transitions to
``running``. Created and terminal runs do not enter that index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0008"
down_revision: str | Sequence[str] | None = "20260729_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNNING_ZONE_INDEX_NAME: str = "uq_simulation_runs_running_control_zone_id"
TELEMETRY_RUN_FK_NAME: str = "fk_telemetry_samples_simulation_run_id_simulation_runs"


def upgrade() -> None:
    """Create persisted runs and attach their telemetry foreign key."""
    op.create_table(
        "simulation_runs",
        sa.Column("control_zone_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "running",
                "stopped",
                "failed",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("speed_multiplier", sa.Integer(), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("virtual_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("step_index", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_zone_id"],
            ["control_zones.id"],
            name=op.f("fk_simulation_runs_control_zone_id_control_zones"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
    )
    op.create_index(
        RUNNING_ZONE_INDEX_NAME,
        "simulation_runs",
        ["control_zone_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_foreign_key(
        TELEMETRY_RUN_FK_NAME,
        "telemetry_samples",
        "simulation_runs",
        ["simulation_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove the telemetry link, index and run table."""
    op.drop_constraint(
        TELEMETRY_RUN_FK_NAME,
        "telemetry_samples",
        type_="foreignkey",
    )
    op.drop_index(RUNNING_ZONE_INDEX_NAME, table_name="simulation_runs")
    op.drop_table("simulation_runs")
