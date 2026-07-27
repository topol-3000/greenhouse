"""Fixtures for tests that talk to a real PostgreSQL instance.

Only tests that request ``database_url`` (directly or through another fixture)
are skipped when no database is configured, so the fake-backed integration
tests in ``tests/integration/api`` keep running outside Compose.

Schema setup goes through Alembic. ``metadata.create_all()`` is deliberately
not used, in the tests either: the migrations are what production applies, so
they are what the tests must exercise.

Isolation is a transaction rollback. Each test runs on one connection with an
outer transaction that is never committed; the session joins it through a
savepoint, so the commit issued by ``get_session`` is real from the
application's point of view but disappears when the test ends.
"""

import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

CONFIGURED_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
"""Captured at import time, before any fixture scrubs the environment."""


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return the configured database URL, skipping the test without one.

    Returns:
        The SQLAlchemy async URL of the PostgreSQL instance to test against.
    """
    if not CONFIGURED_DATABASE_URL:
        pytest.skip("DATABASE_URL is not set; run these tests through Docker Compose")
    return CONFIGURED_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    """Bring the database up to ``head`` once per test session.

    Alembic's async ``env.py`` calls ``asyncio.run``, which cannot run inside an
    already-running event loop, so the upgrade is executed on a worker thread.

    Args:
        database_url: The database to migrate.

    Returns:
        The same URL, once the schema is at ``head``.
    """
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(command.upgrade, config, "head").result()
    return database_url


@pytest.fixture
def database_settings(migrated_database: str) -> Settings:
    """Build settings pointing at the migrated database.

    Args:
        migrated_database: The URL of the migrated database.

    Returns:
        ``Settings`` built from explicit values, ignoring ``.env``.
    """
    return Settings(database_url=migrated_database, app_env="test", _env_file=None)


@pytest.fixture
async def connection(migrated_database: str) -> AsyncIterator[AsyncConnection]:
    """Yield a connection inside a transaction that is always rolled back.

    Args:
        migrated_database: The URL of the migrated database.

    Yields:
        A connection with an open outer transaction.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        async with engine.connect() as open_connection:
            transaction = await open_connection.begin()
            try:
                yield open_connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def app(database_settings: Settings, connection: AsyncConnection) -> FastAPI:
    """Build the application with its sessions bound to the test transaction.

    Args:
        database_settings: Settings pointing at the migrated database.
        connection: The connection whose transaction is rolled back afterwards.

    Returns:
        A configured ``FastAPI`` application.
    """
    application = create_app(database_settings)
    application.state.session_factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    return application


@pytest.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client wired straight to the ASGI application.

    Args:
        app: The application under test.

    Yields:
        An ``httpx.AsyncClient`` that never opens a socket.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
