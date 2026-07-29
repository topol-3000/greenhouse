"""End-to-end proof of the documented Milestone 2 demonstration."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.seed.__main__ import run_demo_command
from tests.integration.simulation.helpers import ManualClock, ManualTicker, install_runtime

API_URL: str = "/api/v1"
NOW: datetime = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
INITIAL_VALUES: dict[str, float] = {
    "air_temperature": 22.0,
    "air_humidity": 65.0,
}


async def _get_items(http_client: httpx.AsyncClient, path: str) -> list[dict[str, Any]]:
    """Read a successful collection response."""
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_milestone_2_demo_runs_from_seed_through_stop_without_real_time(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    database_settings: Settings,
) -> None:
    """Seed M1, run both simulated points for hours, and stop through HTTP."""
    session_factory = cast(
        async_sessionmaker[AsyncSession],
        app.state.session_factory,
    )
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    site = next(
        item for item in await _get_items(http_client, f"{API_URL}/sites") if item["code"] == "home"
    )
    facility = next(
        item
        for item in await _get_items(
            http_client,
            f"{API_URL}/facilities?site_id={site['id']}",
        )
        if item["code"] == "basil-growbox"
    )
    zone = next(
        item
        for item in await _get_items(
            http_client,
            f"{API_URL}/control-zones?facility_id={facility['id']}",
        )
        if item["code"] == "main-climate"
    )
    points = {
        item["code"]: item
        for item in await _get_items(
            http_client,
            f"{API_URL}/points?facility_id={facility['id']}",
        )
        if item["code"] in INITIAL_VALUES
    }
    assert set(points) == set(INITIAL_VALUES)

    ticker = ManualTicker()
    clock = ManualClock(NOW)
    runtime = install_runtime(app, clock=clock, ticker=ticker)
    created_response = await http_client.post(
        f"{API_URL}/simulation-runs",
        json={
            "control_zone_id": zone["id"],
            "speed_multiplier": 3600,
            "initial_temperature": INITIAL_VALUES["air_temperature"],
            "initial_humidity": INITIAL_VALUES["air_humidity"],
            "ambient_temperature": 30.0,
            "ambient_humidity": 50.0,
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()

    started_response = await http_client.post(f"{API_URL}/simulation-runs/{created['id']}/start")
    assert started_response.status_code == 202, started_response.text
    assert started_response.json()["status"] == "running"
    assert started_response.json()["step_index"] == 1

    for _ in range(4):
        clock.advance(seconds=1)
        await ticker.tick()

    progressed_response = await http_client.get(f"{API_URL}/simulation-runs/{created['id']}")
    assert progressed_response.status_code == 200, progressed_response.text
    progressed = progressed_response.json()
    assert progressed["status"] == "running"
    assert progressed["step_index"] == 5
    assert progressed["virtual_time"] == (NOW + timedelta(hours=4)).isoformat().replace(
        "+00:00", "Z"
    )

    histories: dict[str, list[dict[str, Any]]] = {}
    for code, point in points.items():
        state_response = await http_client.get(f"{API_URL}/points/{point['id']}/state")
        assert state_response.status_code == 200, state_response.text
        state = state_response.json()
        assert state["quality"] == "simulated"
        assert state["revision"] == 5
        assert state["value"] != INITIAL_VALUES[code]

        histories[code] = await _get_items(
            http_client,
            f"{API_URL}/points/{point['id']}/telemetry",
        )
        assert len(histories[code]) == 5
        assert histories[code][0]["value"] == state["value"]
        assert histories[code][0]["observed_at"] == (NOW + timedelta(hours=4)).isoformat().replace(
            "+00:00", "Z"
        )
        assert histories[code][0]["received_at"] == (
            NOW + timedelta(seconds=4)
        ).isoformat().replace("+00:00", "Z")
        assert histories[code][0]["observed_at"] != histories[code][0]["received_at"]

    stopped_response = await http_client.post(f"{API_URL}/simulation-runs/{created['id']}/stop")
    assert stopped_response.status_code == 200, stopped_response.text
    assert stopped_response.json()["status"] == "stopped"
    assert runtime.running_task_count == 0

    sample_ids_at_stop = {
        code: [sample["id"] for sample in history] for code, history in histories.items()
    }
    ticker.release()
    await asyncio.sleep(0)

    stopped_read = await http_client.get(f"{API_URL}/simulation-runs/{created['id']}")
    assert stopped_read.status_code == 200, stopped_read.text
    assert stopped_read.json()["status"] == "stopped"
    for code, point in points.items():
        history_after_stop = await _get_items(
            http_client,
            f"{API_URL}/points/{point['id']}/telemetry",
        )
        assert [sample["id"] for sample in history_after_stop] == sample_ids_at_stop[code]
