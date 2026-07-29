"""Integration coverage for the idempotent demo seed command."""

import json
import os
import subprocess
import sys
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.core.logging import configure_logging
from ai_greenhouse.points.models import Point, PointCurrentState
from ai_greenhouse.seed.__main__ import run_demo_command
from ai_greenhouse.topology.models import ControlZone, Facility, Site, ZonePointAssignment

COUNTED_MODELS: tuple[type[Any], ...] = (
    Site,
    Facility,
    ControlZone,
    Point,
    PointCurrentState,
    ZonePointAssignment,
)
"""All six topology tables whose row counts the seed may affect."""


async def _entity_counts(connection: AsyncConnection) -> dict[str, int]:
    """Count every topology table in the current test transaction.

    Args:
        connection: PostgreSQL connection shared with the seed sessions.

    Returns:
        A mapping from table name to row count.
    """
    counts: dict[str, int] = {}
    for model in COUNTED_MODELS:
        statement: Select[tuple[int]] = select(func.count()).select_from(model)
        result = await connection.execute(statement)
        counts[model.__tablename__] = int(result.scalar_one())
    return counts


async def test_demo_seed_is_idempotent_and_logs_identifiers(
    connection: AsyncConnection,
    database_settings: Settings,
    capsys: Any,
) -> None:
    """A second successful command run creates no additional records."""
    configure_logging(database_settings)
    session_factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    first_exit_code: int = await run_demo_command(
        database_settings,
        session_factory=session_factory,
    )
    first_counts: dict[str, int] = await _entity_counts(connection)
    second_exit_code: int = await run_demo_command(
        database_settings,
        session_factory=session_factory,
    )
    second_counts: dict[str, int] = await _entity_counts(connection)

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert first_counts == {
        "sites": 1,
        "facilities": 1,
        "control_zones": 1,
        "points": 4,
        "point_current_states": 4,
        "zone_point_assignments": 4,
    }
    assert second_counts == first_counts

    records: list[dict[str, Any]] = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line
    ]
    completions: list[dict[str, Any]] = [
        record for record in records if record.get("event") == "demo_seed_complete"
    ]
    assert len(completions) == 2
    assert completions[-1]["site_id"]
    assert completions[-1]["facility_id"]
    assert completions[-1]["control_zone_id"]
    assert set(completions[-1]["point_ids"]) == {
        "air_temperature",
        "air_humidity",
        "fan_power",
        "fan_running",
    }


def test_demo_seed_process_exits_nonzero_when_database_is_unreachable() -> None:
    """The real module process translates connection refusal to exit code one."""
    environment: dict[str, str] = os.environ | {
        "DATABASE_URL": (
            "postgresql+asyncpg://seed_user:not-a-real-password-9f3c@127.0.0.1:1/unreachable"
        )
    }

    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "ai_greenhouse.seed", "demo"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    records: list[dict[str, Any]] = [
        json.loads(line) for line in result.stdout.splitlines() if line
    ]
    assert any(record.get("event") == "demo_seed_failed" for record in records)
