"""Persistence private to the public Edge adapter."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.infrastructure.database.base import Base


class EdgeTelemetryMessage(Base):
    """Producer message identity and immutable canonical content."""

    __tablename__ = "edge_telemetry_messages"
    __table_args__ = (UniqueConstraint("sample_id", name="uq_edge_telemetry_messages_sample_id"),)

    gateway_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("gateways.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    message_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    sample_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(
            "telemetry_samples.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    content: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
