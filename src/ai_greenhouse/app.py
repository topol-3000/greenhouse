from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from ai_greenhouse.api.errors import register_exception_handlers
from ai_greenhouse.api.router import api_v1_router, root_router
from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import RequestLoggingMiddleware, configure_logging
from ai_greenhouse.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without opening external connections.

    The application owns no background task and no in-process producer. Every
    measurement reaches the system through the public Edge telemetry boundary,
    so ``lifespan`` has nothing to recover, schedule or cancel: it opens the
    engine and disposes of it again.
    """
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    database_engine = create_database_engine(app_settings)
    session_factory = create_session_factory(database_engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    register_exception_handlers(application)
    application.include_router(root_router)
    application.include_router(api_v1_router)
    return application
