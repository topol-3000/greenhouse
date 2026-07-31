"""Remove the simulation domain from the schema.

Revision ID: 20260731_0015
Revises: 20260731_0014
Create Date: 2026-07-31

Executable environment simulation left the cloud: it belongs to the independent
Simulation Lab, which is an ordinary Edge client of the public v1 contract. The
schema therefore keeps no ``simulation_runs`` table and no simulation-specific
relationship on telemetry.

What this migration removes is exactly the objects created by
``20260729_0008`` plus the column ``20260729_0007`` created for it. Nothing else
is touched, and in particular **no row of measured data is deleted**:
``telemetry_samples`` loses one nullable column and keeps every sample, its
value, its unit and both of its instants. ``point_current_states``, ``commands``
and the topology and gateway configuration are not addressed at all — telemetry
produced by a simulator is still telemetry, and a command it once triggered is
still command history.

The downgrade rebuilds the structures, not the data. A dropped run cannot be
recovered from rows that never referenced it, so the reconstructed column is
``NULL`` for every existing sample, which is the value a non-simulated sample
carried under the old schema anyway.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0015"
down_revision: str | Sequence[str] | None = "20260731_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNNING_ZONE_INDEX_NAME: str = "uq_simulation_runs_running_control_zone_id"
TELEMETRY_RUN_FK_NAME: str = "fk_telemetry_samples_simulation_run_id_simulation_runs"


def upgrade() -> None:
    """Drop the simulation link, the run table and its telemetry column."""
    op.drop_constraint(
        TELEMETRY_RUN_FK_NAME,
        "telemetry_samples",
        type_="foreignkey",
    )
    op.drop_column("telemetry_samples", "simulation_run_id")
    op.drop_index(RUNNING_ZONE_INDEX_NAME, table_name="simulation_runs")
    op.drop_table("simulation_runs")


def downgrade() -> None:
    """Recreate the removed structures, empty, in their original shape."""
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
    op.add_column(
        "telemetry_samples",
        sa.Column("simulation_run_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        TELEMETRY_RUN_FK_NAME,
        "telemetry_samples",
        "simulation_runs",
        ["simulation_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
