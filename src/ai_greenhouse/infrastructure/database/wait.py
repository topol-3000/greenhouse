import asyncio

import structlog

from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import configure_logging
from ai_greenhouse.infrastructure.database.engine import create_database_engine
from ai_greenhouse.infrastructure.database.health import probe_database

logger = structlog.get_logger(__name__)


async def wait_for_database(
    settings: Settings,
    *,
    timeout_seconds: float = 60,
    retry_interval_seconds: float = 1,
) -> None:
    engine = create_database_engine(settings)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    attempt = 0

    try:
        while True:
            attempt += 1
            try:
                await probe_database(engine)
            except Exception as error:
                if loop.time() >= deadline:
                    logger.error(
                        "database_wait_timeout",
                        attempts=attempt,
                        timeout_seconds=timeout_seconds,
                        error_type=type(error).__name__,
                    )
                    raise RuntimeError(
                        f"PostgreSQL did not become ready within {timeout_seconds:g} seconds"
                    ) from None

                logger.warning(
                    "database_not_ready",
                    attempt=attempt,
                    retry_interval_seconds=retry_interval_seconds,
                    error_type=type(error).__name__,
                )
                await asyncio.sleep(retry_interval_seconds)
            else:
                logger.info("database_ready", attempts=attempt)
                return
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    try:
        asyncio.run(wait_for_database(settings))
    except RuntimeError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
