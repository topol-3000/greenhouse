"""Create the empty schema baseline.

Revision ID: 20260727_0001
Revises:
Create Date: 2026-07-27
"""

from collections.abc import Sequence

revision: str = "20260727_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish the migration chain without domain tables."""


def downgrade() -> None:
    """Remove the empty baseline."""
