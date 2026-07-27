"""FastAPI dependencies shared by every domain router.

Domain services receive their ``Settings``, health probe and ``AsyncSession``
through these dependencies. They never build their own engine, session
factory or configuration object.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.infrastructure.database.health import probe_database

DatabaseHealthProbe = Callable[[], Awaitable[None]]


async def get_app_settings(request: Request) -> Settings:
    """Return the application's ``Settings`` stored on app state.

    Args:
        request: The current request, used to reach ``app.state``.

    Returns:
        The ``Settings`` instance built at application startup.
    """
    return cast(Settings, request.app.state.settings)


async def get_database_health_probe(request: Request) -> DatabaseHealthProbe:
    """Return a callable that checks database connectivity.

    Args:
        request: The current request, used to reach ``app.state``.

    Returns:
        A zero-argument coroutine function that raises if the database is
        unreachable and returns ``None`` otherwise.
    """
    engine: AsyncEngine = cast(AsyncEngine, request.app.state.database_engine)
    return partial(probe_database, engine)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session for the whole request.

    The session is committed once the handler returns and rolled back if it
    raises, so a handler never has to manage the transaction boundary itself.
    Domain services receive this session; they never build their own engine or
    session factory.

    Args:
        request: The current request, used to reach ``app.state``.

    Yields:
        An ``AsyncSession`` bound to the request's lifetime.

    Raises:
        Exception: Re-raised after rollback, whatever the handler raised.
    """
    session_factory: async_sessionmaker[AsyncSession] = cast(
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
