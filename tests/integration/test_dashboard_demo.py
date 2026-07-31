"""The reads the dashboard page performs, in the order it performs them.

The page is plain JavaScript and there is no browser in the test suite, so what
is asserted here is the layer below the rendering: that the existing public
endpoints answer everything one frame of the dashboard needs, addressed only by
stable codes. That is also the milestone's claim that no aggregate dashboard
endpoint is required — a claim this test would fail if it stopped being true.

Two frames are covered: a seeded growbox nothing has measured yet, which is the
no-data and empty-history presentation, and one filled by measurements that
arrived over the public Edge boundary, which is the normal one. The cloud runs
no simulator, so the second frame is produced the way a real gateway produces
it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.gateways.service import GatewayConfigurationService
from ai_greenhouse.seed.__main__ import run_demo_command

API_URL: str = "/api/v1"
EDGE_TELEMETRY_URL: str = f"{API_URL}/edge/telemetry"
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

MEASUREMENTS: tuple[tuple[str, float], ...] = (
    ("air_temperature", 22.0),
    ("air_temperature", 22.5),
    ("air_temperature", 23.0),
)
"""Three temperatures inside the demo band, so the frame carries no command."""


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


def _envelope(gateway_id: UUID, point: dict[str, Any], value: Any, observed_at: datetime) -> dict:
    """Build one exact v1 telemetry envelope carrying a single message."""
    return {
        "contract_version": "1.0",
        "gateway_id": str(gateway_id),
        "messages": [
            {
                "message_id": str(uuid4()),
                "point_id": point["id"],
                "data_type": point["data_type"],
                "value": value,
                "observed_at": observed_at.isoformat(),
                "quality": "good",
                "source": {"kind": "sensor", "id": f"test.{point['code']}"},
            }
        ],
    }


async def test_the_dashboard_reads_its_frame_from_existing_endpoints(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    database_settings: Settings,
) -> None:
    """Discover the growbox by code, then read a no-data frame and a measured one."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_command(database_settings, session_factory=session_factory) == 0

    site = await _find_by_code(http_client, f"{API_URL}/sites", DEMO_CODES["site"])
    facility = await _find_by_code(
        http_client,
        f"{API_URL}/facilities?site_id={site['id']}",
        DEMO_CODES["facility"],
    )
    await _find_by_code(
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
    assert empty_history["items"] == []

    gateway = await GatewayConfigurationService(session).create(
        site_id=UUID(site["id"]),
        point_ids=[UUID(points[code]["id"]) for code in sorted(DASHBOARD_POINT_CODES)],
    )
    await session.commit()
    for index, (code, value) in enumerate(MEASUREMENTS):
        accepted = await http_client.post(
            EDGE_TELEMETRY_URL,
            json=_envelope(
                gateway.id,
                points[code],
                value,
                NOW + timedelta(minutes=index),
            ),
        )
        assert accepted.status_code == 200, accepted.text

    temperature = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/state",
    )
    fan_running = await _get(http_client, f"{API_URL}/points/{points['fan_running']['id']}/state")
    history = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/telemetry?limit={HISTORY_LIMIT}",
    )

    assert isinstance(temperature["value"], float)
    assert temperature["value"] == MEASUREMENTS[-1][1]
    assert temperature["quality"] == "good"
    # No control loop is configured in the plain demo seed, so the fan is still
    # unmeasured. The dashboard shows that as "No data" and offers no way to
    # change it.
    assert fan_running["value"] is None
    assert fan_running["quality"] == "no_data"
    assert len(history["items"]) == len(MEASUREMENTS)
    observed = [sample["observed_at"] for sample in history["items"]]
    assert observed == sorted(observed, reverse=True)
    assert history["items"][0]["value"] == temperature["value"]
