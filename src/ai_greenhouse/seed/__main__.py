"""Command-line entry point for explicitly invoked AI Greenhouse seeds and demos.

Three commands. None of them is an application startup hook: the local Compose
entrypoint invokes ``demo-init`` as a command of its own, before Uvicorn, and an
explicit container command such as ``pytest`` runs neither it nor anything else
here.

``demo``
    Creates or finds the basil growbox topology.
``demo-init``
    The topology plus the one ``24–26 °C`` control loop the ``0.1`` browser
    demonstration needs. Idempotent, and it refuses a conflicting existing
    configuration instead of changing it.
``automation-demo``
    Offers the documented three temperatures to the automation flow. It needs
    the topology above and a control loop configured through the API.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import configure_logging
from ai_greenhouse.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)
from ai_greenhouse.seed.automation import drive_automation_demo
from ai_greenhouse.seed.demo import seed_demo
from ai_greenhouse.seed.demo_init import initialize_demo

logger: BoundLogger = structlog.get_logger(__name__)

SeedOperation = Callable[[AsyncSession], Awaitable[object]]

OPERATIONS: dict[str, SeedOperation] = {
    "demo": seed_demo,
    "demo-init": initialize_demo,
    "automation-demo": drive_automation_demo,
}
"""The commands this module accepts, and what each one runs in one transaction."""


async def run_seed_command(
    operation: SeedOperation,
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Run one operation in a single transaction and report an exit code.

    Args:
        operation: The coroutine function to run against the session.
        settings: Application settings containing the database URL.
        session_factory: Optional injected factory used by integration tests.
            Production invocations build their own engine and factory.

    Returns:
        Zero on success and one when setup, connectivity or the operation fails.
    """
    engine: AsyncEngine | None = None
    try:
        if session_factory is None:
            engine = create_database_engine(settings)
            session_factory = create_session_factory(engine)

        async with session_factory() as session:
            async with session.begin():
                await operation(session)
    except Exception:
        logger.exception("seed_command_failed", command=operation.__name__)
        return 1
    finally:
        if engine is not None:
            await engine.dispose()
    return 0


async def run_demo_command(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Run the topology demo seed.

    Args:
        settings: Application settings containing the database URL.
        session_factory: Optional injected factory used by integration tests.

    Returns:
        Zero on success and one on failure.
    """
    return await run_seed_command(seed_demo, settings, session_factory=session_factory)


async def run_demo_init_command(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Prepare the ready-to-use browser demonstration.

    Args:
        settings: Application settings containing the database URL.
        session_factory: Optional injected factory used by integration tests.

    Returns:
        Zero on success and one on failure, including a growbox whose existing
        control loop conflicts with the one the demonstration needs.
    """
    return await run_seed_command(initialize_demo, settings, session_factory=session_factory)


async def run_automation_demo_command(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Drive the documented fan-automation demonstration.

    Args:
        settings: Application settings containing the database URL.
        session_factory: Optional injected factory used by integration tests.

    Returns:
        Zero on success and one on failure, including a growbox that has no
        control loop yet.
    """
    return await run_seed_command(
        drive_automation_demo,
        settings,
        session_factory=session_factory,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    """Validate the command name, configure logging and execute it.

    Args:
        argv: Command arguments without the module name. Defaults to
            ``sys.argv[1:]``.
        settings: Optional explicit settings for tests.

    Returns:
        Zero on success, one on execution failure and two for invalid usage.
    """
    arguments: list[str] = list(sys.argv[1:] if argv is None else argv)
    app_settings: Settings = settings or get_settings()
    configure_logging(app_settings)

    operation: SeedOperation | None = OPERATIONS.get(arguments[0]) if len(arguments) == 1 else None
    if operation is None:
        logger.error(
            "seed_usage_error",
            arguments=arguments,
            expected=f"python -m ai_greenhouse.seed {{{'|'.join(OPERATIONS)}}}",
        )
        return 2
    return asyncio.run(run_seed_command(operation, app_settings))


if __name__ == "__main__":
    raise SystemExit(main())
