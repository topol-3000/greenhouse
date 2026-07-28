"""Request and response schemas of the topology module.

The assignment and configuration schemas name a point's ``point_kind`` and
``data_type``, so this module imports the points module's enums. It imports no
point *behaviour*: what a point means is decided there, and repeated here only
as the vocabulary of a response.
"""

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai_greenhouse.core.types import DEFAULT_TIMEZONE, CodeStr, NameStr, TimezoneStr
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import (
    DataQuality,
    Point,
    PointCurrentState,
    PointDataType,
    PointKind,
)
from ai_greenhouse.topology.models import (
    ControlZone,
    Facility,
    FacilityType,
    Site,
    ZonePointAssignment,
    ZonePointRole,
    ZoneType,
)


class SiteCreate(BaseModel):
    """Body accepted by ``POST /api/v1/sites``."""

    model_config = ConfigDict(extra="forbid")

    name: NameStr
    code: CodeStr
    timezone: TimezoneStr = DEFAULT_TIMEZONE


class SiteUpdate(BaseModel):
    """Body accepted by ``PATCH /api/v1/sites/{site_id}``.

    Only the fields present in the request are applied, so an omitted field and
    an explicit ``null`` both leave the stored value untouched.

    ``code`` is declared even though it cannot be changed. Accepting it here is
    what lets the service answer with HTTP 409 ``immutable_field`` instead of
    the generic HTTP 422 that an unexpected field would produce.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    timezone: TimezoneStr | None = None
    status: StatusEnum | None = None
    code: str | None = None


class SiteRead(BaseModel):
    """Representation of a site returned by every site endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    code: str
    timezone: str
    status: StatusEnum
    created_at: datetime
    updated_at: datetime


class FacilityCreate(BaseModel):
    """Body accepted by ``POST /api/v1/facilities``."""

    model_config = ConfigDict(extra="forbid")

    site_id: UUID
    name: NameStr
    code: CodeStr
    facility_type: FacilityType


class FacilityUpdate(BaseModel):
    """Body accepted by ``PATCH /api/v1/facilities/{facility_id}``.

    Only the fields present in the request are applied, so an omitted field and
    an explicit ``null`` both leave the stored value untouched.

    ``code`` and ``site_id`` are declared even though neither can be changed.
    Accepting them here is what lets the service answer with a precise HTTP 409
    instead of the generic HTTP 422 that an unexpected field would produce.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    facility_type: FacilityType | None = None
    status: StatusEnum | None = None
    code: str | None = None
    site_id: UUID | None = None


class FacilityRead(BaseModel):
    """Representation of a facility returned by every facility endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    site_id: UUID
    name: str
    code: str
    facility_type: FacilityType
    status: StatusEnum
    created_at: datetime
    updated_at: datetime


class ControlZoneCreate(BaseModel):
    """Body accepted by ``POST /api/v1/control-zones``.

    There is no ``site_id``: a zone reaches its site through its facility, and
    accepting a second, redundant parent would make the two contradictable.
    """

    model_config = ConfigDict(extra="forbid")

    facility_id: UUID
    name: NameStr
    code: CodeStr
    zone_type: ZoneType


class ControlZoneUpdate(BaseModel):
    """Body accepted by ``PATCH /api/v1/control-zones/{zone_id}``.

    Only the fields present in the request are applied, so an omitted field and
    an explicit ``null`` both leave the stored value untouched.

    ``code`` and ``facility_id`` are declared even though neither can be
    changed. Accepting them here is what lets the service answer with a precise
    HTTP 409 instead of the generic HTTP 422 that an unexpected field would
    produce.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameStr | None = None
    zone_type: ZoneType | None = None
    status: StatusEnum | None = None
    code: str | None = None
    facility_id: UUID | None = None


class ControlZoneRead(BaseModel):
    """Representation of a control zone returned by every zone endpoint.

    No ``site_id`` is exposed. Clients that need the site read it from the
    zone's facility, which is the only place it is stored.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    facility_id: UUID
    name: str
    code: str
    zone_type: ZoneType
    status: StatusEnum
    created_at: datetime
    updated_at: datetime


class ZonePointAssignmentCreate(BaseModel):
    """Body accepted by ``POST /api/v1/control-zones/{zone_id}/points``.

    There is no ``control_zone_id``: the zone comes from the path, and
    accepting a second, contradictable copy of it in the body would only create
    a way for the two to disagree.
    """

    model_config = ConfigDict(extra="forbid")

    point_id: UUID
    role: ZonePointRole


