"""The reads the owner dashboard performs, in the order it performs them.

The dashboard is the separate `greenhouse-dashboard` application and is not run
by this suite, so what is asserted here is the backend side of that contract:
that the existing public endpoints answer everything one frame of the dashboard
needs, whoever produced the data. That is also the claim that no aggregate
dashboard endpoint is required — a claim this test would fail if it stopped
being true, and the reason moving the frontend out of this repository changed
none of the API it reads.

Three frames are covered: an empty cloud, which is what a clean deployment
serves until a client provisions something; a facility nothing has measured
yet, which is the no-data presentation; and one filled by measurements that
arrived over the public Edge boundary, which is the normal one. The cloud runs
no simulator and creates no facility of its own, so both later frames are
produced the way a real gateway produces them.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.gateways.service import GatewayConfigurationService
from tests.integration.factories import create_climate_growbox, create_control_loop

API_URL: str = "/api/v1"
EDGE_TELEMETRY_URL: str = f"{API_URL}/edge/telemetry"
NOW: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
HISTORY_LIMIT: int = 60

DASHBOARD_POINT_CODES: frozenset[str] = frozenset(
    {"air_temperature", "air_humidity", "fan_power", "fan_running"}
)

MEASUREMENTS: tuple[tuple[str, float], ...] = (
    ("air_temperature", 22.0),
    ("air_humidity", 61.0),
    ("air_temperature", 22.5),
    ("air_temperature", 23.0),
)
"""Readings inside the configured band, so the frame carries no command."""


async def _get(http_client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Read one successful JSON resource."""
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _first(http_client: httpx.AsyncClient, path: str) -> dict[str, Any] | None:
    """Take the first member of a collection, as the page does."""
    items = (await _get(http_client, path))["items"]
    return cast(dict[str, Any], items[0]) if items else None


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


async def test_the_dashboard_discovery_finds_nothing_in_an_empty_cloud(
    http_client: httpx.AsyncClient,
) -> None:
    """The first read of a clean deployment succeeds and returns no facility.

    An empty cloud is a supported state and not an error, which is what the
    dashboard's explicit empty state is written from. Nothing has provisioned
    anything in this test, and nothing in the application did it either.
    """
    sites = await _get(http_client, f"{API_URL}/sites")
    facilities = await _get(http_client, f"{API_URL}/facilities")

    assert sites["items"] == []
    assert sites["total"] == 0
    assert facilities["items"] == []


async def test_the_dashboard_reads_its_frame_from_existing_endpoints(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """Take the configured facility, then read a no-data frame and a measured one."""
    provisioned = await create_climate_growbox(http_client)
    loop = await create_control_loop(http_client, provisioned)

    site = await _first(http_client, f"{API_URL}/sites")
    assert site is not None
    facility = await _first(http_client, f"{API_URL}/facilities?site_id={site['id']}")
    assert facility is not None
    control_zone = await _first(
        http_client,
        f"{API_URL}/control-zones?facility_id={facility['id']}",
    )
    assert control_zone is not None
    points = {
        point["code"]: point
        for point in (await _get(http_client, f"{API_URL}/points?facility_id={facility['id']}"))[
            "items"
        ]
    }
    control_loop = await _first(
        http_client,
        f"{API_URL}/control-loops?control_zone_id={control_zone['id']}",
    )

    assert facility["id"] == provisioned.facility["id"]
    assert control_loop is not None
    assert control_loop["id"] == loop["id"]
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
    empty_commands = await _get(
        http_client,
        f"{API_URL}/commands?control_loop_id={control_loop['id']}&limit=20",
    )
    assert empty_history["items"] == []
    assert empty_commands["items"] == []

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
    humidity = await _get(http_client, f"{API_URL}/points/{points['air_humidity']['id']}/state")
    fan_running = await _get(http_client, f"{API_URL}/points/{points['fan_running']['id']}/state")
    history = await _get(
        http_client,
        f"{API_URL}/points/{points['air_temperature']['id']}/telemetry?limit={HISTORY_LIMIT}",
    )

    assert isinstance(temperature["value"], float)
    assert temperature["value"] == MEASUREMENTS[-1][1]
    assert temperature["quality"] == "good"
    assert humidity["value"] == 61.0
    # Every temperature stayed inside the band, so the loop decided nothing and
    # the fan is still unmeasured. The dashboard shows that as "No data" and
    # offers no way to change it.
    assert fan_running["value"] is None
    assert fan_running["quality"] == "no_data"
    assert (
        await _get(
            http_client,
            f"{API_URL}/commands?control_loop_id={control_loop['id']}&limit=20",
        )
    )["items"] == []
    assert len(history["items"]) == 3
    observed = [sample["observed_at"] for sample in history["items"]]
    assert observed == sorted(observed, reverse=True)
    assert history["items"][0]["value"] == temperature["value"]
