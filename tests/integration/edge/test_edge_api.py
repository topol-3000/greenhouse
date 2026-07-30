"""Focused full-transaction coverage of the public Cloud ↔ Edge adapter."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.gateways.service import GatewayConfigurationService
from tests.integration.factories import (
    AutomationGrowbox,
    count_rows,
    create_automation_growbox,
    create_control_loop,
    create_growbox,
    create_point,
)

EDGE_TELEMETRY_URL = "/api/v1/edge/telemetry"
OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
CONTRACT_ROOT = Path(__file__).parents[3] / "contracts" / "cloud-edge" / "v1"


def validate_contract(schema_name: str, value: dict[str, Any]) -> None:
    """Validate an actual HTTP representation against the immutable schema."""
    schema = json.loads((CONTRACT_ROOT / "schemas" / schema_name).read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def telemetry_message(
    point: dict[str, Any],
    *,
    value: Any,
    observed_at: datetime = OBSERVED_AT,
    message_id: str | None = None,
    source_kind: str = "sensor",
) -> dict[str, Any]:
    """Build one exact v1 telemetry message."""
    return {
        "message_id": message_id or str(uuid4()),
        "point_id": point["id"],
        "data_type": point["data_type"],
        "value": value,
        "observed_at": observed_at.isoformat(),
        "quality": "good",
        "source": {"kind": source_kind, "id": f"test.{point['code']}"},
    }


def envelope(gateway_id: UUID, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one exact v1 envelope."""
    return {
        "contract_version": "1.0",
        "gateway_id": str(gateway_id),
        "messages": messages,
    }


async def create_gateway(
    session: AsyncSession,
    growbox: AutomationGrowbox,
    *,
    point_codes: tuple[str, ...] = ("air_temperature", "fan_power", "fan_running"),
) -> UUID:
    """Persist gateway test configuration through its management service."""
    gateway = await GatewayConfigurationService(session).create(
        site_id=UUID(growbox.site["id"]),
        point_ids=[UUID(growbox.points[code]["id"]) for code in point_codes],
    )
    await session.commit()
    return gateway.id


@pytest.fixture
async def edge_growbox(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> tuple[AutomationGrowbox, UUID]:
    """Build a configured loop and gateway owning all three logical points."""
    growbox = await create_automation_growbox(http_client)
    await create_control_loop(http_client, growbox)
    return growbox, await create_gateway(session, growbox)


async def state(connection: AsyncConnection, point_id: str) -> dict[str, Any]:
    """Read a point projection."""
    result = await connection.execute(
        text("SELECT * FROM point_current_states WHERE point_id = :point_id"),
        {"point_id": UUID(point_id)},
    )
    return dict(result.mappings().one())


async def test_external_telemetry_reuses_history_state_automation_and_idempotency(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """One public fact traverses the existing boundary and an exact replay does nothing."""
    growbox, gateway_id = edge_growbox
    message = telemetry_message(growbox.points["air_temperature"], value=27.0)
    first = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(gateway_id, [message]),
    )
    before = await state(connection, growbox.points["air_temperature"]["id"])
    replay = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(gateway_id, [message]),
    )
    after = await state(connection, growbox.points["air_temperature"]["id"])

    assert first.status_code == 200, first.text
    validate_contract("telemetry-ingestion-result.schema.json", first.json())
    assert first.json()["results"][0]["outcome"] == "recorded_current"
    assert replay.status_code == 200, replay.text
    assert replay.json()["results"][0]["outcome"] == "duplicate"
    assert replay.json()["results"][0]["received_at"] == first.json()["results"][0]["received_at"]
    assert before["value"] == 27.0
    assert after["revision"] == before["revision"]
    assert await count_rows(connection, "telemetry_samples") == 1
    assert await count_rows(connection, "commands") == 1

    poll = await http_client.get(f"/api/v1/edge/gateways/{gateway_id}/commands")
    command = poll.json()["commands"][0]
    assert poll.status_code == 200, poll.text
    validate_contract("command-list.schema.json", poll.json())
    assert command["point_id"] == growbox.points["fan_power"]["id"]
    assert command["reported_point_id"] == growbox.points["fan_running"]["id"]
    assert command["desired_value"] is True
    assert command["state"] == "pending"


async def test_conflicting_message_and_out_of_order_fact_have_exact_behavior(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """A changed replay conflicts; a distinct older fact stays history-only."""
    growbox, gateway_id = edge_growbox
    point = growbox.points["air_temperature"]
    message = telemetry_message(point, value=25.0)
    accepted = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(gateway_id, [message]),
    )
    conflicting = message | {"value": 26.0}
    conflict = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(gateway_id, [conflicting]),
    )
    late = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(
            gateway_id,
            [
                telemetry_message(
                    point,
                    value=27.0,
                    observed_at=OBSERVED_AT - timedelta(minutes=1),
                )
            ],
        ),
    )

    assert accepted.status_code == 200, accepted.text
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "telemetry_message_conflict"
    assert late.status_code == 200, late.text
    assert late.json()["results"][0]["outcome"] == "out_of_order"
    assert (await state(connection, point["id"]))["value"] == 25.0
    assert await count_rows(connection, "telemetry_samples") == 2
    assert await count_rows(connection, "commands") == 0


