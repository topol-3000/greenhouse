"""ORM models of the control module.

Control loops are immutable configuration. Commands preserve immutable decision
content while their delivery state may move once from ``pending`` to a terminal
``applied`` or ``rejected`` acknowledgement.

A command has two possible authors and says which one it had. A control loop
decides one from an accepted measurement; a customer-facing client asks for one
directly. Both are the same fact — one boolean state change addressed to one
logical control point — and both reach the Edge through the same pending row,
so they are one table with a ``source`` discriminator rather than two.

The policy is embedded rather than referenced. ``policy_type`` names
``hysteresis-v1`` and the loop's two immutable thresholds are the only source of
the band it decides on. A separate policy table would add an entity, a version
and an assignment before anything needs to address a policy on its own.

A command carries only the v1 pull-delivery lifecycle. There are no attempts,
leases, expiry, push delivery, or broker concepts.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.infrastructure.database.base import (
    Base,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_now,
)

MAX_IDEMPOTENCY_KEY_LENGTH: int = 200
"""Bound on the key.

``hysteresis-v1``, two UUIDs and a decision come to 91 characters, and a
client-supplied manual key is one UUID. The column is bounded rather than
unbounded because it is unique and therefore indexed, and an unbounded unique
text column is a size nothing controls.
"""

COMMAND_HISTORY_INDEX_NAME: str = "ix_commands_control_loop_id_created_at_id"
"""The command-list read of one loop, newest first, named after its query.

``(control_loop_id, created_at DESC, id DESC)`` is the ordered window the list
endpoint returns, with ``id`` breaking ties between commands written in one
instant. No standalone index on ``control_loop_id`` accompanies it: that index
would be a prefix of this one.
"""

COMMAND_ZONE_INDEX_NAME: str = "ix_commands_control_zone_id_created_at_id"
"""The same ordered window for a customer-facing client, which reads by zone.

A manual command has no control loop, so the loop index cannot serve it. This
one covers both sources and, like it, subsumes a standalone ``control_zone_id``
index.
"""

COMMAND_TARGET_POINT_INDEX_NAME: str = "ix_commands_target_point_id"
"""The list filter that answers "what has been asked of this actuator"."""

COMMAND_TRIGGER_INDEX_NAME: str = "ix_commands_trigger_sample_id"
"""The other query the list endpoint serves: what one measurement caused."""

COMMAND_SOURCE_CONSTRAINT_NAME: str = "source_relationships"
"""Names the rule that keeps a command's source and its references consistent.

A control-loop command has both the loop that decided and the sample it decided
on; a manual command has neither, because inventing either would report a
customer's request as an automatic decision. Expressed in the schema because it
is a property of the row, not of the code path that wrote it.
"""

ZONE_LOOP_CONSTRAINT_NAME: str = "uq_control_loops_control_zone_id"
"""Unique constraint naming the one rule the table enforces on its own.

