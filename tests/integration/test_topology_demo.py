"""End-to-end proof of the complete topology HTTP demonstration."""

from typing import Any

import httpx

SITES_URL: str = "/api/v1/sites"
FACILITIES_URL: str = "/api/v1/facilities"
CONTROL_ZONES_URL: str = "/api/v1/control-zones"
POINTS_URL: str = "/api/v1/points"

DEMO_POINTS: tuple[dict[str, Any], ...] = (
    {
        "code": "air_temperature",
        "name": "Air Temperature",
        "point_kind": "measurement",
        "metric_type": "air_temperature",
        "data_type": "float",
        "unit": "°C",
        "min_value": -20,
        "max_value": 60,
        "role": "primary_measurement",
    },
    {
        "code": "air_humidity",
        "name": "Air Humidity",
        "point_kind": "measurement",
        "metric_type": "air_humidity",
        "data_type": "float",
        "unit": "%",
        "min_value": 0,
        "max_value": 100,
        "role": "secondary_measurement",
    },
    {
        "code": "fan_power",
        "name": "Fan Power",
        "point_kind": "control",
        "metric_type": "fan_power",
        "data_type": "boolean",
        "unit": None,
        "role": "control_output",
    },
    {
        "code": "fan_running",
        "name": "Fan Running",
        "point_kind": "status",
        "metric_type": "fan_running",
        "data_type": "boolean",
        "unit": None,
        "role": "status_feedback",
    },
)
"""Points and assignments created by the copy-pasteable topology scenario."""


async def _post(
    http_client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create one resource and return its decoded representation.

    Args:
        http_client: Client connected to the application under test.
        url: Resource collection URL.
        payload: JSON request body.

    Returns:
        The decoded HTTP 201 response.
    """
    response: httpx.Response = await http_client.post(url, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_topology_demo_scenario(http_client: httpx.AsyncClient) -> None:
    """Build and read the complete demo in the documented HTTP order."""
    site: dict[str, Any] = await _post(
        http_client,
        SITES_URL,
        {"name": "Home", "code": "home", "timezone": "UTC"},
    )
    facility: dict[str, Any] = await _post(
        http_client,
        FACILITIES_URL,
        {
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )
    control_zone: dict[str, Any] = await _post(
        http_client,
        CONTROL_ZONES_URL,
        {
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    points: list[dict[str, Any]] = []
    for definition in DEMO_POINTS:
        point_payload: dict[str, Any] = {
            key: value for key, value in definition.items() if key != "role"
        }
        point_payload |= {"site_id": site["id"], "facility_id": facility["id"]}
        point: dict[str, Any] = await _post(http_client, POINTS_URL, point_payload)
        points.append(point)
        await _post(
            http_client,
            f"{CONTROL_ZONES_URL}/{control_zone['id']}/points",
            {"point_id": point["id"], "role": definition["role"]},
        )

    configuration_response: httpx.Response = await http_client.get(
        f"{FACILITIES_URL}/{facility['id']}/configuration"
    )
    assert configuration_response.status_code == 200, configuration_response.text
    configuration: dict[str, Any] = configuration_response.json()

    assert configuration["site"]["id"] == site["id"]
    assert configuration["site"]["code"] == "home"
    assert configuration["facility"]["id"] == facility["id"]
    assert configuration["facility"]["code"] == "basil-growbox"
    assert len(configuration["control_zones"]) == 1
    assert configuration["control_zones"][0]["id"] == control_zone["id"]
    assert len(configuration["control_zones"][0]["points"]) == 4
    assert [link["role"] for link in configuration["control_zones"][0]["points"]] == [
        definition["role"] for definition in DEMO_POINTS
    ]
    assert len(configuration["points"]) == 4
    assert {point["code"] for point in configuration["points"]} == {
        definition["code"] for definition in DEMO_POINTS
    }
    assert all(
        point["state"]["quality"] == "no_data" and point["state"]["value"] is None
        for point in configuration["points"]
    )

    for point in points:
        state_response: httpx.Response = await http_client.get(f"{POINTS_URL}/{point['id']}/state")
        assert state_response.status_code == 200, state_response.text
        state: dict[str, Any] = state_response.json()
        assert state["quality"] == "no_data"
        assert state["value"] is None
