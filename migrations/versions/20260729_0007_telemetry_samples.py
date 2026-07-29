"""Create the telemetry_samples table.

Revision ID: 20260729_0007
Revises: 20260728_0006
Create Date: 2026-07-29

Drafted with ``alembic revision --autogenerate`` and reviewed by hand.
``quality`` is stored as ``VARCHAR`` with a ``CHECK`` constraint, following the
shared enum convention; the constraint and key names come from the metadata
naming convention.

The table carries no ``updated_at`` and no soft-delete flag. Samples are
append-only, and a column that exists only to record a change would be a
standing invitation to make one.

One index is created, and it is the telemetry history read written down:
``(point_id, observed_at DESC, id DESC)`` returns one point's samples newest
first, with ``id`` breaking ties. A standalone index on ``point_id`` is
deliberately **not** created — it would be a prefix of this one, and a foreign
key needs no index of its own when a composite one leads with it.

``simulation_run_id`` is created without a foreign key. ``simulation_runs`` is
created by a later migration, which attaches the ``ON DELETE RESTRICT``
constraint. The column is created here because the rows that carry it are
written here.

``point_id`` uses ``ON DELETE RESTRICT``: no point that has ever been measured
can be deleted out from under its own history, not even by a direct ``DELETE``
against the database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0007"
down_revision: str | Sequence[str] | None = "20260728_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HISTORY_INDEX_NAME: str = "ix_telemetry_samples_point_id_observed_at_id"


def upgrade() -> None:
    """Create ``telemetry_samples`` with its single composite index."""
    op.create_table(
        "telemetry_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("point_id", sa.Uuid(), nullable=False),
        sa.Column("simulation_run_id", sa.Uuid(), nullable=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["point_id"],
            ["points.id"],
            name=op.f("fk_telemetry_samples_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telemetry_samples")),
    )
    op.create_index(
        HISTORY_INDEX_NAME,
        "telemetry_samples",
        ["point_id", sa.text("observed_at DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Drop the index and the table it serves."""
    op.drop_index(HISTORY_INDEX_NAME, table_name="telemetry_samples")
    op.drop_table("telemetry_samples")
