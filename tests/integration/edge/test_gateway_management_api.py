"""PostgreSQL-backed coverage of the administrative gateway management plane."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    count_rows,
    create_automation_growbox,
    create_control_loop,
    create_growbox,
    create_point,
)

GATEWAYS_URL = "/api/v1/gateways"
EDGE_TELEMETRY_URL = "/api/v1/edge/telemetry"
OBSERVED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


async def provision_gateway(
    http_client: httpx.AsyncClient,
    site_id: str,
    *,
    code: str = "north-gateway",
) -> httpx.Response:
    """Call the public create-or-resolve operation."""
    return await http_client.post(
        GATEWAYS_URL,
        json={"code": code, "site_id": site_id},
    )


async def test_gateway_is_created_resolved_and_read_in_normalized_forms(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """Equivalent provisioning converges on one UUID and one database row."""
    growbox = await create_growbox(http_client)

    created = await provision_gateway(http_client, growbox.site["id"])
    replay = await provision_gateway(http_client, growbox.site["id"])
    gateway = created.json()["gateway"]
    gateway_id = gateway["id"]

    by_id = await http_client.get(f"{GATEWAYS_URL}/{gateway_id}")
    by_code = await http_client.get(f"{GATEWAYS_URL}/by-code/north-gateway")
    configuration = await http_client.get(f"{GATEWAYS_URL}/{gateway_id}/configuration")

    assert created.status_code == 201, created.text
    assert created.json()["outcome"] == "created"
    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == "existing"
    assert replay.json()["gateway"]["id"] == gateway_id
    assert by_id.status_code == by_code.status_code == configuration.status_code == 200
    assert by_id.json() == by_code.json() == gateway
    assert configuration.json() == {
        "gateway_id": gateway_id,
        "code": "north-gateway",
        "site_id": growbox.site["id"],
    }
    assert created.json()["configuration"] == configuration.json()
    assert await count_rows(connection, "gateways") == 1


async def test_incompatible_gateway_code_reports_field_level_conflict(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """A stable code cannot move to another site or create a second resource."""
    first = await create_growbox(http_client)
    second = await create_growbox(http_client, name="Second", code="second")
    created = await provision_gateway(http_client, first.site["id"])

    conflict = await provision_gateway(http_client, second.site["id"])

    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": {
            "code": "gateway_configuration_conflict",
            "message": "Gateway code already exists with incompatible configuration",
            "details": {
                "gateway_id": created.json()["gateway"]["id"],
                "code": "north-gateway",
                "conflicts": [
                    {
                        "field": "site_id",
                        "expected": second.site["id"],
                        "actual": first.site["id"],
                    }
                ],
            },
        }
    }
    assert await count_rows(connection, "gateways") == 1


async def test_gateway_and_stable_code_not_found_errors_are_explicit(
    http_client: httpx.AsyncClient,
) -> None:
    """Lookup, referenced-resource and validation errors keep stable envelopes."""
    gateway_id = uuid4()
    site_id = uuid4()

    by_id = await http_client.get(f"{GATEWAYS_URL}/{gateway_id}")
    by_code = await http_client.get(f"{GATEWAYS_URL}/by-code/missing-gateway")
    missing_site = await provision_gateway(http_client, str(site_id))
    invalid = await http_client.post(
        GATEWAYS_URL,
        json={"code": "Not A Code", "site_id": "not-a-uuid"},
    )

    assert by_id.status_code == by_code.status_code == 404
    assert by_id.json()["error"] == {
        "code": "gateway_not_found",
        "message": "Gateway not found",
        "details": {"gateway_id": str(gateway_id)},
    }
    assert by_code.json()["error"] == {
        "code": "gateway_not_found",
        "message": "Gateway not found",
        "details": {"code": "missing-gateway"},
    }
    assert missing_site.status_code == 404
    assert missing_site.json()["error"] == {
        "code": "site_not_found",
        "message": "Site not found",
        "details": {"site_id": str(site_id)},
    }
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.json()["error"]["details"]["errors"]


async def test_point_authorization_is_additive_deduplicated_and_idempotent(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """Duplicate input and an equivalent retry leave one row per point."""
    growbox = await create_growbox(http_client)
    first_point = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
    )
    second_point = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="air-humidity",
        name="Air humidity",
        metric_type="air_humidity",
        unit="%",
    )
    gateway_id = (await provision_gateway(http_client, growbox.site["id"])).json()["gateway"]["id"]
    url = f"{GATEWAYS_URL}/{gateway_id}/points"
    point_ids = [first_point["id"], first_point["id"], second_point["id"]]

    first = await http_client.post(url, json={"point_ids": point_ids})
    replay = await http_client.post(url, json={"point_ids": point_ids})
    current = await http_client.get(url)

    assert first.status_code == replay.status_code == current.status_code == 200
    assert first.json() == {
        "gateway_id": gateway_id,
        "points": [
            {"point_id": first_point["id"], "outcome": "authorized"},
            {"point_id": second_point["id"], "outcome": "authorized"},
        ],
    }
    assert replay.json()["points"] == [
        {"point_id": first_point["id"], "outcome": "already_authorized"},
        {"point_id": second_point["id"], "outcome": "already_authorized"},
    ]
    assert current.json() == {
        "gateway_id": gateway_id,
        "point_ids": sorted([first_point["id"], second_point["id"]]),
    }
    assert await count_rows(connection, "gateway_points") == 2


async def test_missing_inactive_and_cross_site_points_are_rejected(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """Authorization validates point existence, lifecycle and owning topology."""
    growbox = await create_growbox(http_client)
    other = await create_growbox(http_client, name="Other", code="other")
    inactive = await create_point(http_client, growbox.site["id"])
    cross_site = await create_point(http_client, other.site["id"])
    gateway_id = (await provision_gateway(http_client, growbox.site["id"])).json()["gateway"]["id"]
    url = f"{GATEWAYS_URL}/{gateway_id}/points"
    archived = await http_client.patch(
        f"/api/v1/points/{inactive['id']}",
        json={"status": "archived"},
    )
    assert archived.status_code == 200, archived.text

    missing_id = uuid4()
    missing = await http_client.post(url, json={"point_ids": [str(missing_id)]})
    invalid = await http_client.post(url, json={"point_ids": [inactive["id"]]})
    wrong_site = await http_client.post(url, json={"point_ids": [cross_site["id"]]})

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "point_not_found"
    assert missing.json()["error"]["details"] == {"point_id": str(missing_id)}
    assert invalid.status_code == 409
    assert invalid.json()["error"]["details"]["reason"] == "point_not_active"
    assert wrong_site.status_code == 409
    assert wrong_site.json()["error"]["details"]["reason"] == "point_not_in_site"
    assert await count_rows(connection, "gateway_points") == 0


async def test_point_cannot_be_authorized_to_two_gateways(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The v1 one-owner invariant produces a stable conflict without a duplicate."""
    growbox = await create_growbox(http_client)
    point = await create_point(http_client, growbox.site["id"])
    first_id = (
        await provision_gateway(http_client, growbox.site["id"], code="first-gateway")
    ).json()["gateway"]["id"]
    second_id = (
        await provision_gateway(http_client, growbox.site["id"], code="second-gateway")
    ).json()["gateway"]["id"]
    first = await http_client.post(
        f"{GATEWAYS_URL}/{first_id}/points",
        json={"point_ids": [point["id"]]},
    )

    conflict = await http_client.post(
        f"{GATEWAYS_URL}/{second_id}/points",
        json={"point_ids": [point["id"]]},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {
        "code": "gateway_point_conflict",
        "message": "Point cannot be authorized to the gateway",
        "details": {
            "point_id": point["id"],
            "reason": "point_already_authorized",
            "gateway_id": second_id,
            "conflicting_gateway_id": first_id,
        },
    }
    assert await count_rows(connection, "gateway_points") == 1


def telemetry_message(point: dict[str, Any], value: Any) -> dict[str, Any]:
    """Build the smallest valid Cloud ↔ Edge v1 telemetry message."""
    return {
        "message_id": str(uuid4()),
        "point_id": point["id"],
        "data_type": point["data_type"],
        "value": value,
        "observed_at": OBSERVED_AT.isoformat(),
        "quality": "good",
        "source": {"kind": "sensor", "id": f"test.{point['code']}"},
    }


async def test_http_configured_gateway_immediately_uses_all_edge_operations(
    http_client: httpx.AsyncClient,
) -> None:
    """Gateway-specific setup needs no cloud import, direct database call or seed."""
    growbox = await create_automation_growbox(http_client)
    await create_control_loop(http_client, growbox)
    provisioned = await provision_gateway(http_client, growbox.site["id"])
    gateway_id = provisioned.json()["gateway"]["id"]
    authorization = await http_client.post(
        f"{GATEWAYS_URL}/{gateway_id}/points",
        json={"point_ids": [point["id"] for point in growbox.points.values()]},
    )
    assert authorization.status_code == 200, authorization.text

    telemetry = await http_client.post(
        EDGE_TELEMETRY_URL,
        json={
            "contract_version": "1.0",
            "gateway_id": gateway_id,
            "messages": [telemetry_message(growbox.points["air_temperature"], 27.0)],
        },
    )
    poll = await http_client.get(f"/api/v1/edge/gateways/{gateway_id}/commands")
    command = poll.json()["commands"][0]
    acknowledgement = await http_client.put(
        (f"/api/v1/edge/gateways/{gateway_id}/commands/{command['command_id']}/acknowledgement"),
        json={
            "contract_version": "1.0",
            "gateway_id": gateway_id,
            "command_id": command["command_id"],
            "outcome": "applied",
            "acknowledged_at": "2026-07-30T12:02:00Z",
        },
    )

    assert telemetry.status_code == 200, telemetry.text
    assert telemetry.json()["results"][0]["outcome"] == "recorded_current"
    assert poll.status_code == 200, poll.text
    assert command["point_id"] == growbox.points["fan_power"]["id"]
    assert acknowledgement.status_code == 200, acknowledgement.text
    assert acknowledgement.json()["outcome"] == "applied"


async def test_openapi_documents_gateway_management_operations_and_errors(
    http_client: httpx.AsyncClient,
) -> None:
    """The generated contract exposes every management representation."""
    document = (await http_client.get("/openapi.json")).json()
    paths = document["paths"]

    assert {
        path: set(operations) & {"get", "post", "put", "patch", "delete"}
        for path, operations in paths.items()
        if path.startswith(GATEWAYS_URL)
    } == {
        "/api/v1/gateways": {"post"},
        "/api/v1/gateways/by-code/{code}": {"get"},
        "/api/v1/gateways/{gateway_id}": {"get"},
        "/api/v1/gateways/{gateway_id}/configuration": {"get"},
        "/api/v1/gateways/{gateway_id}/points": {"get", "post"},
    }
    schemas = document["components"]["schemas"]
    assert {
        "GatewayCreate",
        "GatewayRead",
        "GatewayConfigurationRead",
        "GatewayProvisioningRead",
        "GatewayPointAuthorizationRequest",
        "GatewayPointAuthorizationResult",
        "GatewayAuthorizedPointsRead",
        "GatewayErrorResponse",
    } <= set(schemas)
    for path in paths:
        if not path.startswith(GATEWAYS_URL):
            continue
        for operation in paths[path].values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            assert {"404", "409", "422"} <= set(operation["responses"])
            assert (
                operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
                == "#/components/schemas/GatewayErrorResponse"
            )
