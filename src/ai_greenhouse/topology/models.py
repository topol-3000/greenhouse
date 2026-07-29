"""ORM models of the topology module."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
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
    utc_now,
)


class Site(UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin, Base):
    """A physical location and the root of the topology.

    A site is never deleted; ``status`` moves to ``archived`` instead. The
    ``code`` is unique across the whole installation because there is no
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
    sub-structure is ``Area``, which is deliberately out of scope. Zones may
    therefore overlap freely. A climate zone, a lighting zone and an irrigation
    zone can cover the same shelf, and two zones of the same type in one facility
    are allowed as well — the domain resolves such an overlap with priority and
    policy, not by forbidding it. This is also why
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


class ZonePointRole(StrEnum):
    """Part a point plays inside the zone it is assigned to.

    The role is a property of the *link*, not of the point: the same air
    temperature point can be the primary measurement of a climate zone and a
    safety interlock of a safety zone at the same time.
    """

    PRIMARY_MEASUREMENT = "primary_measurement"
    SECONDARY_MEASUREMENT = "secondary_measurement"
    CONTROL_OUTPUT = "control_output"
    STATUS_FEEDBACK = "status_feedback"
    SAFETY_INTERLOCK = "safety_interlock"
    DERIVED_INDICATOR = "derived_indicator"


class ZonePointAssignment(UUIDPrimaryKeyMixin, Base):
    """The link that says a point takes part in a zone, and in what part.

    This is what turns three independent tables into one growbox description,
    and it is what a control loop will read to know which point is the process
    variable and which one is the actuator output.

    It is the one entity that is really deleted rather than archived. An
    assignment is a statement about the current composition of a zone, not a
    record with a history: removing a point from a zone does not retire the
    point, and an ``archived`` link would mean nothing that its absence does not
    already say.

    There is no ``updated_at`` and no ``status``. Nothing about an existing link
    can change — a different role is a different link — so a modification
    instant would never be written, and there is no ``effective_from`` or
    ``effective_to`` either: the history of a zone's composition is deliberately
    out of scope until something needs to read it.

    Attributes:
        control_zone_id: The zone the point takes part in.
        point_id: The point being assigned. Held by identifier and not by an
            ORM relationship, because the point belongs to another aggregate.
        role: Part the point plays in the zone, from :class:`ZonePointRole`.
            Which roles suit which kind of point is a business rule and lives
            in the service, not in this table.
        created_at: Instant the link was made.
    """

    __tablename__ = "zone_point_assignments"
    __table_args__ = (
        UniqueConstraint(
            "control_zone_id",
            "point_id",
            "role",
            name="uq_zone_point_assignments_control_zone_id_point_id_role",
        ),
    )

    control_zone_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("control_zones.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    point_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("points.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[ZonePointRole] = enum_column(
        ZonePointRole,
        constraint_name="role",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"ZonePointAssignment(id={self.id!r}, "
            f"control_zone_id={self.control_zone_id!r}, "
            f"point_id={self.point_id!r}, role={self.role!r})"
        )
