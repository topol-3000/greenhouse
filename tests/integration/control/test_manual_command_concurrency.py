"""Two clients, one key, one command — proved against real PostgreSQL sessions.

Every other integration test in this suite runs inside one rolled-back
transaction, which is exactly the wrong shape for this question: two requests
sharing a transaction cannot race, and a service-level "look it up, then insert
it" would pass there while losing a real race in production.

So this module gives up the shared transaction. It creates a database of its
own, migrates it, and drives an ordinary application against it — separate
sessions, separate connections, real commits — then drops the database again. A
concurrent duplicate here is a concurrent duplicate a customer could actually
cause by double-tapping a button on a slow connection.
"""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings
from tests.integration.factories import (
    COMMANDS_URL,
    IDEMPOTENCY_HEADER,
    create_commandable_growbox,
    manual_command_body,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

CONCURRENCY_DATABASE_NAME: str = "ai_greenhouse_concurrency_check"
"""A database of this module's own, so its committed rows disturb nothing."""

MAINTENANCE_DATABASE_NAME: str = "postgres"

CONCURRENT_REQUESTS: int = 8
"""Enough simultaneous requests that a lost race is not a coin flip.

The window a check-then-insert leaves open is small, so one pair of requests
would find it only sometimes. Eight either all converge on one row or they do
not.
"""


@pytest.fixture
async def live_database(database_url: str) -> AsyncIterator[str]:
    """Yield a migrated database that real transactions may commit to.

    Args:
        database_url: The configured database, used only for its connection
            details; its own schema is never touched.

    Yields:
        The URL of a freshly migrated, empty database.
    """
    configured: URL = make_url(database_url)
    maintenance_url: URL = configured.set(database=MAINTENANCE_DATABASE_NAME)
    live_url: str = configured.set(database=CONCURRENCY_DATABASE_NAME).render_as_string(
        hide_password=False
    )

    engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{CONCURRENCY_DATABASE_NAME}" WITH (FORCE)')
            )
            await connection.execute(text(f'CREATE DATABASE "{CONCURRENCY_DATABASE_NAME}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=os.environ | {"DATABASE_URL": live_url},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        yield live_url
        async with engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{CONCURRENCY_DATABASE_NAME}" WITH (FORCE)')
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def live_client(live_database: str) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client whose requests each open their own committing session."""
    application: FastAPI = create_app(
        Settings(database_url=live_database, app_env="test", _env_file=None)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        yield client


async def count_commands(live_database: str) -> int:
    """Count the committed command rows, from outside the application."""
    engine = create_async_engine(live_database)
    try:
        async with engine.connect() as connection:
            return int(await connection.scalar(text("SELECT count(*) FROM commands")) or 0)
    finally:
        await engine.dispose()


async def test_concurrent_identical_requests_create_exactly_one_command(
    live_client: httpx.AsyncClient,
    live_database: str,
) -> None:
    """A double-tapped button switches the fan once.

    Eight requests carrying one key are answered at once. Exactly one is told it
    created the command, the rest are told it already existed, every one of them
    names the same command — and the table holds one row, which is the only part
    the growbox can feel.
    """
    growbox = await create_commandable_growbox(live_client)
    key = str(uuid4())
    body: dict[str, Any] = manual_command_body(growbox)

    responses = await asyncio.gather(
        *(
            live_client.post(COMMANDS_URL, json=body, headers={IDEMPOTENCY_HEADER: key})
            for _ in range(CONCURRENT_REQUESTS)
        )
    )

    statuses = sorted(response.status_code for response in responses)
    identifiers = {response.json()["command"]["id"] for response in responses}
    outcomes = sorted(response.json()["outcome"] for response in responses)

    assert statuses == [200] * (CONCURRENT_REQUESTS - 1) + [201]
    assert outcomes == ["created"] + ["existing"] * (CONCURRENT_REQUESTS - 1)
    assert len(identifiers) == 1
    assert await count_commands(live_database) == 1


async def test_concurrent_conflicting_requests_cannot_create_two_commands(
    live_client: httpx.AsyncClient,
    live_database: str,
) -> None:
    """One key can never mean two things, however the requests interleave.

    Half the requests ask for ``true`` and half for ``false`` under one key. One
    of the two wins the key and the other is refused as a conflict — and no
    interleaving produces two rows, because the uniqueness that decides is the
    database's and not a lookup the service did a moment earlier.
    """
    growbox = await create_commandable_growbox(live_client)
    key = str(uuid4())
    bodies = [
        manual_command_body(growbox, desired_value=index % 2 == 0)
        for index in range(CONCURRENT_REQUESTS)
    ]

    responses = await asyncio.gather(
        *(
            live_client.post(COMMANDS_URL, json=body, headers={IDEMPOTENCY_HEADER: key})
            for body in bodies
        )
    )

    statuses = [response.status_code for response in responses]
    conflicts = [
        response.json()["error"]["code"] for response in responses if response.status_code == 409
    ]

    assert statuses.count(201) == 1
    assert set(conflicts) == {"idempotency_key_conflict"}
    assert statuses.count(409) + statuses.count(200) == CONCURRENT_REQUESTS - 1
    assert await count_commands(live_database) == 1
