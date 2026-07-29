"""Create the facilities table.

Revision ID: 20260728_0003
Revises: 20260727_0002
Create Date: 2026-07-28

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entity rather than in mixin resolution order.
``facility_type`` and ``status`` are stored as ``VARCHAR`` with a ``CHECK``
constraint, following the shared enum convention; the constraint and index
names come from the metadata naming convention.

The foreign key uses ``ON DELETE RESTRICT``: a site that still has facilities
cannot be removed, not even by a direct ``DELETE`` against the database.

Uniqueness of ``code`` is scoped to ``site_id``. Two sites may each hold a
facility called ``basil-growbox``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0003"
down_revision: str | Sequence[str] | None = "20260727_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``facilities`` with its per-site unique code and filter indexes."""
    op.create_table(
        "facilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=63), nullable=False),
        sa.Column(
            "facility_type",
            sa.Enum(
                "growbox",
                "greenhouse",
                "rack",
                "seedling_room",
                "utility",
                name="facility_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_facilities_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facilities")),
        sa.UniqueConstraint("site_id", "code", name="uq_facilities_site_id_code"),
    )
    op.create_index(
        op.f("ix_facilities_facility_type"),
        "facilities",
        ["facility_type"],
        unique=False,
    )
    op.create_index(op.f("ix_facilities_site_id"), "facilities", ["site_id"], unique=False)
    op.create_index(op.f("ix_facilities_status"), "facilities", ["status"], unique=False)


def downgrade() -> None:
    """Drop ``facilities`` and its three indexes."""
    op.drop_index(op.f("ix_facilities_status"), table_name="facilities")
    op.drop_index(op.f("ix_facilities_site_id"), table_name="facilities")
    op.drop_index(op.f("ix_facilities_facility_type"), table_name="facilities")
    op.drop_table("facilities")
