"""ORM models of the control module.

Control loops are immutable configuration. Commands preserve immutable decision
content while their delivery state may move once from ``pending`` to a terminal
``applied`` or ``rejected`` acknowledgement.

The policy is embedded rather than referenced. ``policy_type`` names
``hysteresis-v1`` and the two thresholds remain its legacy compatibility source;
an active RuntimeTarget may supply the effective bounds without changing the
loop. A separate policy table would add an entity, a version and an assignment
before anything needs to address a policy on its own.

A command carries only the v1 pull-delivery lifecycle. There are no attempts,
leases, expiry, push delivery, or broker concepts.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Uuid, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.infrastructure.database.base import (
    Base,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_now,
)

MAX_IDEMPOTENCY_KEY_LENGTH: int = 200
"""Bound on the derived key.

``hysteresis-v1``, two UUIDs and a decision come to 91 characters. The column
is bounded rather than unbounded because it is unique and therefore indexed,
and an unbounded unique text column is a size nothing controls.
"""

COMMAND_HISTORY_INDEX_NAME: str = "ix_commands_control_loop_id_created_at_id"
"""The command-list read of one loop, newest first, named after its query.

``(control_loop_id, created_at DESC, id DESC)`` is the ordered window the list
endpoint returns, with ``id`` breaking ties between commands written in one
instant. No standalone index on ``control_loop_id`` accompanies it: that index
would be a prefix of this one.
"""

COMMAND_TRIGGER_INDEX_NAME: str = "ix_commands_trigger_sample_id"
"""The other query the list endpoint serves: what one measurement caused."""

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
    """Delivery lifecycle of a logical command."""

    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


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
    """One logical fan state change decided by a control loop.

    Gateway-owned points produce a pending command whose terminal acknowledgement
    never manufactures telemetry. Points without a gateway preserve the
    in-process loopback path, where the applied command and its two result
    samples are atomic.

    The command names a logical point, never a device, an address or a
    protocol. That is what lets the loopback adapter of M3 be replaced by an
    Edge adapter without this table changing.

    The primary key is derived rather than generated, from the same inputs as
    :attr:`idempotency_key`. Re-processing one trigger therefore rebuilds the
    same identifier — and, through it, the same two result-sample identifiers —
    so a replay collides with the row it would duplicate instead of writing a
    second set of samples beside it.

    Attributes:
        id: Primary key, derived from the loop, the trigger and the decision.
        idempotency_key: The decision in one stable string. Unique, and the
            whole of what keeps two concurrent evaluations of one sample from
            both acting.
        control_loop_id: The loop whose effective bounds produced the decision.
        runtime_target_id: The active target whose snapshot bounds produced the
            decision, or ``NULL`` when legacy loop thresholds did. ``ON DELETE
            RESTRICT`` preserves historical provenance after target closure.
        trigger_sample_id: The temperature sample that caused it. ``ON DELETE
            RESTRICT``, like every other reference into the append-only stream.
        target_point_id: The ``fan_power`` point the command was addressed to.
        desired_value: The state asked for. ``True`` is on.
        result_control_sample_id: The sample recording ``desired_value`` on the
            control point.
        result_status_sample_id: The sample recording the value the actuator
            reported back on the status point.
        executed_at: Instant the actuator applied the command, and the
            ``observed_at`` of both result samples.
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
        Index(COMMAND_TRIGGER_INDEX_NAME, "trigger_sample_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(MAX_IDEMPOTENCY_KEY_LENGTH),
        nullable=False,
        unique=True,
    )
    control_loop_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_loops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    runtime_target_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("runtime_targets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    trigger_sample_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("telemetry_samples.id", ondelete="RESTRICT"),
        nullable=False,
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
