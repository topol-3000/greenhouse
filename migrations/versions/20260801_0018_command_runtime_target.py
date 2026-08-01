"""Add RuntimeTarget provenance to commands.

Revision ID: 20260801_0018
Revises: 20260801_0017
Create Date: 2026-08-01

The nullable foreign key records which active RuntimeTarget snapshot supplied
the bounds for a hysteresis decision. Existing and legacy-fallback commands
remain valid with ``NULL`` provenance. The Edge transport is unchanged; this is
cloud-owned history only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0018"
down_revision: str | Sequence[str] | None = "20260801_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_TARGET_INDEX_NAME: str = "ix_commands_runtime_target_id"


def upgrade() -> None:
    """Add nullable command provenance without guessing historical sources."""
    op.add_column("commands", sa.Column("runtime_target_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_commands_runtime_target_id_runtime_targets"),
        "commands",
        "runtime_targets",
        ["runtime_target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(RUNTIME_TARGET_INDEX_NAME, "commands", ["runtime_target_id"])


def downgrade() -> None:
    """Return commands to the Unit 2 schema."""
    op.drop_index(RUNTIME_TARGET_INDEX_NAME, table_name="commands")
    op.drop_constraint(
        op.f("fk_commands_runtime_target_id_runtime_targets"),
        "commands",
        type_="foreignkey",
    )
    op.drop_column("commands", "runtime_target_id")
