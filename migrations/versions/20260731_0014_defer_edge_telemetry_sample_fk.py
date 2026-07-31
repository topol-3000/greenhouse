"""Repair the Edge telemetry-message sample foreign key deferral.

Revision ID: 20260731_0014
Revises: 20260730_0013
Create Date: 2026-07-31

``edge_telemetry_messages`` claims a producer message before the shared
telemetry ingestion path persists its sample.  Both writes belong to the same
request transaction, so the relationship must be checked at commit time rather
than at the ledger insert.  The original migration and ORM model specify that
behaviour, but databases created with the earlier deployed constraint have it
as immediate.  Recreate the constraint to repair those deployments.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0014"
down_revision: str | Sequence[str] | None = "20260730_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME: str = "fk_edge_telemetry_messages_sample_id_telemetry_samples"


def upgrade() -> None:
    """Ensure the ledger-to-sample foreign key is checked at commit time."""
    op.drop_constraint(CONSTRAINT_NAME, "edge_telemetry_messages", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT_NAME,
        "edge_telemetry_messages",
        "telemetry_samples",
        ["sample_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    """Keep the setting declared by the preceding migration unchanged."""
