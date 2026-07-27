from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from ai_greenhouse.api.router import router
from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import RequestLoggingMiddleware, configure_logging
from ai_greenhouse.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without opening external connections."""
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    database_engine = create_database_engine(app_settings)
    session_factory = create_session_factory(database_engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started")
        try:
            yield
        finally:
            await database_engine.dispose()
            logger.info("application_stopped")

    application = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    application.state.settings = app_settings
    application.state.database_engine = database_engine
    application.state.session_factory = session_factory
    application.add_middleware(RequestLoggingMiddleware)
    application.include_router(router)
    return application
