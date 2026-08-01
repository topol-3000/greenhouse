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

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

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
    "control_loops",
    "commands",
    "gateways",
    "gateway_points",
    "edge_telemetry_messages",
    "crops",
    "growing_recipes",
    "recipe_versions",
    "recipe_stages",
    "target_requirements",
}
"""Every table the schema at ``head`` carries; ``downgrade`` must remove them all.

``simulation_runs`` is deliberately absent. It is created by ``20260729_0008``
and dropped again by ``20260731_0015``, so it exists partway along the path and
not at either end of it.
"""

REVISION_BEFORE_SIMULATION_REMOVAL: str = "20260731_0014"
"""The last revision whose schema still carries the simulation domain."""


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


async def foreign_key_deferral(
    database_url: str,
    constraint_name: str,
) -> tuple[bool, bool]:
    """Read whether one foreign key is deferrable and initially deferred."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT condeferrable, condeferred "
                    "FROM pg_constraint WHERE conname = :constraint_name"
                ),
                {"constraint_name": constraint_name},
            )
            row = result.one()
            return bool(row.condeferrable), bool(row.condeferred)
    finally:
        await engine.dispose()


async def column_names(database_url: str, table: str) -> set[str]:
    """Return the column names of one table.

    Args:
        database_url: The database to inspect.
        table: The table whose columns to read.

    Returns:
        Every column name of that table in the ``public`` schema.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table"
                ),
                {"table": table},
            )
            return set(names)
    finally:
        await engine.dispose()


async def relation_names(database_url: str) -> tuple[set[str], set[str]]:
    """Return every constraint name and every index name of the public schema.

    Args:
        database_url: The database to inspect.

    Returns:
        The constraint names and the index names, as two sets.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            constraints = await connection.scalars(text("SELECT conname FROM pg_constraint"))
            indexes = await connection.scalars(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            return set(constraints), set(indexes)
    finally:
        await engine.dispose()


SITE_ID: UUID = UUID("11111111-1111-4111-8111-111111111111")
FACILITY_ID: UUID = UUID("22222222-2222-4222-8222-222222222222")
ZONE_ID: UUID = UUID("33333333-3333-4333-8333-333333333333")
TEMPERATURE_ID: UUID = UUID("44444444-4444-4444-8444-444444444444")
FAN_POWER_ID: UUID = UUID("55555555-5555-4555-8555-555555555555")
FAN_RUNNING_ID: UUID = UUID("66666666-6666-4666-8666-666666666666")
LOOP_ID: UUID = UUID("77777777-7777-4777-8777-777777777777")
RUN_ID: UUID = UUID("88888888-8888-4888-8888-888888888888")
COMMAND_ID: UUID = UUID("99999999-9999-4999-8999-999999999999")
TRIGGER_SAMPLE_ID: UUID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONTROL_SAMPLE_ID: UUID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
STATUS_SAMPLE_ID: UUID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

OBSERVED_AT: datetime = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

EXPECTED_RECORDED_DATA: dict[str, Any] = {
    "samples": [
        (TRIGGER_SAMPLE_ID, TEMPERATURE_ID, 27.5, "°C", "simulated"),
        (CONTROL_SAMPLE_ID, FAN_POWER_ID, True, None, "simulated"),
        (STATUS_SAMPLE_ID, FAN_RUNNING_ID, True, None, "simulated"),
    ],
    "states": [
        (TEMPERATURE_ID, 27.5, "simulated", 1),
        (FAN_POWER_ID, True, "simulated", 1),
        (FAN_RUNNING_ID, True, "simulated", 1),
    ],
    "commands": [(COMMAND_ID, LOOP_ID, TRIGGER_SAMPLE_ID, True, "applied")],
}
"""What has to still be there afterwards: measured values, state and history.

