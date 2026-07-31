"""The complete `0.1` demonstration, from bootstrap to a closed control cycle.

This is the one test that owns the full cycle. It does not re-assert the focused
guarantees the earlier stories already cover — the Edge error contract, the
dashboard's delivery, the acknowledgement conflicts — it asserts the thing none
of them can see on its own: that a bootstrapped growbox, driven only through the
public endpoints an external gateway uses, produces `OFF → ON → OFF` in
persisted state.

The cloud runs no simulator. Every temperature in the cycle arrives over the
Cloud ↔ Edge v1 telemetry boundary, and every fan state comes back the same way,
which is exactly how the environment behind the gateway is free to be a real
growbox or Simulation Lab. Observation time is stated by the producer, so there
is no ``sleep`` here and nothing waits on wall-clock time.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from ai_greenhouse.core.config import Settings
from ai_greenhouse.gateways.service import GatewayConfigurationService
from ai_greenhouse.seed.__main__ import run_demo_init_command
from ai_greenhouse.seed.demo_init import DEMO_LOWER_THRESHOLD, DEMO_UPPER_THRESHOLD
from tests.integration.factories import count_rows

API_URL: str = "/api/v1"
EDGE_TELEMETRY_URL: str = f"{API_URL}/edge/telemetry"
NOW: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

DEMO_POINT_CODES: tuple[str, ...] = (
    "air_temperature",
    "air_humidity",
    "fan_power",
    "fan_running",
)
"""Every logical point the demonstration gateway is authorized for."""


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


def _message(point: dict[str, Any], value: Any, observed_at: datetime, kind: str) -> dict[str, Any]:
    """Build one exact v1 telemetry message."""
    return {
        "message_id": str(uuid4()),
        "point_id": point["id"],
        "data_type": point["data_type"],
        "value": value,
        "observed_at": observed_at.isoformat(),
        "quality": "good",
        "source": {"kind": kind, "id": f"growbox.{point['code']}"},
    }


async def _submit(
    http_client: httpx.AsyncClient,
    gateway_id: UUID,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Submit one accepted telemetry envelope, as the gateway does."""
    response = await http_client.post(
        EDGE_TELEMETRY_URL,
        json={
            "contract_version": "1.0",
            "gateway_id": str(gateway_id),
            "messages": messages,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def _apply_pending_command(
    http_client: httpx.AsyncClient,
    gateway_id: UUID,
    points: dict[str, dict[str, Any]],
    *,
    at: datetime,
) -> dict[str, Any]:
    """Poll one pending command, acknowledge it and report the resulting state.

    This is the whole of the gateway's half of the cycle: the cloud never
    reaches into the growbox, and the fan state it later decides on is a
    measurement the gateway sent back like any other.

    Args:
        http_client: The client under test.
        gateway_id: The gateway the command is scoped to.
        points: The growbox's points, keyed by code.
        at: The instant the gateway reports for the acknowledgement and both
            resulting measurements.

    Returns:
        The single command that was pending.
    """
    poll = await http_client.get(f"{API_URL}/edge/gateways/{gateway_id}/commands")
    assert poll.status_code == 200, poll.text
    commands = poll.json()["commands"]
    assert len(commands) == 1, commands
    command = cast(dict[str, Any], commands[0])

    acknowledgement = await http_client.put(
        f"{API_URL}/edge/gateways/{gateway_id}/commands/{command['command_id']}/acknowledgement",
        json={
            "contract_version": "1.0",
            "gateway_id": str(gateway_id),
            "command_id": command["command_id"],
            "outcome": "applied",
            "acknowledged_at": at.isoformat(),
        },
    )
    assert acknowledgement.status_code == 200, acknowledgement.text

    await _submit(
        http_client,
        gateway_id,
        [
            _message(points["fan_power"], command["desired_value"], at, "controller"),
            _message(points["fan_running"], command["desired_value"], at, "actuator"),
        ],
    )
    return command


async def test_the_zero_one_demo_bootstraps_and_closes_the_control_cycle(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    session: AsyncSession,
    database_settings: Settings,
) -> None:
    """Bootstrap twice, then close `OFF → ON → OFF` over the public Edge boundary."""
    session_factory = cast(async_sessionmaker[AsyncSession], app.state.session_factory)
    assert await run_demo_init_command(database_settings, session_factory=session_factory) == 0

    growbox = await _discover(http_client)
    assert set(growbox["points"]) >= set(DEMO_POINT_CODES)
    assert len(growbox["control_loops"]) == 1
    loop = growbox["control_loops"][0]
    assert loop["policy_type"] == "hysteresis-v1"
    assert loop["lower_threshold"] == float(DEMO_LOWER_THRESHOLD)
    assert loop["upper_threshold"] == float(DEMO_UPPER_THRESHOLD)
    # A ready demo is a configured growbox and nothing more: the environment
    # behind the gateway is what makes anything happen.
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

    points = growbox["points"]
    temperature_id = points["air_temperature"]["id"]
    fan_power_id = points["fan_power"]["id"]
    fan_running_id = points["fan_running"]["id"]
    gateway = await GatewayConfigurationService(session).create(
        site_id=UUID(growbox["facility"]["site_id"]),
        point_ids=[UUID(points[code]["id"]) for code in DEMO_POINT_CODES],
    )
    await session.commit()

    # Inside the band: recorded, and nothing decided.
    await _submit(
        http_client,
        gateway.id,
        [_message(points["air_temperature"], 25.0, NOW, "sensor")],
    )
    assert await _temperature(http_client, temperature_id) == 25.0
    assert await _commands(http_client, loop["id"]) == []

    # Above the band: one ON command, delivered to the gateway and applied there.
    await _submit(
        http_client,
        gateway.id,
        [_message(points["air_temperature"], 27.5, NOW + timedelta(minutes=1), "sensor")],
    )
    switched_on = await _commands(http_client, loop["id"])
    assert len(switched_on) == 1
    assert switched_on[0]["desired_value"] is True
    assert switched_on[0]["target_point_id"] == fan_power_id
    assert await _temperature(http_client, temperature_id) > float(DEMO_UPPER_THRESHOLD)

    on_command = await _apply_pending_command(
        http_client,
        gateway.id,
        points,
        at=NOW + timedelta(minutes=2),
    )
    assert on_command["desired_value"] is True
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["value"] is True
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["value"] is True

    # Below the band with the fan running: the cycle closes.
    await _submit(
        http_client,
        gateway.id,
        [_message(points["air_temperature"], 23.0, NOW + timedelta(minutes=3), "sensor")],
    )
    switched_off = await _commands(http_client, loop["id"])
    temperature_at_off = await _temperature(http_client, temperature_id)

    assert len(switched_off) == 2
    assert switched_off[0]["desired_value"] is False
    assert temperature_at_off < float(DEMO_LOWER_THRESHOLD)

    off_command = await _apply_pending_command(
        http_client,
        gateway.id,
        points,
        at=NOW + timedelta(minutes=4),
    )
    assert off_command["desired_value"] is False
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["value"] is False
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["value"] is False
    # Two commands and two revisions each: the temperature inside the band
    # changed nothing, and nothing recorded that it was evaluated.
    assert (await _get(http_client, f"{API_URL}/points/{fan_power_id}/state"))["revision"] == 2
    assert (await _get(http_client, f"{API_URL}/points/{fan_running_id}/state"))["revision"] == 2
    assert (await http_client.get(f"{API_URL}/edge/gateways/{gateway.id}/commands")).json()[
        "commands"
    ] == []

    # What the browser reads after a reload: the history behind the chart and the
    # commands behind recent activity.
    history = (await _get(http_client, f"{API_URL}/points/{temperature_id}/telemetry?limit=60"))[
        "items"
    ]
    activity_commands = await _commands(http_client, loop["id"])

    observed = [sample["observed_at"] for sample in history]
    assert observed == sorted(observed, reverse=True)
    assert [command["desired_value"] for command in activity_commands] == [False, True]
    assert history[0]["value"] == temperature_at_off
    # Three temperatures and two fan reports of two points each.
    assert await count_rows(connection, "telemetry_samples") == 7


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
