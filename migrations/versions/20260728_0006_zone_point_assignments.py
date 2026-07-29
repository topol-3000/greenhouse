"""Create the zone_point_assignments table.

Revision ID: 20260728_0006
Revises: 20260728_0005
Create Date: 2026-07-28

Drafted with ``alembic revision --autogenerate`` and reviewed by hand. ``role``
is stored as ``VARCHAR`` with a ``CHECK`` constraint, following the shared enum
convention; the constraint and index names come from the metadata naming
convention.

The table has ``created_at`` and no ``updated_at``: nothing about an existing
link can change, so a modification instant would never be written. It has no
``status`` either, because this is the one entity that is really deleted rather
than archived.

``effective_from`` and ``effective_to`` from the domain model are deliberately
absent. Nothing reads the history of a zone's composition, and introducing the
columns now would mean every query has to filter on them for the whole time
nothing writes them.

The composite unique constraint is on ``(control_zone_id, point_id, role)``
rather than on ``(control_zone_id, point_id)``: the same point may take part in
one zone under two different roles — a control point that is both the zone's
output and its safety interlock — and only the exact repetition is a duplicate.

Both foreign keys use ``ON DELETE RESTRICT``: deleting an assignment must not
be a way to reach a zone or a point, and neither can be removed underneath a
link that still refers to it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0006"
down_revision: str | Sequence[str] | None = "20260728_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``zone_point_assignments`` with its indexes and constraints."""
    op.create_table(
        "zone_point_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("control_zone_id", sa.Uuid(), nullable=False),
        sa.Column("point_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "primary_measurement",
                "secondary_measurement",
                "control_output",
                "status_feedback",
                "safety_interlock",
                "derived_indicator",
                name="role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["control_zone_id"],
            ["control_zones.id"],
            name=op.f("fk_zone_point_assignments_control_zone_id_control_zones"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["point_id"],
            ["points.id"],
            name=op.f("fk_zone_point_assignments_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_point_assignments")),
        sa.UniqueConstraint(
            "control_zone_id",
            "point_id",
            "role",
            name="uq_zone_point_assignments_control_zone_id_point_id_role",
        ),
    )
    op.create_index(
        op.f("ix_zone_point_assignments_control_zone_id"),
        "zone_point_assignments",
        ["control_zone_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zone_point_assignments_point_id"),
        "zone_point_assignments",
        ["point_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zone_point_assignments_role"),
        "zone_point_assignments",
        ["role"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the table and its indexes."""
    op.drop_index(op.f("ix_zone_point_assignments_role"), table_name="zone_point_assignments")
    op.drop_index(op.f("ix_zone_point_assignments_point_id"), table_name="zone_point_assignments")
    op.drop_index(
        op.f("ix_zone_point_assignments_control_zone_id"),
        table_name="zone_point_assignments",
    )
    op.drop_table("zone_point_assignments")
