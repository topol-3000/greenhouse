"""One externally provisioned growbox, driven to a closed control cycle.

This is the one test that owns the full cycle. It does not re-assert the focused
guarantees the earlier stories already cover — the Edge error contract, the
dashboard's delivery, the acknowledgement conflicts — it asserts the thing none
of them can see on its own: that a growbox an external client provisioned
through the public HTTP APIs, and drives only through them, produces
`OFF → ON → OFF` in persisted state.

Nothing in the cloud creates any of it. The topology, the points, the
assignments and the loop are all requests a client makes; every temperature
arrives over the Cloud ↔ Edge v1 telemetry boundary and every fan state comes
back the same way, which is exactly how the environment behind the gateway is
free to be a real growbox or Simulation Lab. Observation time is stated by the
producer, so there is no ``sleep`` here and nothing waits on wall-clock time.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.gateways.service import GatewayConfigurationService
from tests.integration.factories import (
    LOWER_THRESHOLD,
    UPPER_THRESHOLD,
    count_rows,
    create_climate_growbox,
    create_control_loop,
)

API_URL: str = "/api/v1"
EDGE_TELEMETRY_URL: str = f"{API_URL}/edge/telemetry"
NOW: datetime = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)

GROWBOX_POINT_CODES: tuple[str, ...] = (
    "air_temperature",
    "air_humidity",
    "fan_power",
    "fan_running",
)
"""Every logical point the gateway is authorized for."""


async def _get(http_client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Read one successful JSON resource."""
    response = await http_client.get(path)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


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


async def test_an_externally_provisioned_growbox_closes_the_control_cycle(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    session: AsyncSession,
) -> None:
    """Provision over HTTP, then close `OFF → ON → OFF` over the public Edge boundary."""
    growbox = await create_climate_growbox(http_client)
    loop = await create_control_loop(http_client, growbox)
    points = growbox.points

    assert set(points) == set(GROWBOX_POINT_CODES)
    assert loop["policy_type"] == "hysteresis-v1"
    # A provisioned growbox is a configuration and nothing more: the environment
    # behind the gateway is what makes anything happen.
    assert await count_rows(connection, "telemetry_samples") == 0
    assert await count_rows(connection, "commands") == 0

    temperature_id = points["air_temperature"]["id"]
    fan_power_id = points["fan_power"]["id"]
    fan_running_id = points["fan_running"]["id"]
    gateway = await GatewayConfigurationService(session).create(
        site_id=UUID(growbox.site["id"]),
        point_ids=[UUID(points[code]["id"]) for code in GROWBOX_POINT_CODES],
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
    assert await _temperature(http_client, temperature_id) > UPPER_THRESHOLD

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
    assert temperature_at_off < LOWER_THRESHOLD

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
    # A command an external gateway applied carries no ``executed_at``: that
    # field belongs to an in-process actuator. The acknowledgement is what dates
    # it, which is what the dashboard's activity list orders by.
    assert [command["state"] for command in activity_commands] == ["applied", "applied"]
    assert all(command["executed_at"] is None for command in activity_commands)
    assert all(command["acknowledged_at"] is not None for command in activity_commands)
    assert history[0]["value"] == temperature_at_off
    # Three temperatures and two fan reports of two points each.
    assert await count_rows(connection, "telemetry_samples") == 7
