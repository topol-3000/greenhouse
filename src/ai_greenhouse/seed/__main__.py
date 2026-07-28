"""Command-line entry point for explicitly invoked AI Greenhouse seeds."""

import asyncio
import sys
from collections.abc import Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.stdlib import BoundLogger

from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import configure_logging
from ai_greenhouse.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)
from ai_greenhouse.seed.demo import seed_demo

logger: BoundLogger = structlog.get_logger(__name__)


async def run_demo_command(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Run the demo seed and translate failures into a process exit code.

    Args:
        settings: Application settings containing the database URL.
        session_factory: Optional injected factory used by integration tests.
            Production invocations build their own engine and factory.

    Returns:
        Zero on success and one when setup, connectivity or seeding fails.
    """
    engine: AsyncEngine | None = None
    try:
        if session_factory is None:
            engine = create_database_engine(settings)
            session_factory = create_session_factory(engine)

        async with session_factory() as session:
            async with session.begin():
                await seed_demo(session)
    except Exception:
        logger.exception("demo_seed_failed")
        return 1
    finally:
        if engine is not None:
            await engine.dispose()
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    """Validate the seed name, configure logging and execute the command.

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

    if arguments != ["demo"]:
        logger.error(
            "seed_usage_error",
            arguments=arguments,
            expected="python -m ai_greenhouse.seed demo",
        )
        return 2
    return asyncio.run(run_demo_command(app_settings))


if __name__ == "__main__":
    raise SystemExit(main())
