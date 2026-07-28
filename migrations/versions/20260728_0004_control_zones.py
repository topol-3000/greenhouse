"""Create the control_zones table.

Revision ID: 20260728_0004
Revises: 20260728_0003
Create Date: 2026-07-28

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. Columns
are ordered as declared on the entity rather than in mixin resolution order.
``zone_type`` and ``status`` are stored as ``VARCHAR`` with a ``CHECK``
constraint, following the convention set in Milestone 1.1; the constraint and
index names come from the metadata naming convention.

The table has no ``site_id``. The site is reached through ``facility_id``, which
is what makes "a control zone never crosses a facility boundary" a property of
the schema rather than a rule the application has to re-check.

The foreign key uses ``ON DELETE RESTRICT``: a facility that still has zones
cannot be removed, not even by a direct ``DELETE`` against the database.

Uniqueness of ``code`` is scoped to ``facility_id``. Two facilities may each
hold a zone called ``main-climate``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0004"
down_revision: str | Sequence[str] | None = "20260728_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``control_zones`` with its per-facility unique code and indexes."""
    op.create_table(
        "control_zones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=63), nullable=False),
        sa.Column(
            "zone_type",
            sa.Enum(
                "climate",
                "irrigation",
                "lighting",
                "measurement",
                "nutrient_solution",
                "safety",
                name="zone_type",
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
            ["facility_id"],
            ["facilities.id"],
            name=op.f("fk_control_zones_facility_id_facilities"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_control_zones")),
        sa.UniqueConstraint("facility_id", "code", name="uq_control_zones_facility_id_code"),
    )
    op.create_index(
        op.f("ix_control_zones_facility_id"),
        "control_zones",
        ["facility_id"],
        unique=False,
    )
    op.create_index(op.f("ix_control_zones_status"), "control_zones", ["status"], unique=False)
    op.create_index(
        op.f("ix_control_zones_zone_type"),
        "control_zones",
        ["zone_type"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``control_zones`` and its three indexes."""
    op.drop_index(op.f("ix_control_zones_zone_type"), table_name="control_zones")
    op.drop_index(op.f("ix_control_zones_status"), table_name="control_zones")
    op.drop_index(op.f("ix_control_zones_facility_id"), table_name="control_zones")
    op.drop_table("control_zones")
