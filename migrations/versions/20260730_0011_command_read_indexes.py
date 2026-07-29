"""Index the two queries the command list endpoint serves.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30

Separate from the table's own migration because these indexes exist for a read
that did not exist until now. ``(control_loop_id, created_at DESC, id DESC)`` is
the ordered window itself; ``trigger_sample_id`` answers what one measurement
caused.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0011"
down_revision: str | Sequence[str] | None = "20260730_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HISTORY_INDEX_NAME: str = "ix_commands_control_loop_id_created_at_id"
TRIGGER_INDEX_NAME: str = "ix_commands_trigger_sample_id"


def upgrade() -> None:
    """Create both read indexes on the command table."""
    op.create_index(
        HISTORY_INDEX_NAME,
        "commands",
        ["control_loop_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(TRIGGER_INDEX_NAME, "commands", ["trigger_sample_id"])


def downgrade() -> None:
    """Remove both read indexes."""
    op.drop_index(TRIGGER_INDEX_NAME, table_name="commands")
    op.drop_index(HISTORY_INDEX_NAME, table_name="commands")
