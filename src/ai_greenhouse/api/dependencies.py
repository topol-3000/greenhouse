from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.infrastructure.database.health import probe_database

DatabaseHealthProbe = Callable[[], Awaitable[None]]


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database_health_probe(request: Request) -> DatabaseHealthProbe:
    engine = cast(AsyncEngine, request.app.state.database_engine)
    return partial(probe_database, engine)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = cast(
        async_sessionmaker[AsyncSession],
        request.app.state.session_factory,
    )
    async with session_factory() as session:
        yield session
