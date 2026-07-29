"""End-to-end coverage of the facility configuration endpoint against PostgreSQL.

Two properties are load-bearing here and each has a test that fails loudly when
it is lost:

* the document is *whole* — the demo growbox is asserted field by field, so a
  silently dropped zone, point or state is a failing test rather than a client's
  problem;
* the document is *bounded* — the number of SQL statements is measured on a
  facility four times the size of the demo one and must not have grown. An ORM
  graph walked lazily would pass every other test in this file and fail that one.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    CONTROL_ZONES_URL,
    POINTS_URL,
    archive,
    configuration_url,
    create_assignment,
    create_control_zone,
    create_facility,
    create_point,
    create_site,
)

MAX_CONFIGURATION_SELECTS: int = 6
"""Ceiling on the SELECTs of one configuration request.

The read model needs five: the facility, its site, its zones, the assignments of
those zones and its points with their state. The one statement of margin is there
so an honest refactor does not have to touch this test, and is small enough that
a query per zone or per point cannot hide under it — the sibling assertion that
the count is the *same* for a facility four times the size is what actually rules
out an N+1.
"""

DEMO_POINTS: list[dict[str, Any]] = [
    {
        "code": "air_temperature",
        "name": "Air temperature",
        "point_kind": "measurement",
        "metric_type": "air_temperature",
        "data_type": "float",
        "unit": "°C",
    },
    {
        "code": "air_humidity",
        "name": "Air humidity",
        "point_kind": "measurement",
        "metric_type": "air_humidity",
        "data_type": "float",
        "unit": "%",
    },
    {
        "code": "fan_power",
        "name": "Fan power",
        "point_kind": "control",
        "metric_type": "fan_power",
        "data_type": "boolean",
        "unit": None,
    },
    {
        "code": "fan_running",
        "name": "Fan running",
        "point_kind": "status",
        "metric_type": "fan_running",
        "data_type": "boolean",
        "unit": None,
    },
]
"""The four points of the Milestone 1 demonstration growbox."""

DEMO_ROLES: list[str] = [
    "primary_measurement",
    "secondary_measurement",
    "control_output",
    "status_feedback",
]
"""The role each demonstration point plays in the Main Climate zone."""


async def build_demo_growbox(
    http_client: httpx.AsyncClient,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build the whole Milestone 1 demonstration growbox through the API.

    Args:
        http_client: The client under test.

    Returns:
        The site, the facility, the Main Climate zone and its four points, in
        the order the milestone documents them.
    """
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    control_zone = await create_control_zone(http_client, facility["id"])
    points: list[dict[str, Any]] = [
        await create_point(http_client, site["id"], facility_id=facility["id"], **fields)
        for fields in DEMO_POINTS
    ]
    for point, role in zip(points, DEMO_ROLES, strict=True):
        await create_assignment(http_client, control_zone["id"], point["id"], role)
    return site, facility, control_zone, points


async def build_large_growbox(
    http_client: httpx.AsyncClient,
    *,
    site_code: str,
    facility_code: str,
    zone_count: int,
    points_per_zone: int,
) -> dict[str, Any]:
    """Build a facility of a given size with every point assigned to a zone.

    Args:
        http_client: The client under test.
        site_code: Code of the site to create.
        facility_code: Code of the facility to create.
        zone_count: How many zones the facility gets.
        points_per_zone: How many measurement points each zone gets.

    Returns:
        The decoded response body of the created facility.
    """
    site = await create_site(http_client, name=site_code, code=site_code)
    facility = await create_facility(
        http_client,
        site["id"],
        name=facility_code,
        code=facility_code,
    )
    for zone_index in range(zone_count):
        control_zone = await create_control_zone(
            http_client,
            facility["id"],
            name=f"Zone {zone_index}",
            code=f"zone-{zone_index}",
        )
        for point_index in range(points_per_zone):
            code: str = f"{facility_code}_metric_{zone_index}_{point_index}"
            point = await create_point(
                http_client,
                site["id"],
                facility_id=facility["id"],
                code=code,
                name=code,
                metric_type=f"metric_{point_index}",
            )
            role: str = "primary_measurement" if point_index == 0 else "secondary_measurement"
            await create_assignment(http_client, control_zone["id"], point["id"], role)
    return facility