async def test_one_forbidden_point_rejects_the_complete_envelope(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """All relationships are checked before the first message is persisted."""
    growbox, gateway_id = edge_growbox
    other = await create_growbox(
        http_client,
        name="Other",
        code="other",
    )
    forbidden = await create_point(
        http_client,
        other.site["id"],
        code="other-temperature",
    )
    response = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(
            gateway_id,
            [
                telemetry_message(growbox.points["air_temperature"], value=25.0),
                telemetry_message(forbidden, value=20.0),
            ],
        ),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "gateway_point_forbidden"
    assert await count_rows(connection, "telemetry_samples") == 0
    assert await count_rows(connection, "edge_telemetry_messages") == 0


async def test_missing_and_inactive_gateways_use_the_published_errors(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """Gateway identity and lifecycle map to distinct exact errors."""
    growbox, gateway_id = edge_growbox
    payload = envelope(
        gateway_id,
        [telemetry_message(growbox.points["air_temperature"], value=25.0)],
    )
    missing = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=payload | {"gateway_id": str(uuid4())},
    )
    await session.execute(
        text("UPDATE gateways SET status = 'archived' WHERE id = :gateway_id"),
        {"gateway_id": gateway_id},
    )
    await session.commit()
    inactive = await http_client.post(EDGE_TELEMETRY_URL, json=payload)

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "gateway_not_found"
    assert inactive.status_code == 409
    assert inactive.json()["error"]["code"] == "gateway_inactive"


async def test_command_scope_acknowledgement_and_reported_state(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """Polling cannot enumerate across gateways and acknowledgements never write state."""
    growbox, gateway_id = edge_growbox
    trigger = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(
            gateway_id,
            [telemetry_message(growbox.points["air_temperature"], value=27.0)],
        ),
    )
    assert trigger.status_code == 200, trigger.text
    poll = await http_client.get(f"/api/v1/edge/gateways/{gateway_id}/commands")
    command_id = poll.json()["commands"][0]["command_id"]

    other = await create_automation_growbox(
        http_client,
        name="Second",
        code="second",
    )
    other_gateway = await create_gateway(session, other)
    other_poll = await http_client.get(f"/api/v1/edge/gateways/{other_gateway}/commands")
    cross_ack = await http_client.put(
        f"/api/v1/edge/gateways/{other_gateway}/commands/{command_id}/acknowledgement",
        json={
            "contract_version": "1.0",
            "gateway_id": str(other_gateway),
            "command_id": command_id,
            "outcome": "applied",
            "acknowledged_at": "2026-07-30T12:02:00Z",
        },
    )
    before = await state(connection, growbox.points["fan_power"]["id"])
    acknowledgement = {
        "contract_version": "1.0",
        "gateway_id": str(gateway_id),
        "command_id": command_id,
        "outcome": "applied",
        "acknowledged_at": "2026-07-30T12:02:00Z",
    }
    ack_url = f"/api/v1/edge/gateways/{gateway_id}/commands/{command_id}/acknowledgement"
    first = await http_client.put(ack_url, json=acknowledgement)
    duplicate = await http_client.put(ack_url, json=acknowledgement)
    conflict = await http_client.put(
        ack_url,
        json=acknowledgement | {"acknowledged_at": "2026-07-30T12:03:00Z"},
    )
    after = await state(connection, growbox.points["fan_power"]["id"])

    assert other_poll.json()["commands"] == []
    assert cross_ack.status_code == 404
    assert cross_ack.json()["error"]["code"] == "command_not_found"
    assert first.status_code == duplicate.status_code == 200
    validate_contract("command-acknowledgement.schema.json", first.json())
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "command_acknowledgement_conflict"
    assert after["revision"] == before["revision"] == 0
    assert (await http_client.get(f"/api/v1/edge/gateways/{gateway_id}/commands")).json()[
        "commands"
    ] == []

    reported = await http_client.post(
        EDGE_TELEMETRY_URL,
        json=envelope(
            gateway_id,
            [
                telemetry_message(
                    growbox.points["fan_power"],
                    value=True,
                    observed_at=OBSERVED_AT + timedelta(minutes=3),
                    source_kind="controller",
                ),
                telemetry_message(
                    growbox.points["fan_running"],
                    value=True,
                    observed_at=OBSERVED_AT + timedelta(minutes=3),
                    source_kind="actuator",
                ),
            ],
        ),
    )
    assert reported.status_code == 200, reported.text
    assert (await state(connection, growbox.points["fan_power"]["id"]))["value"] is True
    assert (await state(connection, growbox.points["fan_running"]["id"]))["value"] is True


async def test_acknowledgement_path_body_mismatch_is_validation_error(
    edge_growbox: tuple[AutomationGrowbox, UUID],
    http_client: httpx.AsyncClient,
) -> None:
    """The acknowledgement resource identity must agree in path and body."""
    _, gateway_id = edge_growbox
    command_id = uuid4()
    response = await http_client.put(
        f"/api/v1/edge/gateways/{gateway_id}/commands/{command_id}/acknowledgement",
        json={
            "contract_version": "1.0",
            "gateway_id": str(gateway_id),
            "command_id": str(uuid4()),
            "outcome": "rejected",
            "acknowledged_at": "2026-07-30T12:02:00Z",
            "reason": {"code": "unavailable", "message": "Unavailable."},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_openapi_exposes_only_the_published_edge_operations(
    http_client: httpx.AsyncClient,
) -> None:
    """The generated OpenAPI paths and methods match the immutable manifest."""
    document = (await http_client.get("/openapi.json")).json()
    edge_operations = {
        path: set(methods) & {"get", "post", "put", "patch", "delete"}
        for path, methods in document["paths"].items()
        if path.startswith("/api/v1/edge/")
    }

    assert edge_operations == {
        "/api/v1/edge/telemetry": {"post"},
        "/api/v1/edge/gateways/{gateway_id}/commands": {"get"},
        ("/api/v1/edge/gateways/{gateway_id}/commands/{command_id}/acknowledgement"): {"put"},
    }
