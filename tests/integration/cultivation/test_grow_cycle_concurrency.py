"""What two transactions activating at once actually do, on real PostgreSQL.

Every other test in this package runs inside one rolled-back transaction, which
is what keeps them fast and isolated — and which is exactly why the concurrency
guarantees cannot be asserted there. A row lock and a partial unique index only
mean anything between *separate* transactions on *separate* connections, so this
module builds its own engine, commits for real, and cleans up after itself.

Two guarantees are asserted, and neither of them is a Python lock:

1. Two activations of one cycle converge. The row lock serialises them, and the
   second sees the first one's result instead of repeating its writes.
2. Two activations resolving to one control loop do not both win. The partial
   unique index refuses the second active target, and the loser's whole
   transaction rolls back — its cycle is still planned, with no stage instance
   and no target of its own.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, NamedTuple
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings
from ai_greenhouse.cultivation.exceptions import GrowCycleTargetConflictError
from ai_greenhouse.cultivation.schemas import GrowCycleRead
from ai_greenhouse.cultivation.service import GrowCycleService
from tests.integration.factories import (
    CycleEnvironment,
    create_cycle_environment,
    create_grow_cycle,
)

COMMITTED_TABLES: tuple[str, ...] = (
    "runtime_targets",
    "grow_stage_instances",
    "grow_cycle_zone_assignments",
    "grow_cycles",
    "commands",
    "control_loops",
    "zone_point_assignments",
    "point_current_states",
    "telemetry_samples",
    "points",
    "control_zones",
    "facilities",
    "sites",
    "target_requirements",
    "recipe_stages",
    "recipe_versions",
    "growing_recipes",
    "crops",
)
"""Everything this module commits, emptied again however a test ends.

The rest of the suite rolls its transaction back and leaves nothing behind, so a
row committed here and forgotten would be a row the next test sees.
"""


class CommittedFixture(NamedTuple):
    """A provisioned environment that really exists in the database."""

    sessions: async_sessionmaker[AsyncSession]
    environment: CycleEnvironment


async def count(engine: AsyncEngine, table: str) -> int:
    """Count the committed rows of one table.

    Args:
        engine: The engine to read through.
        table: The table to count. Never taken from request input.

    Returns:
        The number of rows visible to a fresh transaction.
    """
    async with engine.connect() as connection:
        total = await connection.scalar(text(f"SELECT count(*) FROM {table}"))
    return int(total or 0)


@pytest.fixture
async def committed(migrated_database: str) -> AsyncIterator[CommittedFixture]:
    """Provision a cycle environment that survives the request that created it.

    Args:
        migrated_database: The URL of the migrated database.

    Yields:
        A session factory on its own engine, and the provisioned environment.
    """
    engine: AsyncEngine = create_async_engine(migrated_database, poolclass=NullPool)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    application = create_app(
        Settings(database_url=migrated_database, app_env="test", _env_file=None)
    )
    application.state.session_factory = sessions
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield CommittedFixture(sessions, await create_cycle_environment(client))
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"TRUNCATE {', '.join(COMMITTED_TABLES)} RESTART IDENTITY CASCADE")
            )
        await engine.dispose()


async def activate(
    sessions: async_sessionmaker[AsyncSession],
    grow_cycle_id: str,
) -> GrowCycleRead | Exception:
    """Activate one cycle in a transaction of its own.

    Commit and rollback are done here exactly as ``get_session`` does them for a
    request, so what runs is the production transaction boundary rather than an
    approximation of it.

    Args:
        sessions: The factory to open the transaction from.
        grow_cycle_id: The cycle to activate.

    Returns:
        The activated cycle, or the domain failure that refused it.
    """
    async with sessions() as session:
        try:
            activated = await GrowCycleService(session).activate_cycle(UUID(grow_cycle_id))
        except Exception as failure:  # noqa: BLE001 - the failure is the assertion
            await session.rollback()
            return failure
        await session.commit()
        return activated


async def plan(
    sessions: async_sessionmaker[AsyncSession],
    environment: CycleEnvironment,
    **overrides: Any,
) -> dict[str, Any]:
    """Plan one cycle and commit it, so another transaction can see it.

    Args:
        sessions: The factory the planning request runs on.
        environment: The facility, zone and version the cycle joins.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The created planned cycle.
    """
    application = create_app(Settings(app_env="test", _env_file=None))
    application.state.session_factory = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        return await create_grow_cycle(client, environment, **overrides)


async def test_two_activations_of_one_cycle_converge(
    committed: CommittedFixture,
    migrated_database: str,
) -> None:
    """The row lock is what makes the second call a read rather than a second write."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    cycle = await plan(committed.sessions, committed.environment)

    try:
        first, second = await asyncio.gather(
            activate(committed.sessions, cycle["id"]),
            activate(committed.sessions, cycle["id"]),
        )

        assert isinstance(first, GrowCycleRead), first
        assert isinstance(second, GrowCycleRead), second
        assert first.started_at == second.started_at
        assert first.active_runtime_target is not None
        assert second.active_runtime_target is not None
        assert first.active_runtime_target.id == second.active_runtime_target.id
        assert await count(engine, "grow_stage_instances") == 1
        assert await count(engine, "runtime_targets") == 1
    finally:
        await engine.dispose()


async def test_two_cycles_racing_for_one_loop_leave_one_winner(
    committed: CommittedFixture,
    migrated_database: str,
) -> None:
    """The partial unique index decides it, and the loser rolls back whole."""
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    first_cycle = await plan(committed.sessions, committed.environment, code="first-cycle")
    second_cycle = await plan(committed.sessions, committed.environment, code="second-cycle")

    try:
        results = await asyncio.gather(
            activate(committed.sessions, first_cycle["id"]),
            activate(committed.sessions, second_cycle["id"]),
        )
        winners = [result for result in results if isinstance(result, GrowCycleRead)]
        losers = [result for result in results if isinstance(result, Exception)]

        assert len(winners) == 1, results
        assert len(losers) == 1, results
        assert isinstance(losers[0], GrowCycleTargetConflictError)
        assert losers[0].code == "grow_cycle_target_conflict"
        assert losers[0].http_status == 409
        assert await count(engine, "grow_stage_instances") == 1
        assert await count(engine, "runtime_targets") == 1

        loser_id = (
            second_cycle["id"] if winners[0].id == UUID(first_cycle["id"]) else first_cycle["id"]
        )
        async with engine.connect() as connection:
            status = await connection.scalar(
                text("SELECT status FROM grow_cycles WHERE id = :cycle_id"),
                {"cycle_id": loser_id},
            )
            started_at = await connection.scalar(
                text("SELECT started_at FROM grow_cycles WHERE id = :cycle_id"),
                {"cycle_id": loser_id},
            )
            stage_instances = await connection.scalar(
                text("SELECT count(*) FROM grow_stage_instances WHERE grow_cycle_id = :cycle_id"),
                {"cycle_id": loser_id},
            )
            targets = await connection.scalar(
                text("SELECT count(*) FROM runtime_targets WHERE grow_cycle_id = :cycle_id"),
                {"cycle_id": loser_id},
            )

        assert status == "planned"
        assert started_at is None
        assert stage_instances == 0
        assert targets == 0
    finally:
        await engine.dispose()
