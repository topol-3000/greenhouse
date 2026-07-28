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


class ZoneType(StrEnum):
    """Aspect of a facility a control zone measures or controls."""

    CLIMATE = "climate"
    IRRIGATION = "irrigation"
    LIGHTING = "lighting"
    MEASUREMENT = "measurement"
    NUTRIENT_SOLUTION = "nutrient_solution"
    SAFETY = "safety"


class ControlZone(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A part of a facility that is measured or controlled as one unit.

    A zone is the boundary of *control*, not of physical space: the physical
    sub-structure is ``Area``, which is deliberately out of scope until a later
    milestone. Zones may therefore overlap freely. A climate zone, a lighting
    zone and an irrigation zone can cover the same shelf, and two zones of the
    same type in one facility are allowed as well — the domain resolves such an
    overlap with priority and policy, not by forbidding it. This is also why
    ``zone_type`` is a column rather than a table per kind of zone.

    There is no ``site_id``. The site is reached through ``facility_id``, which
    is what makes "a zone never crosses a facility boundary" a property of the
    schema instead of a rule the service has to keep checking.

    Attributes:
        facility_id: Owning facility, fixed at creation.
        name: Human-readable label, 1-200 characters after stripping.
        code: Stable slug, unique within the facility and immutable afterwards.
        zone_type: Aspect controlled by the zone, from :class:`ZoneType`.
    """

    __tablename__ = "control_zones"
    __table_args__ = (
        UniqueConstraint("facility_id", "code", name="uq_control_zones_facility_id_code"),
    )

    facility_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("facilities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    code: Mapped[str] = mapped_column(String(MAX_CODE_LENGTH), nullable=False)
    zone_type: Mapped[ZoneType] = enum_column(
        ZoneType,
        constraint_name="zone_type",
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"ControlZone(id={self.id!r}, facility_id={self.facility_id!r}, code={self.code!r})"
