"""ORM model and lifecycle vocabulary of the simulation module."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.infrastructure.database.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)

MODEL_VERSION_V1: str = "simple-climate-v1"
"""The frozen first model. Runs created before M4 carry it and keep its formula."""

MODEL_VERSION_V2: str = "simple-climate-v2"
"""The model whose temperature target follows the zone's logical fan state."""

CURRENT_MODEL_VERSION: str = MODEL_VERSION_V2
"""Version every new run is created with.

Server-owned rather than requested: the parameter snapshot and the formula
belong to the version, so a client that could name one would be choosing
constants it does not send.
"""

MAX_MODEL_VERSION_LENGTH: int = 64
MAX_FAILURE_REASON_LENGTH: int = 500
RUNNING_ZONE_INDEX_NAME: str = "uq_simulation_runs_running_control_zone_id"


class SimulationStatus(StrEnum):
    """Lifecycle of a one-shot simulation run."""

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class SimulationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One persisted configuration and lifecycle of the climate model."""

    __tablename__ = "simulation_runs"
    __table_args__ = (
        Index(
            RUNNING_ZONE_INDEX_NAME,
            "control_zone_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    control_zone_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_zones.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[SimulationStatus] = enum_column(
        SimulationStatus,
        constraint_name="status",
        nullable=False,
        default=SimulationStatus.CREATED,
    )
    model_version: Mapped[str] = mapped_column(
        String(MAX_MODEL_VERSION_LENGTH),
        nullable=False,
        default=CURRENT_MODEL_VERSION,
    )
    speed_multiplier: Mapped[int] = mapped_column(Integer(), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
    )
    virtual_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    step_index: Mapped[int] = mapped_column(BigInteger(), nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String(MAX_FAILURE_REASON_LENGTH),
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation of the run."""
        return (
            f"SimulationRun(id={self.id!r}, control_zone_id={self.control_zone_id!r}, "
            f"status={self.status!r})"
        )
