"""The complete `0.1` demonstration, from bootstrap to a closed control cycle.

This is the one test that owns the full cycle. It does not re-assert the focused
guarantees the earlier stories already cover — the v2 formula, the dashboard's
delivery, the lifecycle conflicts — it asserts the thing none of them can see on
its own: that a bootstrapped growbox, driven only through the endpoints the
browser uses, produces `OFF → ON → OFF` in persisted state.

Virtual time is injected. There is no ``sleep`` here, and a scenario that needed
one would be measuring the test runner rather than the system.
"""

from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.seed.__main__ import run_demo_init_command
from ai_greenhouse.seed.demo_init import DEMO_LOWER_THRESHOLD, DEMO_UPPER_THRESHOLD
from tests.integration.factories import count_rows
from tests.integration.simulation.helpers import ManualClock, ManualTicker, install_runtime

API_URL: str = "/api/v1"
NOW: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

DEMO_RUN: dict[str, float | int] = {
    "speed_multiplier": 600,
    "initial_temperature": 22.0,
    "initial_humidity": 65.0,
    "ambient_temperature": 30.0,
    "ambient_humidity": 50.0,
}
"""The parameters the dashboard's Start action sends."""

TICK_BUDGET: int = 30
"""Ticks the cycle may take before the test gives up.

Well above the ten the documented coefficients need, and low enough that a model
which stopped converging fails instead of running forever.
"""


