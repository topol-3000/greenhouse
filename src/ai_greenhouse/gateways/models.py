"""Persistent gateway identity and logical-point authorization."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import MAX_CODE_LENGTH
from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)


class Gateway(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A stable Edge gateway identity and its provisioning code within one site."""

    __tablename__ = "gateways"

    code: Mapped[str] = mapped_column(
        String(MAX_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    site_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class GatewayPoint(Base):
    """Explicit authorization of one logical point to one gateway.

    A logical point has one gateway owner in v1. This makes command ownership
    unambiguous and prevents two gateways from polling the same command.
    """

    __tablename__ = "gateway_points"
    __table_args__ = (UniqueConstraint("point_id", name="uq_gateway_points_point_id"),)

    gateway_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("gateways.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