class ZonePointAssignmentRead(BaseModel):
    """Representation of one zone-point assignment.

    The point's own fields are copied in rather than referenced, because a
    client listing a zone's composition wants to read it without a request per
    link. The copy is deliberately partial: what a point *means* is answered by
    ``GET /api/v1/points/{point_id}``, and duplicating the whole entity here
    would make this endpoint a second, competing source of it.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    control_zone_id: UUID
    point_id: UUID
    role: ZonePointRole
    created_at: datetime
    point_code: str
    point_name: str
    point_kind: PointKind
    data_type: PointDataType
    unit: str | None

    @classmethod
    def from_assignment(cls, assignment: ZonePointAssignment, point: Point) -> Self:
        """Build the enriched representation from the link and its point.

        Args:
            assignment: The stored link.
            point: The point it refers to, already loaded by the caller. It is
                passed in rather than fetched through a relationship so that
                listing a zone cannot turn into one query per assignment.

        Returns:
            The assignment with the point's metadata folded into it.
        """
        return cls(
            id=assignment.id,
            control_zone_id=assignment.control_zone_id,
            point_id=assignment.point_id,
            role=assignment.role,
            created_at=assignment.created_at,
            point_code=point.code,
            point_name=point.name,
            point_kind=point.point_kind,
            data_type=point.data_type,
            unit=point.unit,
        )


class ConfigurationSite(BaseModel):
    """The site of a facility, as it appears in the configuration document."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    code: str
    timezone: str


class ConfigurationFacility(BaseModel):
    """The facility the configuration document describes."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    name: str
    code: str
    facility_type: FacilityType
    status: StatusEnum


class ConfigurationZonePoint(BaseModel):
    """One link inside a zone of the configuration document.

    Only the link is described here. The point's own fields appear once, in the
    document's ``points`` list, so that a point taking part in three zones is
    still described once and cannot be described three inconsistent ways.
    """

    model_config = ConfigDict(frozen=True)

    point_id: UUID
    code: str
    role: ZonePointRole


class ConfigurationZone(BaseModel):
    """One control zone of the configuration document, with its composition."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    code: str
    zone_type: ZoneType
    status: StatusEnum
    points: list[ConfigurationZonePoint]

    @classmethod
    def from_zone(
        cls,
        control_zone: ControlZone,
        points: list[ConfigurationZonePoint],
    ) -> Self:
        """Build a zone entry from the zone and its already-loaded links.

        Args:
            control_zone: The stored zone.
            points: Its assignments, in creation order.

        Returns:
            The zone entry of the configuration document.
        """
        return cls(
            id=control_zone.id,
            name=control_zone.name,
            code=control_zone.code,
            zone_type=control_zone.zone_type,
            status=control_zone.status,
            points=points,
        )


class ConfigurationPointState(BaseModel):
    """The last known state of a point inside the configuration document.

    Throughout Milestone 1 this is ``value = null``, ``quality = "no_data"``
    and ``observed_at = null`` for every point. ``received_at`` and ``revision``
    are left to ``GET /api/v1/points/{point_id}/state``: the configuration
    answers "what is this growbox and what does it read", not "how did this
    value get here".
    """

    model_config = ConfigDict(frozen=True)

    value: Any = None
    quality: DataQuality
    observed_at: datetime | None


class ConfigurationPoint(BaseModel):
    """One point of the configuration document, with its current state."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    code: str
    name: str
    point_kind: PointKind
    metric_type: str
    data_type: PointDataType
    unit: str | None
    status: StatusEnum
    state: ConfigurationPointState

    @classmethod
    def from_point(cls, point: Point, state: PointCurrentState | None) -> Self:
        """Build a point entry from the point and its state projection.

        Args:
            point: The stored point.
            state: Its projection, or ``None`` when the row is missing. A
                missing projection is reported as ``no_data`` rather than as a
                failure: the configuration document describes a growbox, and one
                unreadable point must not make the whole growbox unreadable.

        Returns:
            The point entry of the configuration document.
        """
        return cls(
            id=point.id,
            code=point.code,
            name=point.name,
            point_kind=point.point_kind,
            metric_type=point.metric_type,
            data_type=point.data_type,
            unit=point.unit,
            status=point.status,
            state=ConfigurationPointState(
                value=None if state is None else state.value,
                quality=DataQuality.NO_DATA if state is None else state.quality,
                observed_at=None if state is None else state.observed_at,
            ),
        )


class FacilityConfigurationRead(BaseModel):
    """Response of ``GET /api/v1/facilities/{facility_id}/configuration``.

    The whole growbox in one answer: what it is, where it stands, how it is
    divided into zones and which points those zones read and drive. It is a read
    model assembled from a fixed number of queries, not a serialised ORM graph —
    the entities keep referring to each other by identifier, and this document
    is where they are brought together.
    """

    model_config = ConfigDict(frozen=True)

    facility: ConfigurationFacility
    site: ConfigurationSite
    control_zones: list[ConfigurationZone]
    points: list[ConfigurationPoint]

    @classmethod
    def from_parts(
        cls,
        facility: Facility,
        site: Site,
        control_zones: list[ConfigurationZone],
        points: list[ConfigurationPoint],
    ) -> Self:
        """Assemble the configuration document from its already-built parts.

        Args:
            facility: The facility described by the document.
            site: The site it stands on.
            control_zones: Its zones with their composition, in creation order.
            points: Its points with their state, in creation order.

        Returns:
            The complete configuration document.
        """
        return cls(
            facility=ConfigurationFacility.model_validate(facility),
            site=ConfigurationSite.model_validate(site),
            control_zones=control_zones,
            points=points,
        )
