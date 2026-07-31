"""End-to-end proof of the documented fan-automation demonstration.

One scenario, driven the way the README documents it: seed the growbox,
configure the loop over HTTP, offer `27 → 25 → 23` to the ingestion path, and
read the whole chain back through the API.

Everything narrower — the decision itself, replay, out-of-order input, the
actuator rollback — is asserted in ``unit/test_hysteresis_policy.py`` and
``integration/control/test_automation.py`` and is not repeated here. What this
file adds is the part only the complete run can show: that the milestone's
demonstration produces exactly two commands and that their chain is readable.
"""

from typing import Any, cast

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.seed.__main__ import run_automation_demo_command, run_demo_command
from ai_greenhouse.seed.automation import DEMO_TEMPERATURES
from tests.integration.factories import (
    COMMANDS_URL,
    CONTROL_LOOPS_URL,
    LOWER_THRESHOLD,
    UPPER_THRESHOLD,
    count_rows,
)

API_URL: str = "/api/v1"


async def _items(http_client: httpx.AsyncClient, path: str) -> list[dict[str, Any]]:
    """Read a successful collection response.

    Args:
        http_client: The client under test.
        path: The collection to read.

    Returns:
        The decoded ``items`` of the response.
    """
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _state(http_client: httpx.AsyncClient, point_id: str) -> dict[str, Any]:
    """Read one point's current state through the API.

    Args:
        http_client: The client under test.
        point_id: The point whose projection to read.

    Returns:
        The decoded state representation.
    """
    response = await http_client.get(f"{API_URL}/points/{point_id}/state")
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_documented_demo_turns_the_fan_on_holds_it_and_turns_it_off(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    database_settings: Settings,
) -> None:
    """``27 → 25 → 23 °C`` yields exactly ON, no command, OFF — and nothing more."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    site = next(
        item for item in await _items(http_client, f"{API_URL}/sites") if item["code"] == "home"
    )
    facility = next(
        item
        for item in await _items(http_client, f"{API_URL}/facilities?site_id={site['id']}")
        if item["code"] == "basil-growbox"
    )
    zone = next(
        item
        for item in await _items(
            http_client,
            f"{API_URL}/control-zones?facility_id={facility['id']}",
        )
        if item["code"] == "main-climate"
    )
    points = {
        item["code"]: item
        for item in await _items(http_client, f"{API_URL}/points?facility_id={facility['id']}")
    }

    created = await http_client.post(
        CONTROL_LOOPS_URL,
        json={
            "control_zone_id": zone["id"],
            "measurement_point_id": points["air_temperature"]["id"],
            "control_point_id": points["fan_power"]["id"],
            "status_point_id": points["fan_running"]["id"],
            "lower_threshold": LOWER_THRESHOLD,
            "upper_threshold": UPPER_THRESHOLD,
        },
    )
    assert created.status_code == 201, created.text
    loop = created.json()

    assert (
        await run_automation_demo_command(database_settings, session_factory=session_factory) == 0
    )

    commands = await _items(http_client, f"{COMMANDS_URL}?control_loop_id={loop['id']}")
    assert [command["desired_value"] for command in commands] == [False, True]
    assert len(DEMO_TEMPERATURES) == 3

    off_command, on_command = commands
    newest = await _items(http_client, f"{COMMANDS_URL}?control_loop_id={loop['id']}&limit=1")
    assert [command["id"] for command in newest] == [off_command["id"]]
    assert on_command["target_point_id"] == points["fan_power"]["id"]
    assert on_command["idempotency_key"].startswith(f"hysteresis-v1:{loop['id']}:")
    assert on_command["idempotency_key"].endswith(":on")
    assert off_command["idempotency_key"].endswith(":off")

    history = await _items(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/telemetry",
    )
    trigger_ids = {sample["id"] for sample in history}
    assert on_command["trigger_sample_id"] in trigger_ids
    assert off_command["trigger_sample_id"] in trigger_ids
    assert on_command["trigger_sample_id"] != off_command["trigger_sample_id"]

    for command in commands:
        by_trigger = await _items(
            http_client,
            f"{COMMANDS_URL}?trigger_sample_id={command['trigger_sample_id']}",
        )
        assert [item["id"] for item in by_trigger] == [command["id"]]

        read = await http_client.get(f"{COMMANDS_URL}/{command['id']}")
        assert read.status_code == 200, read.text
        assert read.json() == command

        for point_code, field in (
            ("fan_power", "result_control_sample_id"),
            ("fan_running", "result_status_sample_id"),
        ):
            results = await _items(
                http_client,
                f"{API_URL}/points/{points[point_code]['id']}/telemetry",
            )
            recorded = next(item for item in results if item["id"] == command[field])
            assert recorded["value"] is command["desired_value"]

    for code in ("fan_power", "fan_running"):
        state = await _state(http_client, points[code]["id"])
        assert state["value"] is False, code


async def test_re_running_the_demo_changes_nothing(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    database_settings: Settings,
) -> None:
    """The demonstration is idempotent, because its sample identifiers are derived."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    site = next(
        item for item in await _items(http_client, f"{API_URL}/sites") if item["code"] == "home"
    )
    facility = next(
        item
        for item in await _items(http_client, f"{API_URL}/facilities?site_id={site['id']}")
        if item["code"] == "basil-growbox"
    )
    zone = next(
        item
        for item in await _items(
            http_client,
            f"{API_URL}/control-zones?facility_id={facility['id']}",
        )
        if item["code"] == "main-climate"
    )
    points = {
        item["code"]: item
        for item in await _items(http_client, f"{API_URL}/points?facility_id={facility['id']}")
    }
    created = await http_client.post(
        CONTROL_LOOPS_URL,
        json={
            "control_zone_id": zone["id"],
            "measurement_point_id": points["air_temperature"]["id"],
            "control_point_id": points["fan_power"]["id"],
            "status_point_id": points["fan_running"]["id"],
            "lower_threshold": LOWER_THRESHOLD,
            "upper_threshold": UPPER_THRESHOLD,
        },
    )
    assert created.status_code == 201, created.text

    assert (
        await run_automation_demo_command(database_settings, session_factory=session_factory) == 0
    )
    commands_after_first = await count_rows(connection, "commands")
    samples_after_first = await count_rows(connection, "telemetry_samples")

    assert (
        await run_automation_demo_command(database_settings, session_factory=session_factory) == 0
    )

    assert commands_after_first == 2
    assert await count_rows(connection, "commands") == commands_after_first
    assert await count_rows(connection, "telemetry_samples") == samples_after_first


async def test_the_demo_refuses_a_growbox_without_a_control_loop(
    app: FastAPI,
    connection: AsyncConnection,
    database_settings: Settings,
) -> None:
    """A misconfigured growbox fails loudly instead of silently proving nothing."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    exit_code = await run_automation_demo_command(
        database_settings,
        session_factory=session_factory,
    )

    assert exit_code == 1
    assert await count_rows(connection, "commands") == 0
    assert await connection.scalar(text("SELECT count(*) FROM telemetry_samples")) == 0
