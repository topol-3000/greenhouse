"""Add the explicit control/status relationship and the manual command source.

Revision ID: 20260803_0020
Revises: 20260803_0019
Create Date: 2026-08-03

Two changes, and one of them carries data.

``points.reported_point_id`` is the persisted answer to "which point reports
this control point's actual state back". It is a self-referencing foreign key
because a status point is an ordinary point, and it is the *only* answer: no
code, name, unit or metric is ever consulted for it. Two check constraints hold
the half of the rule that lives in one row — only a control point may carry the
relationship, and no point reports itself — while the rest, which spans two
rows, stays in the service.

``commands`` learns who asked for it. ``source`` discriminates the control loop
from a customer, ``control_zone_id`` is recorded on the command itself because a
manual command has no loop to reach a zone through, and ``control_loop_id`` and
``trigger_sample_id`` become nullable because a manual command has neither. A
check constraint pairs the source with the references it is allowed to have, so
a manual row can never claim a decision nobody made.

Data
----
Every existing command is a control-loop command: this revision is what
introduces the other kind. ``source`` is backfilled to ``control_loop`` and
``control_zone_id`` from the command's own loop through
``control_loops.control_zone_id``. Neither reads a name, a code or an ordering,
and neither can be ambiguous: ``control_loop_id`` was ``NOT NULL`` before this
revision and a loop has exactly one zone. Both columns are added nullable,
filled, and only then made ``NOT NULL``, so the backfill never fights a
constraint.

``points.reported_point_id`` is deliberately *not* backfilled from
``control_loops``. A loop names a control point and a status point, which looks
like the same relationship, and for a loop that exists it is — but the
relationship is public configuration a client is about to command through, and
inferring it for some control points while leaving others unconfigured would
publish a rule that is true by accident. It starts ``NULL`` everywhere and is
established explicitly, which is also what a control point that never had a loop
has to do.

The downgrade restores the previous shape. It can only run on a database whose
commands are all control-loop commands, because the columns it drops are the
only place a manual one is representable; a manual command has no
``control_loop_id`` to put back, so the downgrade refuses rather than inventing
one or deleting the row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0020"
down_revision: str | Sequence[str] | None = "20260803_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORTED_POINT_INDEX: str = "ix_points_reported_point_id"
REPORTED_POINT_FK: str = "fk_points_reported_point_id_points"
REPORTED_POINT_KIND_CHECK: str = "ck_points_reported_point_requires_control_kind"
REPORTED_POINT_SELF_CHECK: str = "ck_points_reported_point_id_not_self"

COMMAND_ZONE_FK: str = "fk_commands_control_zone_id_control_zones"
COMMAND_ZONE_INDEX: str = "ix_commands_control_zone_id_created_at_id"
COMMAND_TARGET_POINT_INDEX: str = "ix_commands_target_point_id"
COMMAND_SOURCE_CHECK: str = "ck_commands_source_relationships"
COMMAND_SOURCE_ENUM_CHECK: str = "ck_commands_source"

COMMAND_SOURCE_RELATIONSHIPS: str = (
    "(source = 'control_loop'"
    " AND control_loop_id IS NOT NULL AND trigger_sample_id IS NOT NULL)"
    " OR (source = 'manual'"
    " AND control_loop_id IS NULL AND trigger_sample_id IS NULL)"
)
"""A command has exactly the references its source is allowed to have."""

MANUAL_COMMANDS_EXIST: str = "SELECT count(*) FROM commands WHERE source = 'manual'"
"""What the downgrade cannot represent, and therefore checks for first."""

COMMAND_SOURCE_TYPE: sa.Enum = sa.Enum(
    "control_loop",
    "manual",
    name="source",
    native_enum=False,
    create_constraint=False,
)
"""The project's enum storage: ``VARCHAR`` plus a ``CHECK``, never a native type.

``create_constraint`` is off because the constraint is created explicitly below,
after the backfill. Emitting it with the column would have it checked against
rows that have not been given a source yet.
"""


def upgrade() -> None:
    """Add the relationship, then the command source, backfilling both."""
    op.add_column("points", sa.Column("reported_point_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        REPORTED_POINT_FK,
        "points",
        "points",
        ["reported_point_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(REPORTED_POINT_INDEX, "points", ["reported_point_id"])
    op.create_check_constraint(
        op.f(REPORTED_POINT_KIND_CHECK),
        "points",
        "reported_point_id IS NULL OR point_kind = 'control'",
    )
    op.create_check_constraint(
        op.f(REPORTED_POINT_SELF_CHECK),
        "points",
        "reported_point_id IS NULL OR reported_point_id <> id",
    )

    op.add_column("commands", sa.Column("source", COMMAND_SOURCE_TYPE, nullable=True))
    op.add_column("commands", sa.Column("control_zone_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE commands SET source = 'control_loop' WHERE source IS NULL")
    op.execute(
        "UPDATE commands SET control_zone_id = control_loops.control_zone_id"
        " FROM control_loops WHERE control_loops.id = commands.control_loop_id"
        " AND commands.control_zone_id IS NULL"
    )
    op.alter_column("commands", "source", existing_type=COMMAND_SOURCE_TYPE, nullable=False)
    op.alter_column("commands", "control_zone_id", existing_type=sa.Uuid(), nullable=False)

    op.create_check_constraint(
        op.f(COMMAND_SOURCE_ENUM_CHECK),
        "commands",
        "source IN ('control_loop', 'manual')",
    )
    op.create_foreign_key(
        COMMAND_ZONE_FK,
        "commands",
        "control_zones",
        ["control_zone_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("commands", "control_loop_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("commands", "trigger_sample_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        op.f(COMMAND_SOURCE_CHECK),
        "commands",
        COMMAND_SOURCE_RELATIONSHIPS,
    )
    op.create_index(
        COMMAND_ZONE_INDEX,
        "commands",
        ["control_zone_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
    )
    op.create_index(COMMAND_TARGET_POINT_INDEX, "commands", ["target_point_id"])


def downgrade() -> None:
    """Restore the control-loop-only command shape and drop the relationship.

    Raises:
        RuntimeError: If any manual command exists. The previous schema has no
            way to hold one, and deleting a customer's command or inventing a
            control loop for it would both be worse than refusing.
    """
    manual: int = int(op.get_bind().scalar(sa.text(MANUAL_COMMANDS_EXIST)) or 0)
    if manual:
        raise RuntimeError(
            f"{manual} manual command(s) exist and cannot be represented before"
            " 20260803_0020; remove them deliberately before downgrading."
        )

    op.drop_index(COMMAND_TARGET_POINT_INDEX, table_name="commands")
    op.drop_index(COMMAND_ZONE_INDEX, table_name="commands")
    op.drop_constraint(op.f(COMMAND_SOURCE_CHECK), "commands", type_="check")
    op.alter_column("commands", "trigger_sample_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("commands", "control_loop_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint(COMMAND_ZONE_FK, "commands", type_="foreignkey")
    op.drop_constraint(op.f(COMMAND_SOURCE_ENUM_CHECK), "commands", type_="check")
    op.drop_column("commands", "control_zone_id")
    op.drop_column("commands", "source")

    op.drop_constraint(op.f(REPORTED_POINT_SELF_CHECK), "points", type_="check")
    op.drop_constraint(op.f(REPORTED_POINT_KIND_CHECK), "points", type_="check")
    op.drop_index(REPORTED_POINT_INDEX, table_name="points")
    op.drop_constraint(REPORTED_POINT_FK, "points", type_="foreignkey")
    op.drop_column("points", "reported_point_id")
