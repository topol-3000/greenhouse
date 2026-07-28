"""Create the Milestone 1 basil growbox through the existing domain services.

The repository reads in this module exist only to make the operation
idempotent. No model is inserted, updated or deleted directly: all writes go
through the services that enforce the HTTP API's domain rules.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ai_greenhouse.points.models import Point, PointDataType, PointKind
from ai_greenhouse.points.repository import PointRepository
from ai_greenhouse.points.schemas import PointCreate
from ai_greenhouse.points.service import PointService
from ai_greenhouse.topology.models import (
    ControlZone,
    Facility,
    FacilityType,
    Site,
    ZonePointAssignment,
    ZonePointRole,
    ZoneType,
)
from ai_greenhouse.topology.repository import (
    ControlZoneRepository,
    FacilityRepository,
    SiteRepository,
    ZonePointAssignmentRepository,
)
from ai_greenhouse.topology.schemas import (
    ControlZoneCreate,
    FacilityCreate,
    SiteCreate,
    ZonePointAssignmentCreate,
)
from ai_greenhouse.topology.service import (
    ControlZoneService,
    FacilityService,
    SiteService,
    ZonePointAssignmentService,
)

logger: BoundLogger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DemoPoint:
    """One point and its zone role in the demonstration growbox."""

    code: str
    name: str
    point_kind: PointKind
    metric_type: str
    data_type: PointDataType
    role: ZonePointRole
    unit: str | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    """Identifiers of every record created or found by the demo seed."""

    site_id: UUID
    facility_id: UUID
    control_zone_id: UUID
    point_ids: dict[str, UUID]
    assignment_ids: dict[str, UUID]


DEMO_POINTS: tuple[DemoPoint, ...] = (
    DemoPoint(
        code="air_temperature",
        name="Air Temperature",
        point_kind=PointKind.MEASUREMENT,
        metric_type="air_temperature",
        data_type=PointDataType.FLOAT,
        role=ZonePointRole.PRIMARY_MEASUREMENT,
        unit="°C",
        min_value=Decimal("-20"),
        max_value=Decimal("60"),
    ),
    DemoPoint(
        code="air_humidity",
        name="Air Humidity",
        point_kind=PointKind.MEASUREMENT,
        metric_type="air_humidity",
        data_type=PointDataType.FLOAT,
        role=ZonePointRole.SECONDARY_MEASUREMENT,
        unit="%",
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    ),
    DemoPoint(
        code="fan_power",
        name="Fan Power",
        point_kind=PointKind.CONTROL,
        metric_type="fan_power",
        data_type=PointDataType.BOOLEAN,
        role=ZonePointRole.CONTROL_OUTPUT,
    ),
    DemoPoint(
        code="fan_running",
        name="Fan Running",
        point_kind=PointKind.STATUS,
        metric_type="fan_running",
        data_type=PointDataType.BOOLEAN,
        role=ZonePointRole.STATUS_FEEDBACK,
    ),
)
"""The complete point set and zone composition of the Milestone 1 demo."""


def _log_entity(
    entity_type: str,
    code: str,
    entity_id: UUID,
    *,
    created: bool,
    **context: Any,
) -> None:
    """Emit the identifier and idempotency outcome of one seed entity.

    Args:
        entity_type: Stable entity label used by log consumers.
        code: Human-readable stable code of the entity.
        entity_id: Identifier of the created or found record.
        created: Whether this invocation created the record.
        **context: Optional parent or role identifiers.
    """
    logger.info(
        "demo_seed_entity",
        entity_type=entity_type,
        code=code,
        entity_id=str(entity_id),
        action="created" if created else "found",
        **context,
    )


async def _get_or_create_site(session: AsyncSession) -> Site:
    """Resolve the demo site by code or create it through ``SiteService``.

    Args:
        session: Transaction-scoped seed session.

    Returns:
        The existing or newly created Home site.
    """
    existing: Site | None = await SiteRepository(session).get_by_code("home")
    created: bool = existing is None
    site: Site = existing or await SiteService(session).create_site(
        SiteCreate(name="Home", code="home", timezone="UTC")
    )
    _log_entity("site", site.code, site.id, created=created)
    return site


async def _get_or_create_facility(session: AsyncSession, site: Site) -> Facility:
    """Resolve the growbox within its site or create it through the service.

    Args:
        session: Transaction-scoped seed session.
        site: Parent Home site.

    Returns:
        The existing or newly created Basil Growbox facility.
    """
    existing: Facility | None = await FacilityRepository(session).get_by_code(
        site.id,
        "basil-growbox",
    )
    created: bool = existing is None
    facility: Facility = existing or await FacilityService(session).create_facility(
        FacilityCreate(
            site_id=site.id,
            name="Basil Growbox",
            code="basil-growbox",
            facility_type=FacilityType.GROWBOX,
        )
    )
    _log_entity(
        "facility",
        facility.code,
        facility.id,
        created=created,
        site_id=str(site.id),
    )
    return facility


async def _get_or_create_control_zone(
    session: AsyncSession,
    facility: Facility,
) -> ControlZone:
    """Resolve Main Climate within its facility or create it through the service.

    Args:
        session: Transaction-scoped seed session.
        facility: Parent Basil Growbox facility.

    Returns:
        The existing or newly created Main Climate zone.
    """
    existing: ControlZone | None = await ControlZoneRepository(session).get_by_code(
        facility.id,
        "main-climate",
    )
    created: bool = existing is None
    control_zone: ControlZone = existing or await ControlZoneService(session).create_control_zone(
        ControlZoneCreate(
            facility_id=facility.id,
            name="Main Climate",
            code="main-climate",
            zone_type=ZoneType.CLIMATE,
        )
    )
    _log_entity(
        "control_zone",
        control_zone.code,
        control_zone.id,
        created=created,
        facility_id=str(facility.id),
    )
    return control_zone


async def _get_or_create_point(
    session: AsyncSession,
    *,
    site: Site,
    facility: Facility,
    definition: DemoPoint,
) -> Point:
    """Resolve one point within its site or create it through ``PointService``.

    Args:
        session: Transaction-scoped seed session.
        site: Parent Home site.
        facility: Facility scope of the point.
        definition: Stable demo point fields.

    Returns:
        The existing or newly created point.
    """
    existing: Point | None = await PointRepository(session).get_by_code(
        site.id,
        definition.code,
    )
    created: bool = existing is None
    point: Point = existing or await PointService(session).create_point(
        PointCreate(
            site_id=site.id,
            facility_id=facility.id,
            code=definition.code,
            name=definition.name,
            point_kind=definition.point_kind,
            metric_type=definition.metric_type,
            data_type=definition.data_type,
            unit=definition.unit,
            min_value=definition.min_value,
            max_value=definition.max_value,
        )
    )
    _log_entity(
        "point",
        point.code,
        point.id,
        created=created,
        site_id=str(site.id),
        facility_id=str(facility.id),
    )
    return point


async def _get_or_create_assignment(
    session: AsyncSession,
    *,
    control_zone: ControlZone,
    point: Point,
    role: ZonePointRole,
) -> ZonePointAssignment:
    """Resolve one exact zone link or create it through the assignment service.

    Args:
        session: Transaction-scoped seed session.
        control_zone: Zone receiving the point.
        point: Point being assigned.
        role: Part the point plays in the zone.

    Returns:
        The existing or newly created assignment.
    """
    repository = ZonePointAssignmentRepository(session)
    existing: ZonePointAssignment | None = await repository.get_by_zone_point_role(
        control_zone.id,
        point.id,
        role,
    )
    created: bool = existing is None
    if existing is None:
        assignment, _point = await ZonePointAssignmentService(session).create_assignment(
            control_zone.id,
            ZonePointAssignmentCreate(point_id=point.id, role=role),
        )
    else:
        assignment = existing
    _log_entity(
        "zone_point_assignment",
        point.code,
        assignment.id,
        created=created,
        control_zone_id=str(control_zone.id),
        point_id=str(point.id),
        role=role.value,
    )
    return assignment


async def seed_demo(session: AsyncSession) -> DemoSeedResult:
    """Create or resolve the complete Milestone 1 demonstration growbox.

    The caller owns the transaction. A failure therefore rolls the whole seed
    back instead of leaving a partially created growbox.

    Args:
        session: Session whose transaction contains the complete seed.

    Returns:
        Identifiers of all created or found records.

    Raises:
        DomainError: If existing data conflicts with a required relationship or
            a domain invariant rejects a creation.
        SQLAlchemyError: If PostgreSQL cannot be reached or a statement fails.
    """
    site: Site = await _get_or_create_site(session)
    facility: Facility = await _get_or_create_facility(session, site)
    control_zone: ControlZone = await _get_or_create_control_zone(session, facility)

    point_ids: dict[str, UUID] = {}
    assignment_ids: dict[str, UUID] = {}
    for definition in DEMO_POINTS:
        point: Point = await _get_or_create_point(
            session,
            site=site,
            facility=facility,
            definition=definition,
        )
        assignment: ZonePointAssignment = await _get_or_create_assignment(
            session,
            control_zone=control_zone,
            point=point,
            role=definition.role,
        )
        point_ids[definition.code] = point.id
        assignment_ids[definition.code] = assignment.id

    result = DemoSeedResult(
        site_id=site.id,
        facility_id=facility.id,
        control_zone_id=control_zone.id,
        point_ids=point_ids,
        assignment_ids=assignment_ids,
    )
    logger.info(
        "demo_seed_complete",
        site_id=str(result.site_id),
        facility_id=str(result.facility_id),
        control_zone_id=str(result.control_zone_id),
        point_ids={code: str(identifier) for code, identifier in result.point_ids.items()},
        assignment_ids={
            code: str(identifier) for code, identifier in result.assignment_ids.items()
        },
    )
    return result