@contextmanager
def count_selects(connection: AsyncConnection) -> Iterator[list[str]]:
    """Record every ``SELECT`` executed on the test's connection.

    Only ``SELECT`` statements are recorded. The savepoints the test harness
    opens and releases around each request are not part of what the read model
    costs, and counting them would make this measurement depend on the harness.

    Args:
        connection: The test's connection, which the application's sessions are
            bound to.

    Yields:
        The list the statements are appended to, filled as requests run.
    """
    statements: list[str] = []
    sync_connection = connection.sync_connection
    assert sync_connection is not None

    def record(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(sync_connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(sync_connection, "before_cursor_execute", record)


async def test_the_demo_growbox_is_returned_whole(http_client: httpx.AsyncClient) -> None:
    """Asserted field by field, including the empty state of every point."""
    site, facility, control_zone, points = await build_demo_growbox(http_client)

    response = await http_client.get(configuration_url(facility["id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["facility"] == {
        "id": facility["id"],
        "name": "Basil Growbox",
        "code": "basil-growbox",
        "facility_type": "growbox",
        "status": "active",
    }
    assert body["site"] == {
        "id": site["id"],
        "name": "Home",
        "code": "home",
        "timezone": "UTC",
    }
    assert body["control_zones"] == [
        {
            "id": control_zone["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
            "status": "active",
            "points": [
                {
                    "point_id": point["id"],
                    "code": point["code"],
                    "role": role,
                }
                for point, role in zip(points, DEMO_ROLES, strict=True)
            ],
        }
    ]
    assert body["points"] == [
        {
            "id": point["id"],
            "code": fields["code"],
            "name": fields["name"],
            "point_kind": fields["point_kind"],
            "metric_type": fields["metric_type"],
            "data_type": fields["data_type"],
            "unit": fields.get("unit"),
            "status": "active",
            "state": {"value": None, "quality": "no_data", "observed_at": None},
        }
        for point, fields in zip(points, DEMO_POINTS, strict=True)
    ]


async def test_an_empty_facility_is_described_as_empty(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])

    response = await http_client.get(configuration_url(facility["id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["control_zones"] == []
    assert body["points"] == []
    assert body["facility"]["id"] == facility["id"]


async def test_an_unknown_facility_is_reported_as_missing(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.get(configuration_url(str(uuid4())))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "facility_not_found"


async def test_another_facilitys_points_are_not_included(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    other_facility = await create_facility(
        http_client,
        site["id"],
        name="Mint Growbox",
        code="mint-growbox",
    )
    await create_point(http_client, site["id"], facility_id=facility["id"])
    await create_point(
        http_client,
        site["id"],
        facility_id=other_facility["id"],
        code="mint_temperature",
    )

    response = await http_client.get(configuration_url(facility["id"]))

    assert [point["code"] for point in response.json()["points"]] == ["air_temperature"]


async def test_a_point_of_the_whole_site_is_described_when_a_zone_uses_it(
    http_client: httpx.AsyncClient,
) -> None:
    """Every ``point_id`` a zone names must be answerable from the same document."""
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    control_zone = await create_control_zone(http_client, facility["id"])
    site_point = await create_point(http_client, site["id"], code="outdoor_temperature")
    await create_assignment(
        http_client,
        control_zone["id"],
        site_point["id"],
        "secondary_measurement",
    )

    response = await http_client.get(configuration_url(facility["id"]))

    body = response.json()
    assert [point["code"] for point in body["points"]] == ["outdoor_temperature"]
    assert body["control_zones"][0]["points"][0]["point_id"] == site_point["id"]


async def test_archived_zones_and_points_are_left_out(http_client: httpx.AsyncClient) -> None:
    site, facility, control_zone, points = await build_demo_growbox(http_client)
    retired_zone = await create_control_zone(
        http_client,
        facility["id"],
        name="Old Zone",
        code="old-zone",
        zone_type="lighting",
    )
    await archive(http_client, f"{CONTROL_ZONES_URL}/{retired_zone['id']}")
    await archive(http_client, f"{POINTS_URL}/{points[1]['id']}")

    response = await http_client.get(configuration_url(facility["id"]))

    body = response.json()
    assert [zone["id"] for zone in body["control_zones"]] == [control_zone["id"]]
    assert [point["code"] for point in body["points"]] == [
        "air_temperature",
        "fan_power",
        "fan_running",
    ]
    assert [link["code"] for link in body["control_zones"][0]["points"]] == [
        "air_temperature",
        "fan_power",
        "fan_running",
    ]


async def test_archived_zones_and_points_are_included_on_request(
    http_client: httpx.AsyncClient,
) -> None:
    _site, facility, control_zone, points = await build_demo_growbox(http_client)
    retired_zone = await create_control_zone(
        http_client,
        facility["id"],
        name="Old Zone",
        code="old-zone",
        zone_type="lighting",
    )
    await archive(http_client, f"{CONTROL_ZONES_URL}/{retired_zone['id']}")
    await archive(http_client, f"{POINTS_URL}/{points[1]['id']}")

    response = await http_client.get(
        configuration_url(facility["id"]),
        params={"include_archived": "true"},
    )

    body = response.json()
    assert [zone["id"] for zone in body["control_zones"]] == [
        control_zone["id"],
        retired_zone["id"],
    ]
    assert [zone["status"] for zone in body["control_zones"]] == ["active", "archived"]
    assert len(body["points"]) == 4
    assert [point["status"] for point in body["points"]] == [
        "active",
        "archived",
        "active",
        "active",
    ]
    assert len(body["control_zones"][0]["points"]) == 4


async def test_a_point_of_several_zones_is_described_once(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    climate = await create_control_zone(http_client, facility["id"])
    safety = await create_control_zone(
        http_client,
        facility["id"],
        name="Safety",
        code="safety",
        zone_type="safety",
    )
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    await create_assignment(http_client, climate["id"], point["id"], "primary_measurement")
    await create_assignment(http_client, safety["id"], point["id"], "safety_interlock")

    response = await http_client.get(configuration_url(facility["id"]))

    body = response.json()
    assert len(body["points"]) == 1
    assert [link["role"] for zone in body["control_zones"] for link in zone["points"]] == [
        "primary_measurement",
        "safety_interlock",
    ]


@pytest.mark.parametrize("include_archived", ["false", "true"])
async def test_the_query_count_does_not_grow_with_the_facility(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    include_archived: str,
) -> None:
    small = await build_large_growbox(
        http_client,
        site_code="small-site",
        facility_code="small-box",
        zone_count=1,
        points_per_zone=4,
    )
    large = await build_large_growbox(
        http_client,
        site_code="large-site",
        facility_code="large-box",
        zone_count=3,
        points_per_zone=4,
    )
    params: dict[str, str] = {"include_archived": include_archived}

    with count_selects(connection) as small_statements:
        small_response = await http_client.get(configuration_url(small["id"]), params=params)
    with count_selects(connection) as large_statements:
        large_response = await http_client.get(configuration_url(large["id"]), params=params)

    assert small_response.status_code == 200, small_response.text
    assert large_response.status_code == 200, large_response.text
    assert len(large_response.json()["control_zones"]) == 3
    assert len(large_response.json()["points"]) == 12
    assert len(large_statements) == len(small_statements), large_statements
    assert len(large_statements) <= MAX_CONFIGURATION_SELECTS, large_statements
