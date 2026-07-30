"""Add stable provisioning codes to Edge gateways.

Revision ID: 20260730_0013
Revises: 20260730_0012
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0013"
down_revision: str | Sequence[str] | None = "20260730_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a globally unique stable code, including existing gateway rows."""
    op.add_column("gateways", sa.Column("code", sa.String(length=63), nullable=True))
    op.execute("UPDATE gateways SET code = 'gateway-' || CAST(id AS VARCHAR)")
    op.alter_column("gateways", "code", nullable=False)
    op.create_index(op.f("ix_gateways_code"), "gateways", ["code"], unique=True)


def downgrade() -> None:
    """Remove the provisioning code while preserving operational UUIDs."""
    op.drop_index(op.f("ix_gateways_code"), table_name="gateways")
    op.drop_column("gateways", "code")
