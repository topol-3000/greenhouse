"""Bring the demonstration growbox to a state the dashboard can be used in.

``demo-init`` is the topology seed plus the one control loop the fan automation
needs, and nothing else. It creates no run, no telemetry and no command: the
point of the ``0.1`` demo is that a reader presses Start and watches the cycle
happen, not that they find it already finished.

It is idempotent by resolving what exists before creating anything, and it never
edits or deletes what it finds. A growbox that is already configured differently
is somebody's data: the bootstrap reports the conflict and stops rather than
making the demo work by overwriting it.

Every write goes through a domain service, so the loop this creates is subject to
exactly the rules ``POST /api/v1/control-loops`` applies. There is no bootstrap
endpoint and this runs no FastAPI startup hook — it is an explicit command that
the local Compose entrypoint happens to invoke.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ai_greenhouse.control.models import ControlLoop, ControlPolicyType
from ai_greenhouse.control.repository import ControlLoopRepository
from ai_greenhouse.control.schemas import ControlLoopCreate
from ai_greenhouse.control.service import ControlLoopService
from ai_greenhouse.seed.demo import DemoSeedResult, seed_demo

logger: BoundLogger = structlog.get_logger(__name__)

DEMO_LOWER_THRESHOLD: Decimal = Decimal("24.0")
DEMO_UPPER_THRESHOLD: Decimal = Decimal("26.0")
"""The hysteresis band of the documented ``0.1`` demonstration, in ``°C``.

The same two numbers the README walkthrough tells the reader to expect the fan to
switch at.
"""

MEASUREMENT_CODE: str = "air_temperature"
CONTROL_CODE: str = "fan_power"
STATUS_CODE: str = "fan_running"


class DemoConfigurationConflictError(RuntimeError):
    """The growbox already carries a control loop the demo cannot use.

    A loop is immutable, so there is nothing to reconcile: the conflict is
    reported with what differs, and the existing loop is left exactly as it is.
    """


@dataclass(frozen=True, slots=True)
class DemoInitResult:
    """What the bootstrap created or found.

    Attributes:
        topology: Identifiers of the growbox, its zone and its four points.
        control_loop_id: The loop watching the demo temperature point.
        created_control_loop: Whether this invocation created that loop.
    """

    topology: DemoSeedResult
    control_loop_id: UUID
    created_control_loop: bool


def _describe(loop: ControlLoop) -> dict[str, str]:
    """Return the loop's configuration as safe log and error context.

    Args:
        loop: The existing loop being judged.

    Returns:
        The fields a reader needs in order to see what differs.
    """
    return {
        "control_loop_id": str(loop.id),
        "policy_type": loop.policy_type.value,
        "lower_threshold": str(loop.lower_threshold),
        "upper_threshold": str(loop.upper_threshold),
        "control_point_id": str(loop.control_point_id),
        "status_point_id": str(loop.status_point_id),
    }


def _is_compatible(loop: ControlLoop, topology: DemoSeedResult) -> bool:
    """Answer whether an existing loop is the one the demo needs.

    Thresholds are compared numerically rather than textually: ``24.0`` and
    ``24`` are the same band, and a stored scale is not a difference in
    configuration.

    Args:
        loop: The loop found on the demo temperature point.
        topology: The growbox the loop has to belong to.

    Returns:
        ``True`` when the demo can run against this loop unchanged.
    """
    return (
        loop.policy_type is ControlPolicyType.HYSTERESIS_V1
        and loop.control_zone_id == topology.control_zone_id
        and loop.control_point_id == topology.point_ids[CONTROL_CODE]
        and loop.status_point_id == topology.point_ids[STATUS_CODE]
        and loop.lower_threshold == DEMO_LOWER_THRESHOLD
        and loop.upper_threshold == DEMO_UPPER_THRESHOLD
    )


async def _get_or_create_control_loop(
    session: AsyncSession,
    topology: DemoSeedResult,
) -> tuple[ControlLoop, bool]:
    """Resolve the demo's control loop or configure it through the service.

    Args:
        session: Transaction-scoped bootstrap session.
        topology: The growbox the loop belongs to.

    Returns:
        The existing or newly created loop, and whether it was created.

    Raises:
        DemoConfigurationConflictError: If a loop already watches the demo
            temperature point and is not the one the demo needs.
        DomainError: If the loop the demo needs is refused, which means the
            growbox is not wired the way the seed leaves it.
    """
    measurement_point_id: UUID = topology.point_ids[MEASUREMENT_CODE]
    existing: ControlLoop | None = await ControlLoopRepository(session).get_by_measurement_point(
        measurement_point_id
    )
    if existing is not None:
        if not _is_compatible(existing, topology):
            logger.error("demo_init_control_loop_conflict", **_describe(existing))
            raise DemoConfigurationConflictError(
                "The demo temperature point already drives a different control loop. "
                "A loop is immutable and nothing was changed; remove or relabel the "
                f"existing configuration first ({_describe(existing)})."
            )
        return existing, False

    loop: ControlLoop = await ControlLoopService(session).create_loop(
        ControlLoopCreate(
            control_zone_id=topology.control_zone_id,
            measurement_point_id=measurement_point_id,
            control_point_id=topology.point_ids[CONTROL_CODE],
            status_point_id=topology.point_ids[STATUS_CODE],
            lower_threshold=DEMO_LOWER_THRESHOLD,
            upper_threshold=DEMO_UPPER_THRESHOLD,
        )
    )
    return loop, True


async def initialize_demo(session: AsyncSession) -> DemoInitResult:
    """Create or resolve the ready-to-use demonstration growbox.

    The caller owns the transaction, so a conflict discovered halfway through
    leaves the database as it was rather than half-configured.

    Args:
        session: Session whose transaction contains the whole bootstrap.

    Returns:
        What was created or found.

    Raises:
        DemoConfigurationConflictError: If an incompatible loop already exists.
        DomainError: If a domain invariant refuses part of the bootstrap.
        SQLAlchemyError: If PostgreSQL cannot be reached or a statement fails.
    """
    topology: DemoSeedResult = await seed_demo(session)
    loop, created = await _get_or_create_control_loop(session, topology)
    logger.info(
        "demo_init_complete",
        control_zone_id=str(topology.control_zone_id),
        action="created" if created else "found",
        **_describe(loop),
    )
    return DemoInitResult(
        topology=topology,
        control_loop_id=loop.id,
        created_control_loop=created,
    )
