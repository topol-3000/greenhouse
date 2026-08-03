"""A manual command leaves the cloud the same way an automatic one does.

The claim this unit rests on is that adding a customer-facing way to *create* a
command adds no second delivery path. So this test drives one end to end through
the boundaries that already existed: the gateway polls it with the operation it
already polls automatic commands with, acknowledges it with the operation it
already acknowledges them with, and reports the resulting state as ordinary
telemetry. The browser calls none of that, and the cloud reaches into nothing.

The three states a customer can observe are asserted in the order they happen —
pending, acknowledged-but-still-pending, terminal — because the middle one is
the one a client is most likely to mistake for success.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    COMMANDS_URL,
    POINTS_URL,
    CommandableGrowbox,
    count_rows,
    create_commandable_growbox,
    submit_manual_command,
)

API_URL: str = "/api/v1"
NOW: datetime = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


@pytest.fixture
async def growbox(http_client: httpx.AsyncClient) -> CommandableGrowbox:
    """Build the growbox whose fan a gateway can carry out commands for."""
    return await create_commandable_growbox(http_client)


def edge_commands_url(gateway_id: str) -> str:
    """Return the gateway's command-polling URL."""
    return f"{API_URL}/edge/gateways/{gateway_id}/commands"


async def poll(http_client: httpx.AsyncClient, gateway_id: str) -> list[dict[str, Any]]:
    """Poll one gateway's pending commands, as the gateway does."""
    response = await http_client.get(edge_commands_url(gateway_id))
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json()["commands"])


async def acknowledge(
    http_client: httpx.AsyncClient,
    gateway_id: str,
    command_id: str,
    *,
    outcome: str,
    at: datetime,
    reason: dict[str, str] | None = None,
) -> httpx.Response:
    """Send one terminal acknowledgement over the published v1 contract."""
    body: dict[str, Any] = {
        "contract_version": "1.0",
        "gateway_id": gateway_id,
        "command_id": command_id,
        "outcome": outcome,
        "acknowledged_at": at.isoformat(),
    }
    if reason is not None:
        body["reason"] = reason
    return await http_client.put(
        f"{edge_commands_url(gateway_id)}/{command_id}/acknowledgement",
        json=body,
    )


async def read_command(http_client: httpx.AsyncClient, command_id: str) -> dict[str, Any]:
    """Read one command the way a customer-facing client reads it."""
    response = await http_client.get(f"{COMMANDS_URL}/{command_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def test_a_manual_command_is_polled_acknowledged_and_applied(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """The complete customer-observable lifecycle, over the existing Edge boundary.

    The gateway receives the command with the same shape as an automatic one —
    the v1 envelope carries no source, because the actuator does not care who
    asked — and the customer watches the same command move through the public
    read.
    """
    created = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    assert created.status_code == 201, created.text
    command_id = created.json()["command"]["id"]
    gateway_id = growbox.gateway["id"]

    pending = await poll(http_client, gateway_id)
    assert [entry["command_id"] for entry in pending] == [command_id]
    assert pending[0]["point_id"] == growbox.points["fan_power"]["id"]
    assert pending[0]["reported_point_id"] == growbox.points["fan_running"]["id"]
    assert pending[0]["data_type"] == "boolean"
    assert pending[0]["desired_value"] is True
    assert pending[0]["state"] == "pending"

    applied = await acknowledge(
        http_client,
        gateway_id,
        command_id,
        outcome="applied",
        at=NOW,
    )
    assert applied.status_code == 200, applied.text

    terminal = await read_command(http_client, command_id)
    assert terminal["state"] == "applied"
    assert terminal["acknowledged_at"] is not None
    assert terminal["source"] == "manual"
    assert terminal["rejection_reason"] is None
    # Terminal means gone from the queue: the gateway is never handed it twice.
    assert await poll(http_client, gateway_id) == []


async def test_acknowledgement_alone_does_not_mean_the_fan_moved(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """Receipt is not application, and the reported point is what says so.

    The gateway has acknowledged and reported the fan as still off. The command
    is ``applied`` — that is what the gateway said about carrying it out — and
    the desired value is ``true`` while the status point reads ``false``. A
    client that treated either as the other would be wrong, and this is the case
    that shows why the two are separate fields.
    """
    created = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    command_id = created.json()["command"]["id"]
    fan_running_id = growbox.points["fan_running"]["id"]

    await acknowledge(
        http_client,
        growbox.gateway["id"],
        command_id,
        outcome="applied",
        at=NOW,
    )
    reported = await http_client.post(
        f"{API_URL}/edge/telemetry",
        json={
            "contract_version": "1.0",
            "gateway_id": growbox.gateway["id"],
            "messages": [
                {
                    "message_id": str(uuid4()),
                    "point_id": fan_running_id,
                    "data_type": "boolean",
                    "value": False,
                    "observed_at": (NOW + timedelta(minutes=1)).isoformat(),
                    "quality": "good",
                    "source": {"kind": "actuator", "id": "growbox.fan_running"},
                }
            ],
        },
    )
    assert reported.status_code == 200, reported.text

    command = await read_command(http_client, command_id)
    state = await http_client.get(f"{POINTS_URL}/{fan_running_id}/state")

    assert command["desired_value"] is True
    assert state.json()["value"] is False


async def test_a_rejected_manual_command_carries_a_typed_reason(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """Terminal failure is readable and machine-branchable, not an opaque object."""
    created = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    command_id = created.json()["command"]["id"]

    rejected = await acknowledge(
        http_client,
        growbox.gateway["id"],
        command_id,
        outcome="rejected",
        at=NOW,
        reason={"code": "actuator_unavailable", "message": "The relay did not respond."},
    )
    assert rejected.status_code == 200, rejected.text

    command = await read_command(http_client, command_id)

    assert command["state"] == "rejected"
    assert command["rejection_reason"] == {
        "code": "actuator_unavailable",
        "message": "The relay did not respond.",
    }
    assert await poll(http_client, growbox.gateway["id"]) == []


async def test_a_replayed_request_offers_the_gateway_nothing_new(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
) -> None:
    """The idempotency guarantee, asserted where it actually matters.

    One row is easy to check. What a customer cares about is that a retry does
    not switch the fan twice, and the only way that could happen is a second
    pending command reaching the gateway. It does not exist.
    """
    key = str(uuid4())
    first = await submit_manual_command(http_client, growbox, idempotency_key=key)
    replay = await submit_manual_command(http_client, growbox, idempotency_key=key)

    pending = await poll(http_client, growbox.gateway["id"])

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    assert [entry["command_id"] for entry in pending] == [first.json()["command"]["id"]]
    assert await count_rows(connection, "commands") == 1


async def test_a_replay_after_acknowledgement_returns_the_current_state(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """A replay answers with the command as it is now, not as it was created.

    A client retrying long after the fact learns the terminal outcome from the
    replay itself, and still causes nothing.
    """
    key = str(uuid4())
    created = await submit_manual_command(http_client, growbox, idempotency_key=key)
    command_id = created.json()["command"]["id"]
    await acknowledge(
        http_client,
        growbox.gateway["id"],
        command_id,
        outcome="applied",
        at=NOW,
    )

    replay = await submit_manual_command(http_client, growbox, idempotency_key=key)

    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == "existing"
    assert replay.json()["command"]["state"] == "applied"
    assert replay.json()["command"]["acknowledged_at"] is not None
