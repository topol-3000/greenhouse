from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.api.errors import register_exception_handlers
from ai_greenhouse.api.router import api_v1_router, root_router
from ai_greenhouse.core.config import Settings, get_settings
from ai_greenhouse.core.logging import RequestLoggingMiddleware, configure_logging
from ai_greenhouse.infrastructure.database.engine import (
    create_database_engine,
    create_session_factory,
)
from ai_greenhouse.simulation.runtime import SimulationRuntime

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without opening external connections."""
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    database_engine = create_database_engine(app_settings)
    session_factory = create_session_factory(database_engine)
    application: FastAPI
    simulation_runtime = SimulationRuntime(
        lambda: cast(
            async_sessionmaker[AsyncSession],
            application.state.session_factory,
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = cast(SimulationRuntime, app.state.simulation_runtime)
        recovered_runs = await runtime.recover_interrupted_runs()
        logger.info("application_started")
        if recovered_runs:
            logger.info("simulation_runs_recovered", run_count=recovered_runs)
        try:
            yield
        finally:
            await runtime.shutdown()
            await database_engine.dispose()
            logger.info("application_stopped")

    application = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    application.state.settings = app_settings
    application.state.database_engine = database_engine
    application.state.session_factory = session_factory
    application.state.simulation_runtime = simulation_runtime
    application.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(application)
    application.include_router(root_router)
    application.include_router(api_v1_router)
    return application