Every sample was produced by a simulator and every state carries ``simulated``,
which is the case the removal must not confuse with the concept it removes: the
producer left, the measurements it recorded are history and stay.
"""


async def seed_pre_removal_rows(database_url: str) -> None:
    """Write one growbox, its measurements and its command on the old schema.

    The statements are written by hand rather than through the services on
    purpose: the services describe today's schema, and what this fixture has to
    produce is a database as the *previous* revision left it, simulation run
    included.

    Args:
        database_url: A database migrated to
            ``REVISION_BEFORE_SIMULATION_REMOVAL``.
    """
    engine = create_async_engine(database_url)
    points = (
        (TEMPERATURE_ID, "air_temperature", "measurement", "air_temperature", "float", "°C"),
        (FAN_POWER_ID, "fan_power", "control", "fan_power", "boolean", None),
        (FAN_RUNNING_ID, "fan_running", "status", "fan_running", "boolean", None),
    )
    samples = (
        (TRIGGER_SAMPLE_ID, TEMPERATURE_ID, 27.5, "°C"),
        (CONTROL_SAMPLE_ID, FAN_POWER_ID, True, None),
        (STATUS_SAMPLE_ID, FAN_RUNNING_ID, True, None),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO sites (id, name, code, timezone, status, created_at, updated_at)"
                    " VALUES (:id, 'Home', 'home', 'UTC', 'active', :now, :now)"
                ),
                {"id": SITE_ID, "now": OBSERVED_AT},
            )
            await connection.execute(
                text(
                    "INSERT INTO facilities"
                    " (id, site_id, name, code, facility_type, status, created_at, updated_at)"
                    " VALUES (:id, :site_id, 'Basil Growbox', 'basil-growbox', 'growbox',"
                    " 'active', :now, :now)"
                ),
                {"id": FACILITY_ID, "site_id": SITE_ID, "now": OBSERVED_AT},
            )
            await connection.execute(
                text(
                    "INSERT INTO control_zones"
                    " (id, facility_id, name, code, zone_type, status, created_at, updated_at)"
                    " VALUES (:id, :facility_id, 'Main Climate', 'main-climate', 'climate',"
                    " 'active', :now, :now)"
                ),
                {"id": ZONE_ID, "facility_id": FACILITY_ID, "now": OBSERVED_AT},
            )
            for point_id, code, kind, metric, data_type, unit in points:
                await connection.execute(
                    text(
                        "INSERT INTO points (id, site_id, facility_id, code, name, point_kind,"
                        " metric_type, data_type, unit, status, created_at, updated_at)"
                        " VALUES (:id, :site_id, :facility_id, :code, :code, :kind, :metric,"
                        " :data_type, :unit, 'active', :now, :now)"
                    ),
                    {
                        "id": point_id,
                        "site_id": SITE_ID,
                        "facility_id": FACILITY_ID,
                        "code": code,
                        "kind": kind,
                        "metric": metric,
                        "data_type": data_type,
                        "unit": unit,
                        "now": OBSERVED_AT,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO simulation_runs (id, control_zone_id, status, model_version,"
                    " speed_multiplier, parameters, step_index, created_at, updated_at)"
                    " VALUES (:id, :zone_id, 'stopped', 'simple-climate-v2', 600,"
                    " CAST('{}' AS jsonb), 4, :now, :now)"
                ),
                {"id": RUN_ID, "zone_id": ZONE_ID, "now": OBSERVED_AT},
            )
            for sample_id, point_id, value, unit in samples:
                await connection.execute(
                    text(
                        "INSERT INTO telemetry_samples (id, point_id, simulation_run_id, value,"
                        " unit, observed_at, received_at, quality)"
                        " VALUES (:id, :point_id, :run_id, CAST(:value AS jsonb), :unit,"
                        " :now, :now, 'simulated')"
                    ),
                    {
                        "id": sample_id,
                        "point_id": point_id,
                        "run_id": RUN_ID,
                        "value": json.dumps(value),
                        "unit": unit,
                        "now": OBSERVED_AT,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO point_current_states (point_id, value, observed_at,"
                        " received_at, quality, revision, updated_at)"
                        " VALUES (:point_id, CAST(:value AS jsonb), :now, :now,"
                        " 'simulated', 1, :now)"
                    ),
                    {"point_id": point_id, "value": json.dumps(value), "now": OBSERVED_AT},
                )
            await connection.execute(
                text(
                    "INSERT INTO control_loops (id, control_zone_id, measurement_point_id,"
                    " control_point_id, status_point_id, policy_type, lower_threshold,"
                    " upper_threshold, created_at)"
                    " VALUES (:id, :zone_id, :measurement_id, :control_id, :status_id,"
                    " 'hysteresis-v1', 24.0, 26.0, :now)"
                ),
                {
                    "id": LOOP_ID,
                    "zone_id": ZONE_ID,
                    "measurement_id": TEMPERATURE_ID,
                    "control_id": FAN_POWER_ID,
                    "status_id": FAN_RUNNING_ID,
                    "now": OBSERVED_AT,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO commands (id, idempotency_key, control_loop_id,"
                    " trigger_sample_id, target_point_id, reported_point_id, desired_value,"
                    " state, result_control_sample_id, result_status_sample_id, issued_at,"
                    " executed_at, acknowledged_at, created_at)"
                    " VALUES (:id, 'hysteresis-v1:preserved', :loop_id, :trigger_id,"
                    " :control_id, :status_id, true, 'applied', :control_sample_id,"
                    " :status_sample_id, :now, :now, :now, :now)"
                ),
                {
                    "id": COMMAND_ID,
                    "loop_id": LOOP_ID,
                    "trigger_id": TRIGGER_SAMPLE_ID,
                    "control_id": FAN_POWER_ID,
                    "status_id": FAN_RUNNING_ID,
                    "control_sample_id": CONTROL_SAMPLE_ID,
                    "status_sample_id": STATUS_SAMPLE_ID,
                    "now": OBSERVED_AT,
                },
            )
    finally:
        await engine.dispose()


async def recorded_data(database_url: str) -> dict[str, Any]:
    """Read back the measured values, the projection and the command history.

    Args:
        database_url: The database to inspect, at any revision.

    Returns:
        The three collections in deterministic order, comparable across
        revisions because none of the columns read belongs to the removal.
    """
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            samples = await connection.execute(
                text("SELECT id, point_id, value, unit, quality FROM telemetry_samples ORDER BY id")
            )
            states = await connection.execute(
                text(
                    "SELECT point_id, value, quality, revision FROM point_current_states"
                    " ORDER BY point_id"
                )
            )
            commands = await connection.execute(
                text(
                    "SELECT id, control_loop_id, trigger_sample_id, desired_value, state"
                    " FROM commands ORDER BY id"
                )
            )
            return {
                "samples": [tuple(row) for row in samples],
                "states": [tuple(row) for row in states],
                "commands": [tuple(row) for row in commands],
            }
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
    assert await foreign_key_deferral(
        scratch_database,
        "fk_edge_telemetry_messages_sample_id_telemetry_samples",
    ) == (True, True)


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


async def test_the_schema_at_head_carries_no_simulation_object(
    scratch_database: str,
) -> None:
    """Executable simulation left the cloud, and so did everything it persisted.

    Asserted against PostgreSQL rather than against the declarative metadata,
    because a dropped model with a surviving table is exactly the failure a
    removal migration can have. Names are matched loosely on purpose: an index
    or a check constraint nobody remembered would still contain the word.
    """
    assert run_alembic(scratch_database, "upgrade", "head").returncode == 0
    tables = await table_names(scratch_database)
    telemetry_columns = await column_names(scratch_database, "telemetry_samples")
    constraints, indexes = await relation_names(scratch_database)

    assert [name for name in tables if "simulation" in name] == []
    assert "simulation_run_id" not in telemetry_columns
    assert [name for name in constraints if "simulation" in name] == []
    assert [name for name in indexes if "simulation" in name] == []


async def test_removing_the_simulation_domain_preserves_recorded_data(
    scratch_database: str,
) -> None:
    """Telemetry, current state and command history survive the removal.

    The rows are written against the schema *before* the removal, including a
    sample that names a simulation run, because the row a simulator produced is
    the one most at risk of being deleted along with the concept. It is
    telemetry, and it stays.

    The round trip is the whole assertion: upgrade, then downgrade back to the
    revision the data was written on, then upgrade again. A downgrade that
    reconstructs the wrong shape fails on the second upgrade rather than
    quietly.
    """
    before = run_alembic(scratch_database, "upgrade", REVISION_BEFORE_SIMULATION_REMOVAL)
    assert before.returncode == 0, before.stderr
    await seed_pre_removal_rows(scratch_database)

    upgrade = run_alembic(scratch_database, "upgrade", "head")
    preserved = await recorded_data(scratch_database)
    downgrade = run_alembic(scratch_database, "downgrade", REVISION_BEFORE_SIMULATION_REMOVAL)
    after_downgrade = await recorded_data(scratch_database)
    upgrade_again = run_alembic(scratch_database, "upgrade", "head")

    assert upgrade.returncode == 0, upgrade.stderr
    assert downgrade.returncode == 0, downgrade.stderr
    assert upgrade_again.returncode == 0, upgrade_again.stderr
    assert preserved == EXPECTED_RECORDED_DATA
    assert after_downgrade == EXPECTED_RECORDED_DATA
    assert await recorded_data(scratch_database) == EXPECTED_RECORDED_DATA
    assert "simulation_run_id" not in await column_names(scratch_database, "telemetry_samples")
    assert run_alembic(scratch_database, "check").returncode == 0
