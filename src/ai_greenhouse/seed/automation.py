"""Drive the documented fan-automation demonstration through the real flow.

This is verification infrastructure, not a domain entity. It creates nothing
and decides nothing: it offers three temperatures to the same ingestion path
every producer uses, and reports what came back. A device sending the same three
values would produce the same three outcomes, which is the whole claim the
milestone makes.

It is deliberately not an ingestion endpoint. Telemetry has no public write API,
and adding one so that a demonstration is easier to run would be the milestone
proving something it did not build.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ai_greenhouse.control.automation import IngestionResult, TelemetryIngestionService
from ai_greenhouse.control.models import ControlLoop
from ai_greenhouse.control.repository import ControlLoopRepository
from ai_greenhouse.points.models import DataQuality, Point
from ai_greenhouse.points.repository import PointRepository
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord
from ai_greenhouse.topology.models import Site
from ai_greenhouse.topology.repository import SiteRepository

logger: BoundLogger = structlog.get_logger(__name__)

DEMO_SITE_CODE: str = "home"
DEMO_MEASUREMENT_CODE: str = "air_temperature"

DEMO_TEMPERATURES: tuple[float, ...] = (27.0, 25.0, 23.0)
"""The three documented measurements: above the band, inside it, below it."""

DEMO_OBSERVED_BASE: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
"""Measurement time of the first reading.

Fixed rather than taken from the wall clock so that a second run offers exactly
the inputs the first one did. The demonstration therefore expects a growbox
whose temperature carries no newer telemetry — which is what running it on a
fresh database means.
"""

DEMO_OBSERVED_STEP: timedelta = timedelta(minutes=1)
"""Gap between readings. Each is strictly newer, so each replaces the last."""

DEMO_RUN_KEY: str = "fan-automation-demo"
"""Namespace of the derived sample identifiers.

Derived rather than random, because "re-running the demonstration changes
nothing" is one of the properties being demonstrated, and it is the sample
identifier that carries it.
"""


class AutomationDemoNotConfiguredError(RuntimeError):
    """The demonstration was run against a growbox that is not ready for it."""


@dataclass(frozen=True, slots=True)
class AutomationDemoResult:
    """What the three offered readings led to.

    Attributes:
        control_loop_id: The loop that evaluated them.
        measurement_point_id: The point they were recorded on.
        sample_ids: The three derived trigger-sample identifiers, in order.
        command_ids: What each reading produced — a command, or ``None`` where
            the temperature called for no change.
    """

    control_loop_id: UUID
    measurement_point_id: UUID
    sample_ids: tuple[UUID, ...]
    command_ids: tuple[UUID | None, ...]


def demo_sample_id(point_id: UUID, index: int) -> UUID:
    """Derive the identifier of one demonstration reading.

    Args:
        point_id: The measured point, used as the UUID namespace.
        index: Position of the reading in the documented sequence.

    Returns:
        The sample identifier, the same on every run.
    """
    return uuid5(point_id, f"{DEMO_RUN_KEY}:{index}")


async def drive_automation_demo(session: AsyncSession) -> AutomationDemoResult:
    """Offer the documented readings and report what automation did with them.

    The caller owns the transaction, exactly as it does for the topology seed.

    Args:
        session: Session whose transaction contains the whole demonstration.

    Returns:
        The loop, the point and what each reading produced.

    Raises:
        AutomationDemoNotConfiguredError: If the demo growbox has not been
            seeded, or if its climate zone has no control loop yet.
        DomainError: If the telemetry boundary refuses a reading.
    """
    site: Site | None = await SiteRepository(session).get_by_code(DEMO_SITE_CODE)
    if site is None:
        raise AutomationDemoNotConfiguredError(
            "Demo site not found; run `python -m ai_greenhouse.seed demo` first"
        )
    point: Point | None = await PointRepository(session).get_by_code(
        site.id,
        DEMO_MEASUREMENT_CODE,
    )
    if point is None:
        raise AutomationDemoNotConfiguredError(
            "Demo air_temperature point not found; run `python -m ai_greenhouse.seed demo` first"
        )
    loop: ControlLoop | None = await ControlLoopRepository(session).get_by_measurement_point(
        point.id
    )
    if loop is None:
        raise AutomationDemoNotConfiguredError(
            "No control loop watches the demo air_temperature point; "
            "create one through POST /api/v1/control-loops first"
        )

    ingestion = TelemetryIngestionService(session)
    sample_ids: list[UUID] = []
    command_ids: list[UUID | None] = []
    for index, value in enumerate(DEMO_TEMPERATURES):
        sample_id: UUID = demo_sample_id(point.id, index)
        observed_at: datetime = DEMO_OBSERVED_BASE + index * DEMO_OBSERVED_STEP
        result: IngestionResult = await ingestion.ingest(
            TelemetrySampleRecord(
                id=sample_id,
                point_id=point.id,
                value=value,
                observed_at=observed_at,
                received_at=observed_at,
                quality=DataQuality.SIMULATED,
            )
        )
        sample_ids.append(sample_id)
        command_ids.append(None if result.command is None else result.command.id)
        logger.info(
            "automation_demo_reading",
            temperature=value,
            sample_id=str(sample_id),
            outcome=result.outcome.value,
            command_id=None if result.command is None else str(result.command.id),
            automation_failed=result.automation_failed,
        )

    result_summary = AutomationDemoResult(
        control_loop_id=loop.id,
        measurement_point_id=point.id,
        sample_ids=tuple(sample_ids),
        command_ids=tuple(command_ids),
    )
    logger.info(
        "automation_demo_complete",
        control_loop_id=str(result_summary.control_loop_id),
        measurement_point_id=str(result_summary.measurement_point_id),
        sample_ids=[str(identifier) for identifier in result_summary.sample_ids],
        command_ids=[
            None if identifier is None else str(identifier)
            for identifier in result_summary.command_ids
        ],
    )
    return result_summary
