from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.infrastructure.database.health import probe_database

DatabaseHealthProbe = Callable[[], Awaitable[None]]


async def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_database_health_probe(request: Request) -> DatabaseHealthProbe:
    engine = cast(AsyncEngine, request.app.state.database_engine)
    return partial(probe_database, engine)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session for the whole request.

    The session is committed once the handler returns and rolled back if it
    raises, so a handler never has to manage the transaction boundary itself.
    Domain services receive this session; they never build their own engine or
    session factory.
    """
    session_factory = cast(
        async_sessionmaker[AsyncSession],
        request.app.state.session_factory,
    )
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
