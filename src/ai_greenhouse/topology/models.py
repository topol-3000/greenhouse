"""ORM models of the topology module."""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import (
    DEFAULT_TIMEZONE,
    MAX_CODE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TIMEZONE_LENGTH,
)
from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)


class Site(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A physical location and the root of the topology.

    A site is never deleted; ``status`` moves to ``archived`` instead. The
    ``code`` is unique across the whole installation because Milestone 1 has no
    organization above the site to scope it to.

    Attributes:
        name: Human-readable label, 1-200 characters after stripping.
        code: Stable slug, unique across all sites and immutable after creation.
        timezone: IANA timezone name the site's local time is expressed in.
    """

    __tablename__ = "sites"

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    code: Mapped[str] = mapped_column(
        String(MAX_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(MAX_TIMEZONE_LENGTH),
        nullable=False,
        default=DEFAULT_TIMEZONE,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Site(id={self.id!r}, code={self.code!r})"


class FacilityType(StrEnum):
    """Kind of growing or infrastructure object a facility represents."""

    GROWBOX = "growbox"
    GREENHOUSE = "greenhouse"
    RACK = "rack"
    SEEDLING_ROOM = "seedling_room"
    UTILITY = "utility"


class Facility(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A growing or infrastructure object inside a site.

    A facility belongs to exactly one site for its whole life. Moving one to
    another site is a separate administrative operation that has to migrate the
    dependent zones and points, so ``site_id`` is never changed through the API.

    Like a site, a facility is retired by moving ``status`` to ``archived``
    rather than by being deleted; the foreign key uses ``ON DELETE RESTRICT`` so
    a site cannot be removed underneath it at the database level either.

    Attributes:
        site_id: Owning site, fixed at creation.
        name: Human-readable label, 1-200 characters after stripping.
        code: Stable slug, unique within the site and immutable after creation.
        facility_type: Kind of object, from :class:`FacilityType`.
    """

    __tablename__ = "facilities"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_facilities_site_id_code"),)

    site_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    code: Mapped[str] = mapped_column(String(MAX_CODE_LENGTH), nullable=False)
    facility_type: Mapped[FacilityType] = enum_column(
        FacilityType,
        constraint_name="facility_type",
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Facility(id={self.id!r}, site_id={self.site_id!r}, code={self.code!r})"
