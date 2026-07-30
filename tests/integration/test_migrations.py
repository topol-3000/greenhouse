"""Coverage of the Alembic migrations themselves, on a database of their own.

Three things are required of every migration: it applies to an empty
database, it leaves the schema in step with the declarative metadata, and it has
a ``downgrade`` that really works. The application's own test database cannot be
used to check the last one — downgrading it would pull the schema out from under
every other test — so each test here creates a scratch database, does its work
and drops it again.

The commands are run through ``python -m alembic`` in a subprocess, which is the
same path a developer and CI take. Running them in-process would need the
settings cache cleared and Alembic's ``asyncio.run`` kept off the running loop,
and would prove less.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

SCRATCH_DATABASE_NAME: str = "ai_greenhouse_migration_check"
"""A database no other test touches, so it can be dropped and rebuilt freely."""

MAINTENANCE_DATABASE_NAME: str = "postgres"
"""The database the scratch one is created from; it is never migrated."""

EXPECTED_TABLES: set[str] = {
    "sites",
    "facilities",
    "control_zones",
    "points",
    "point_current_states",
    "zone_point_assignments",
    "telemetry_samples",
    "simulation_runs",
    "control_loops",
    "commands",
}
"""Every table the migrations create, all of which ``downgrade`` must remove."""


def run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Alembic command against the given database.

    Args:
        database_url: The database the command operates on, passed through the
            environment because ``migrations/env.py`` reads it from settings.
        *arguments: The command and its options, such as ``("upgrade", "head")``.

    Returns:
        The completed process, with output captured.
    """
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def table_names(database_url: str) -> set[str]:
    """Return the names of the public tables of one database.

    Args:
        database_url: The database to inspect.

    Returns:
        Every table name in the ``public`` schema, including ``alembic_version``.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return set(names)
    finally:
        await engine.dispose()


@pytest.fixture
async def scratch_database(database_url: str) -> AsyncIterator[str]:
    """Yield the URL of an empty database created for one test.

    Args:
        database_url: The configured database, used only for its connection
            details; its schema is never touched.

    Yields:
        The URL of a freshly created, empty database.
    """
    configured: URL = make_url(database_url)
    maintenance_url: URL = configured.set(database=MAINTENANCE_DATABASE_NAME)
    scratch_url: URL = configured.set(database=SCRATCH_DATABASE_NAME)

    engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE_NAME}" WITH (FORCE)')
            )
            await connection.execute(text(f'CREATE DATABASE "{SCRATCH_DATABASE_NAME}"'))
        yield scratch_url.render_as_string(hide_password=False)
        async with engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE_NAME}" WITH (FORCE)')
            )
    finally:
        await engine.dispose()


async def test_migrations_apply_to_an_empty_database_and_match_the_metadata(
    scratch_database: str,
) -> None:
    """``alembic check`` is the guard against a model that no migration describes."""
    upgrade = run_alembic(scratch_database, "upgrade", "head")
    check = run_alembic(scratch_database, "check")

    assert upgrade.returncode == 0, upgrade.stderr
    assert check.returncode == 0, check.stderr
    assert EXPECTED_TABLES <= await table_names(scratch_database)


async def test_every_migration_downgrades_back_to_an_empty_database(
    scratch_database: str,
) -> None:
    """A downgrade path that is never run is a downgrade path that does not work."""
    assert run_alembic(scratch_database, "upgrade", "head").returncode == 0

    downgrade = run_alembic(scratch_database, "downgrade", "base")
    after_downgrade: set[str] = await table_names(scratch_database)
    upgrade_again = run_alembic(scratch_database, "upgrade", "head")

    assert downgrade.returncode == 0, downgrade.stderr
    assert after_downgrade & EXPECTED_TABLES == set(), after_downgrade
    assert upgrade_again.returncode == 0, upgrade_again.stderr
    assert EXPECTED_TABLES <= await table_names(scratch_database)
