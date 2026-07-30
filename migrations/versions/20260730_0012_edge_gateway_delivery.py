"""Add gateway ownership, public message idempotency and command delivery.

Revision ID: 20260730_0012
Revises: 20260730_0011
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0012"
down_revision: str | Sequence[str] | None = "20260730_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the v1 cloud-side Edge boundary without losing command history."""
    op.create_table(
        "gateways",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["sites.id"],
            name=op.f("fk_gateways_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gateways")),
    )
    op.create_index(op.f("ix_gateways_site_id"), "gateways", ["site_id"])
    op.create_index(op.f("ix_gateways_status"), "gateways", ["status"])

    op.create_table(
        "gateway_points",
        sa.Column("gateway_id", sa.Uuid(), nullable=False),
        sa.Column("point_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["gateways.id"],
            name=op.f("fk_gateway_points_gateway_id_gateways"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["point_id"],
            ["points.id"],
            name=op.f("fk_gateway_points_point_id_points"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "gateway_id",
            "point_id",
            name=op.f("pk_gateway_points"),
        ),
        sa.UniqueConstraint("point_id", name="uq_gateway_points_point_id"),
    )

    op.add_column("commands", sa.Column("reported_point_id", sa.Uuid(), nullable=True))
    op.add_column("commands", sa.Column("gateway_id", sa.Uuid(), nullable=True))
    op.add_column(
        "commands",
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "applied",
                "rejected",
                name="state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
    )
    op.add_column("commands", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "commands",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "commands",
        sa.Column(
            "rejection_reason",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE commands
        SET reported_point_id = control_loops.status_point_id,
            state = 'applied',
            issued_at = commands.created_at,
            acknowledged_at = commands.executed_at
        FROM control_loops
        WHERE commands.control_loop_id = control_loops.id
        """
    )
    op.alter_column("commands", "reported_point_id", nullable=False)
    op.alter_column("commands", "state", nullable=False)
    op.alter_column("commands", "issued_at", nullable=False)
    op.alter_column("commands", "result_control_sample_id", nullable=True)
    op.alter_column("commands", "result_status_sample_id", nullable=True)
    op.alter_column("commands", "executed_at", nullable=True)
    op.create_foreign_key(
        op.f("fk_commands_reported_point_id_points"),
        "commands",
        "points",
        ["reported_point_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_commands_gateway_id_gateways"),
        "commands",
        "gateways",
        ["gateway_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_commands_gateway_id"), "commands", ["gateway_id"])

    op.create_table(
        "edge_telemetry_messages",
        sa.Column("gateway_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("sample_id", sa.Uuid(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["gateways.id"],
            name=op.f("fk_edge_telemetry_messages_gateway_id_gateways"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sample_id"],
            ["telemetry_samples.id"],
            name=op.f("fk_edge_telemetry_messages_sample_id_telemetry_samples"),
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "gateway_id",
            "message_id",
            name=op.f("pk_edge_telemetry_messages"),
        ),
        sa.UniqueConstraint(
            "sample_id",
            name="uq_edge_telemetry_messages_sample_id",
        ),
    )


def downgrade() -> None:
    """Remove the public adapter while restoring the original applied schema."""
    op.drop_table("edge_telemetry_messages")
    op.drop_index(op.f("ix_commands_gateway_id"), table_name="commands")
    op.drop_constraint(
        op.f("fk_commands_gateway_id_gateways"),
        "commands",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_commands_reported_point_id_points"),
        "commands",
        type_="foreignkey",
    )
    op.alter_column("commands", "executed_at", nullable=False)
    op.alter_column("commands", "result_status_sample_id", nullable=False)
    op.alter_column("commands", "result_control_sample_id", nullable=False)
    op.drop_column("commands", "rejection_reason")
    op.drop_column("commands", "acknowledged_at")
    op.drop_column("commands", "issued_at")
    op.drop_column("commands", "state")
    op.drop_column("commands", "gateway_id")
    op.drop_column("commands", "reported_point_id")
    op.drop_table("gateway_points")
    op.drop_index(op.f("ix_gateways_status"), table_name="gateways")
    op.drop_index(op.f("ix_gateways_site_id"), table_name="gateways")
    op.drop_table("gateways")
