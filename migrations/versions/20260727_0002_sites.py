"""Create the sites table.

Revision ID: 20260727_0002
Revises: 20260727_0001
Create Date: 2026-07-27

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entity rather than in mixin resolution order.
``status`` is stored as ``VARCHAR`` with a ``CHECK`` constraint, following the
shared enum convention; the constraint and index names come from the metadata
naming convention.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0002"
down_revision: str | Sequence[str] | None = "20260727_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``sites`` with its unique code index and status index."""
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=63), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                name="status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
    )
    op.create_index(op.f("ix_sites_code"), "sites", ["code"], unique=True)
    op.create_index(op.f("ix_sites_status"), "sites", ["status"], unique=False)


def downgrade() -> None:
    """Drop ``sites`` and both of its indexes."""
    op.drop_index(op.f("ix_sites_status"), table_name="sites")
    op.drop_index(op.f("ix_sites_code"), table_name="sites")
    op.drop_table("sites")
