"""The reads the dashboard page performs, in the order it performs them.

The page is plain JavaScript and there is no browser in the test suite, so what
is asserted here is the layer below the rendering: that the existing public
endpoints answer everything one frame of the dashboard needs, addressed only by
stable codes. That is also the milestone's claim that no aggregate dashboard
endpoint is required — a claim this test would fail if it stopped being true.

Two frames are covered: a seeded growbox nothing has measured yet, which is the
no-data and empty-history presentation, and a running simulation, which is the
normal one.
"""

from datetime import UTC, datetime
from typing import Any, cast

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.seed.__main__ import run_demo_command
from tests.integration.simulation.helpers import ManualClock, ManualTicker, install_runtime

API_URL: str = "/api/v1"
NOW: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
HISTORY_LIMIT: int = 60

DEMO_CODES: dict[str, str] = {
    "site": "home",
    "facility": "basil-growbox",
    "control_zone": "main-climate",
}
DASHBOARD_POINT_CODES: frozenset[str] = frozenset(
    {"air_temperature", "air_humidity", "fan_power", "fan_running"}
)


async def _get(http_client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Read one successful JSON resource."""
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _find_by_code(http_client: httpx.AsyncClient, path: str, code: str) -> dict[str, Any]:
    """Resolve one collection member by its stable code, as the page does."""
    items = (await _get(http_client, path))["items"]
    match = next((item for item in items if item["code"] == code), None)
    assert match is not None, f"no resource with code {code!r} at {path}"
    return match


async def test_the_dashboard_reads_its_frame_from_existing_endpoints(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    database_settings: Settings,
) -> None:
    """Discover the growbox by code, then read a no-data frame and a live one."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    site = await _find_by_code(http_client, f"{API_URL}/sites", DEMO_CODES["site"])
    facility = await _find_by_code(
        http_client,
        f"{API_URL}/facilities?site_id={site['id']}",
        DEMO_CODES["facility"],
    )
    control_zone = await _find_by_code(
        http_client,
        f"{API_URL}/control-zones?facility_id={facility['id']}",
        DEMO_CODES["control_zone"],
    )
    points = {
        point["code"]: point
        for point in (await _get(http_client, f"{API_URL}/points?facility_id={facility['id']}"))[
            "items"
        ]
    }

    assert facility["name"] == "Basil Growbox"
    assert DASHBOARD_POINT_CODES <= set(points)
    assert points["air_temperature"]["unit"] == "°C"
    assert points["air_humidity"]["unit"] == "%"

    for code in DASHBOARD_POINT_CODES:
        state = await _get(http_client, f"{API_URL}/points/{points[code]['id']}/state")
        assert state["value"] is None
        assert state["quality"] == "no_data"
    empty_history = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/telemetry?limit={HISTORY_LIMIT}",
    )
    latest_runs = await _get(
        http_client,
        f"{API_URL}/simulation-runs?control_zone_id={control_zone['id']}&limit=1",
    )
    assert empty_history["items"] == []
    assert latest_runs["items"] == []

    ticker = ManualTicker()
    clock = ManualClock(NOW)
    install_runtime(app, clock=clock, ticker=ticker)
    created = await http_client.post(
        f"{API_URL}/simulation-runs",
        json={
            "control_zone_id": control_zone["id"],
            "speed_multiplier": 600,
            "initial_temperature": 22.0,
            "initial_humidity": 65.0,
            "ambient_temperature": 30.0,
            "ambient_humidity": 50.0,
        },
    )
    assert created.status_code == 201, created.text
    started = await http_client.post(f"{API_URL}/simulation-runs/{created.json()['id']}/start")
    assert started.status_code == 202, started.text
    for _ in range(2):
        clock.advance(seconds=1)
        await ticker.tick()

    temperature = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/state",
    )
    humidity = await _get(http_client, f"{API_URL}/points/{points['air_humidity']['id']}/state")
    fan_running = await _get(http_client, f"{API_URL}/points/{points['fan_running']['id']}/state")
    history = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/telemetry?limit={HISTORY_LIMIT}",
    )
    running_runs = await _get(
        http_client,
        f"{API_URL}/simulation-runs?control_zone_id={control_zone['id']}&limit=1",
    )

    assert isinstance(temperature["value"], float)
    assert isinstance(humidity["value"], float)
    assert temperature["quality"] == "simulated"
    # No control loop is configured in the plain demo seed, so the fan is still
    # unmeasured. The dashboard shows that as "No data" and offers no way to
    # change it.
    assert fan_running["value"] is None
    assert fan_running["quality"] == "no_data"
    assert len(history["items"]) == 3
    observed = [sample["observed_at"] for sample in history["items"]]
    assert observed == sorted(observed, reverse=True)
    assert history["items"][0]["value"] == temperature["value"]
    assert running_runs["items"][0]["status"] == "running"
    assert running_runs["items"][0]["model_version"] == "simple-climate-v2"

    stopped = await http_client.post(f"{API_URL}/simulation-runs/{created.json()['id']}/stop")
    assert stopped.status_code == 200, stopped.text