M3 allows exactly one loop per control zone, and the index behind the
constraint also serves the ``?control_zone_id=`` list filter. Nothing else
queries this table yet, so nothing else is indexed.
"""


class ControlPolicyType(StrEnum):
    """Policy a control loop evaluates.

    One member today. It is an enum rather than a constant string so that the
    stored value carries a ``CHECK`` constraint like every other enum column,
    and so a second policy arrives as a member instead of as a free-text value
    nothing validates.
    """

    HYSTERESIS_V1 = "hysteresis-v1"


class CommandState(StrEnum):
    """Delivery lifecycle of a logical command.

    ``PENDING`` is the only non-terminal state and the one every command is
    written in. ``APPLIED`` is terminal success and ``REJECTED`` is terminal
    failure; both are reached exactly once, through an Edge acknowledgement or
    through the in-process loopback actuator, and never left again.

    Acknowledgement is not a state. A pending command whose ``acknowledged_at``
    is set has been received by the Edge and nothing more: the physical change
    has not been reported either way, so the command is still non-terminal.
    """

    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class CommandSource(StrEnum):
    """Who asked for a command.

    Persisted rather than derived from ``control_loop_id IS NULL``: the source
    is what a reader actually wants to filter and reason about, and a nullable
    foreign key is a poor place to keep a meaning.
    """

    CONTROL_LOOP = "control_loop"
    MANUAL = "manual"


class ControlLoop(UUIDPrimaryKeyMixin, Base):
    """One immutable automation rule binding three points of a climate zone.

    The loop names points, never devices or channels: which hardware serves
    ``control_point_id`` is a question the automation flow never asks, which is
    what lets the loopback adapter be replaced by an Edge adapter without
    touching this table.

    Attributes:
        control_zone_id: The climate zone the loop automates. Unique, because
            M3 allows one loop per zone.
        measurement_point_id: Numeric ``air_temperature`` point whose accepted
            samples trigger evaluation. Indexed: automation looks the loop up by
            this column for every sample that becomes a current state.
        control_point_id: Boolean ``fan_power`` point the command is addressed
            to.
        status_point_id: Boolean ``fan_running`` point the adapter reports back
            on. Never an input of the policy.
        policy_type: The evaluated policy, fixed at ``hysteresis-v1``.
        lower_threshold: Temperature strictly below which the fan is switched
            off, in the measurement point's unit.
        upper_threshold: Temperature strictly above which the fan is switched
            on, in the measurement point's unit.
        created_at: Instant the loop was configured.
    """

    __tablename__ = "control_loops"

    control_zone_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_zones.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    measurement_point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    control_point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status_point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_type: Mapped[ControlPolicyType] = enum_column(
        ControlPolicyType,
        constraint_name="policy_type",
        nullable=False,
        default=ControlPolicyType.HYSTERESIS_V1,
    )
    lower_threshold: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    upper_threshold: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"ControlLoop(id={self.id!r}, control_zone_id={self.control_zone_id!r})"


class Command(Base):
    """One logical boolean state change addressed to one control point.

    A control loop decides one from an accepted measurement, and a
    customer-facing client asks for one directly. :attr:`source` says which, and
    the automatic-only references are null on a manual command rather than
    filled with something that would read as a decision nobody made.

    Gateway-owned points produce a pending command whose terminal acknowledgement
    never manufactures telemetry. Points without a gateway preserve the
    in-process loopback path, where the applied command and its two result
    samples are atomic. A manual command is only accepted for a gateway-owned
    point, so it is always the first of those two.

    The command names a logical point, never a device, an address or a
    protocol. That is what lets the loopback adapter of M3 be replaced by an
    Edge adapter without this table changing.

    A control-loop command's primary key is derived rather than generated, from
    the same inputs as its :attr:`idempotency_key`. Re-processing one trigger
    therefore rebuilds the same identifier — and, through it, the same two
    result-sample identifiers — so a replay collides with the row it would
    duplicate instead of writing a second set of samples beside it. A manual
    command's key is the client's, so its identifier is generated and the unique
    index on the key is what a replay collides with.

    Attributes:
        id: Primary key. Derived from the loop, the trigger and the decision for
            a control-loop command; generated for a manual one.
        source: Who asked for the command, from :class:`CommandSource`.
        idempotency_key: The request in one stable string, unique across both
            sources. Derived as ``policy:loop:sample:action`` for a control-loop
            command, and the UUID the client supplied for a manual one. It is
            the whole of what keeps two concurrent requests for one logical
            action from both acting.
        control_zone_id: The zone the commanded point is assigned to. Recorded
            on the command itself rather than reached through the loop, because
            a manual command has no loop and a customer-facing client reads by
            zone.
        control_loop_id: The loop whose immutable thresholds produced the
            decision. ``NULL`` on a manual command.
        trigger_sample_id: The temperature sample that caused it. ``ON DELETE
            RESTRICT``, like every other reference into the append-only stream.
            ``NULL`` on a manual command.
        target_point_id: The control point the command was addressed to.
        reported_point_id: The status point that reports the resulting state
            back. Copied from the configuration in force when the command was
            created, so a later reconfiguration does not reinterpret it.
        desired_value: The state asked for. ``True`` is on. It is a request and
            never a reading: what the point actually reports lives in the
            telemetry stream and its current-state projection.
        result_control_sample_id: The sample recording ``desired_value`` on the
            control point, written only by the in-process loopback actuator.
        result_status_sample_id: The sample recording the value the actuator
            reported back on the status point, written only by the loopback.
        issued_at: Instant the command was decided or requested.
        executed_at: Instant the in-process actuator applied the command, and
            the ``observed_at`` of both result samples. ``NULL`` for everything
            an Edge gateway carries out.
        acknowledged_at: Instant the Edge reported *receipt*. It does not mean
            the change was physically applied, and a command carrying it is
            still pending until it becomes ``applied`` or ``rejected``.
        rejection_reason: Machine code and message of a terminal rejection.
        created_at: Instant the row was written.
    """

    __tablename__ = "commands"
    __table_args__ = (
        Index(
            COMMAND_HISTORY_INDEX_NAME,
            "control_loop_id",
            desc("created_at"),
            desc("id"),
        ),
        Index(
            COMMAND_ZONE_INDEX_NAME,
            "control_zone_id",
            desc("created_at"),
            desc("id"),
        ),
        Index(COMMAND_TARGET_POINT_INDEX_NAME, "target_point_id"),
        Index(COMMAND_TRIGGER_INDEX_NAME, "trigger_sample_id"),
        CheckConstraint(
            "(source = 'control_loop'"
            " AND control_loop_id IS NOT NULL AND trigger_sample_id IS NOT NULL)"
            " OR (source = 'manual'"
            " AND control_loop_id IS NULL AND trigger_sample_id IS NULL)",
            name=COMMAND_SOURCE_CONSTRAINT_NAME,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    source: Mapped[CommandSource] = enum_column(
        CommandSource,
        constraint_name="source",
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(MAX_IDEMPOTENCY_KEY_LENGTH),
        nullable=False,
        unique=True,
    )
    control_zone_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_zones.id", ondelete="RESTRICT"),
        nullable=False,
    )
    control_loop_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("control_loops.id", ondelete="RESTRICT"),
        nullable=True,
    )
    trigger_sample_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("telemetry_samples.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reported_point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    gateway_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("gateways.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    desired_value: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    state: Mapped[CommandState] = enum_column(
        CommandState,
        constraint_name="state",
        nullable=False,
    )
    result_control_sample_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("telemetry_samples.id", ondelete="RESTRICT"),
        nullable=True,
    )
    result_status_sample_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("telemetry_samples.id", ondelete="RESTRICT"),
        nullable=True,
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[dict[str, str] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Command(id={self.id!r}, idempotency_key={self.idempotency_key!r})"