async def _get(http_client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Read one successful JSON resource."""
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _find_by_code(http_client: httpx.AsyncClient, path: str, code: str) -> dict[str, Any]:
    """Resolve one collection member by its stable code, as the dashboard does."""
    items = (await _get(http_client, path))["items"]
    match = next((item for item in items if item["code"] == code), None)
    assert match is not None, f"no resource with code {code!r} at {path}"
    return match


async def _discover(http_client: httpx.AsyncClient) -> dict[str, Any]:
    """Walk the dashboard's discovery sequence and return what it resolves."""
    site = await _find_by_code(http_client, f"{API_URL}/sites", "home")
    facility = await _find_by_code(
        http_client,
        f"{API_URL}/facilities?site_id={site['id']}",
        "basil-growbox",
    )
    control_zone = await _find_by_code(
        http_client,
        f"{API_URL}/control-zones?facility_id={facility['id']}",
        "main-climate",
    )
    points = {
        point["code"]: point
        for point in (await _get(http_client, f"{API_URL}/points?facility_id={facility['id']}"))[
            "items"
        ]
    }
    loops = await _get(
        http_client,
        f"{API_URL}/control-loops?control_zone_id={control_zone['id']}",
    )
    return {
        "facility": facility,
        "control_zone": control_zone,
        "points": points,
        "control_loops": loops["items"],
    }


async def _commands(http_client: httpx.AsyncClient, control_loop_id: str) -> list[dict[str, Any]]:
    """Read the loop's applied commands, newest first, as the dashboard does."""
    return (await _get(http_client, f"{API_URL}/commands?control_loop_id={control_loop_id}"))[
        "items"
    ]


async def _temperature(http_client: httpx.AsyncClient, point_id: str) -> float:
    """Read the current temperature the way the dashboard reads it."""
    return float((await _get(http_client, f"{API_URL}/points/{point_id}/state"))["value"])


async def test_the_zero_one_demo_bootstraps_and_closes_the_control_cycle(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    database_settings: Settings,
) -> None:
    """Bootstrap twice, then drive one run through `OFF → ON → OFF` and Stop."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_init_command(database_settings, session_factory=session_factory) == 0

    growbox = await _discover(http_client)
    assert set(growbox["points"]) >= {
        "air_temperature",
        "air_humidity",
        "fan_power",
        "fan_running",
    }
    assert len(growbox["control_loops"]) == 1
    loop = growbox["control_loops"][0]
    assert loop["policy_type"] == "hysteresis-v1"
    assert loop["lower_threshold"] == float(DEMO_LOWER_THRESHOLD)
    assert loop["upper_threshold"] == float(DEMO_UPPER_THRESHOLD)
    # A ready demo is a configured growbox and nothing more: the reader presses
    # Start themselves.
    assert await count_rows(connection, "simulation_runs") == 0
    assert await count_rows(connection, "telemetry_samples") == 0
    assert await count_rows(connection, "commands") == 0

    assert await run_demo_init_command(database_settings, session_factory=session_factory) == 0
    repeated = await _discover(http_client)
    assert len(repeated["control_loops"]) == 1
    assert repeated["control_loops"][0]["id"] == loop["id"]
    assert await count_rows(connection, "sites") == 1
    assert await count_rows(connection, "facilities") == 1
    assert await count_rows(connection, "control_zones") == 1
    assert await count_rows(connection, "points") == 4
    assert await count_rows(connection, "zone_point_assignments") == 4

    temperature_id = growbox["points"]["air_temperature"]["id"]
    fan_power_id = growbox["points"]["fan_power"]["id"]
    fan_running_id = growbox["points"]["fan_running"]["id"]
    ticker = ManualTicker()
    clock = ManualClock(NOW)
    runtime = install_runtime(app, clock=clock, ticker=ticker)

    created = await http_client.post(
        f"{API_URL}/simulation-runs",
        json={"control_zone_id": growbox["control_zone"]["id"], **DEMO_RUN},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    started = await http_client.post(f"{API_URL}/simulation-runs/{run_id}/start")
    assert started.status_code == 202, started.text
    assert await _temperature(http_client, temperature_id) == DEMO_RUN["initial_temperature"]

    warming_ticks = 0
    while len(await _commands(http_client, loop["id"])) == 0 and warming_ticks < TICK_BUDGET:
        clock.advance(seconds=1)
        await ticker.tick()
        warming_ticks += 1
    switched_on = await _commands(http_client, loop["id"])
    temperature_at_on = await _temperature(http_client, temperature_id)

    assert len(switched_on) == 1
    assert switched_on[0]["desired_value"] is True
    assert switched_on[0]["target_point_id"] == fan_power_id
    assert temperature_at_on > float(DEMO_UPPER_THRESHOLD)
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["value"] is True
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["value"] is True

    clock.advance(seconds=1)
    await ticker.tick()
    assert await _temperature(http_client, temperature_id) < temperature_at_on

    cooling_ticks = 1
    while len(await _commands(http_client, loop["id"])) == 1 and cooling_ticks < TICK_BUDGET:
        clock.advance(seconds=1)
        await ticker.tick()
        cooling_ticks += 1
    switched_off = await _commands(http_client, loop["id"])
    temperature_at_off = await _temperature(http_client, temperature_id)

    assert len(switched_off) == 2
    assert switched_off[0]["desired_value"] is False
    assert temperature_at_off < float(DEMO_LOWER_THRESHOLD)
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["value"] is False
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["value"] is False
    # Two commands and two revisions each: the temperatures inside the band
    # changed nothing, and nothing recorded that they were evaluated.
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["revision"] == 2
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["revision"] == 2

    stopped = await http_client.post(f"{API_URL}/simulation-runs/{run_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"
    assert runtime.running_task_count == 0
    samples_at_stop = await count_rows(connection, "telemetry_samples")

    ticker.release()
    ticker.release()
    assert await count_rows(connection, "telemetry_samples") == samples_at_stop

    # What the browser reads after a reload: the persisted run, the history behind
    # the chart and the commands behind recent activity.
    reloaded_run = await _get(http_client, f"{API_URL}/simulation-runs/{run_id}")
    history = (await _get(http_client, f"{API_URL}/points/{temperature_id}/telemetry?limit=60"))[
        "items"
    ]
    activity_commands = await _commands(http_client, loop["id"])

    assert reloaded_run["status"] == "stopped"
    assert reloaded_run["model_version"] == "simple-climate-v2"
    assert reloaded_run["step_index"] == warming_ticks + cooling_ticks + 1
    observed = [sample["observed_at"] for sample in history]
    assert observed == sorted(observed, reverse=True)
    assert [command["desired_value"] for command in activity_commands] == [False, True]
    assert history[0]["value"] == temperature_at_off


async def test_a_conflicting_control_loop_stops_the_bootstrap_unchanged(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    database_settings: Settings,
) -> None:
    """A growbox configured differently is data, not something to overwrite."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_init_command(database_settings, session_factory=session_factory) == 0
    growbox = await _discover(http_client)
    existing = growbox["control_loops"][0]

    conflicting = await http_client.post(
        f"{API_URL}/control-loops",
        json={
            "control_zone_id": growbox["control_zone"]["id"],
            "measurement_point_id": growbox["points"]["air_temperature"]["id"],
            "control_point_id": growbox["points"]["fan_power"]["id"],
            "status_point_id": growbox["points"]["fan_running"]["id"],
            "lower_threshold": 18.0,
            "upper_threshold": 32.0,
        },
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "control_loop_exists"

    unchanged = await _discover(http_client)
    assert unchanged["control_loops"] == [existing]


@pytest.mark.parametrize(
    ("lower", "upper"),
    [(18.0, 32.0), (24.0, 25.0)],
)
async def test_an_incompatible_band_is_reported_and_not_rewritten(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    lower: float,
    upper: float,
) -> None:
    """The bootstrap refuses a band it did not configure and changes nothing."""
    from ai_greenhouse.seed.demo import seed_demo
    from ai_greenhouse.seed.demo_init import DemoConfigurationConflictError, initialize_demo

    topology = await seed_demo(session)
    await session.commit()
    foreign = await http_client.post(
        f"{API_URL}/control-loops",
        json={
            "control_zone_id": str(topology.control_zone_id),
            "measurement_point_id": str(topology.point_ids["air_temperature"]),
            "control_point_id": str(topology.point_ids["fan_power"]),
            "status_point_id": str(topology.point_ids["fan_running"]),
            "lower_threshold": lower,
            "upper_threshold": upper,
        },
    )
    assert foreign.status_code == 201, foreign.text

    with pytest.raises(DemoConfigurationConflictError):
        await initialize_demo(session)

    await session.rollback()
    loops = (
        await _get(
            http_client,
            f"{API_URL}/control-loops?control_zone_id={topology.control_zone_id}",
        )
    )["items"]
    assert len(loops) == 1
    assert loops[0]["lower_threshold"] == lower
    assert loops[0]["upper_threshold"] == upper
