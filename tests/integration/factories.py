"""Request builders shared by the integration tests.

Every test in ``tests/integration`` drives the application through HTTP, so the
setup of a test is a short sequence of ``POST``\\ s. Those sequences were copied
into each test module before; they live here once instead, which keeps the test
modules to the behaviour they assert.

These helpers only ever call the public API — nothing here writes to the
database directly. A test that needs to observe the database does so through
the ``connection`` fixture, which is the same transaction the requests run in.
"""

from typing import Any, NamedTuple

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SITES_URL: str = "/api/v1/sites"
FACILITIES_URL: str = "/api/v1/facilities"
CONTROL_ZONES_URL: str = "/api/v1/control-zones"
POINTS_URL: str = "/api/v1/points"
CONTROL_LOOPS_URL: str = "/api/v1/control-loops"
COMMANDS_URL: str = "/api/v1/commands"

DOMAIN_COLLECTION_URLS: tuple[str, ...] = (
    SITES_URL,
    FACILITIES_URL,
    CONTROL_ZONES_URL,
    POINTS_URL,
    CONTROL_LOOPS_URL,
)
"""Every collection a client can reach. None of them accepts ``DELETE``."""

LOWER_THRESHOLD: float = 24.0
UPPER_THRESHOLD: float = 26.0
"""The hysteresis band of the documented demonstration, in ``°C``."""

AUTOMATION_POINTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "primary_measurement",
        {
            "code": "air_temperature",
            "name": "Air Temperature",
            "point_kind": "measurement",
            "metric_type": "air_temperature",
            "data_type": "float",
            "unit": "°C",
        },
    ),
    (
        "control_output",
        {
            "code": "fan_power",
            "name": "Fan Power",
            "point_kind": "control",
            "metric_type": "fan_power",
            "data_type": "boolean",
            "unit": None,
        },
    ),
    (
        "status_feedback",
        {
            "code": "fan_running",
            "name": "Fan Running",
            "point_kind": "status",
            "metric_type": "fan_running",
            "data_type": "boolean",
            "unit": None,
        },
    ),
)
"""The three points a ``hysteresis-v1`` loop binds, with their zone roles."""

HUMIDITY_POINT: dict[str, Any] = {
    "code": "air_humidity",
    "name": "Air Humidity",
    "point_kind": "measurement",
    "metric_type": "air_humidity",
    "data_type": "float",
    "unit": "%",
}
"""The secondary measurement a climate zone carries but no loop reads."""


def assignments_url(zone_id: str) -> str:
    """Return the assignment collection URL of one zone.

    Args:
        zone_id: The zone whose composition is addressed.

    Returns:
        The collection URL nested under that zone.
    """
    return f"{CONTROL_ZONES_URL}/{zone_id}/points"


def configuration_url(facility_id: str) -> str:
    """Return the configuration URL of one facility.

    Args:
        facility_id: The facility to describe.

    Returns:
        The composite read URL of that facility.
    """
    return f"{FACILITIES_URL}/{facility_id}/configuration"


async def create_site(http_client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Create a site and return its representation.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created site.
    """
    payload: dict[str, Any] = {"name": "Home", "code": "home"} | overrides
    response = await http_client.post(SITES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_facility(
    http_client: httpx.AsyncClient,
    site_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a facility and return its representation.

    Args:
        http_client: The client under test.
        site_id: The site to create the facility in.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created facility.
    """
    payload: dict[str, Any] = {
        "site_id": site_id,
        "name": "Basil Growbox",
        "code": "basil-growbox",
        "facility_type": "growbox",
    } | overrides
    response = await http_client.post(FACILITIES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_control_zone(
    http_client: httpx.AsyncClient,
    facility_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a control zone and return its representation.

    Args:
        http_client: The client under test.
        facility_id: The facility to create the zone in.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created zone.
    """
    payload: dict[str, Any] = {
        "facility_id": facility_id,
        "name": "Main Climate",
        "code": "main-climate",
        "zone_type": "climate",
    } | overrides
    response = await http_client.post(CONTROL_ZONES_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def point_body(site_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a valid point creation body.

    Args:
        site_id: The site to create the point on.
        **overrides: Fields replacing the defaults.

    Returns:
        The request body, ready to be posted.
    """
    return {
        "site_id": site_id,
        "code": "air_temperature",
        "name": "Air temperature",
        "point_kind": "measurement",
        "metric_type": "air_temperature",
        "data_type": "float",
        "unit": "°C",
    } | overrides


async def create_point(
    http_client: httpx.AsyncClient,
    site_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a point and return its representation.

    Args:
        http_client: The client under test.
        site_id: The site the point belongs to.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created point.
    """
    response = await http_client.post(POINTS_URL, json=point_body(site_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def assign(
    http_client: httpx.AsyncClient,
    zone_id: str,
    point_id: str,
    role: str,
) -> httpx.Response:
    """Assign a point to a zone and return the raw response.

    The status is deliberately not asserted: most of the assignment rules are
    refusals, and the tests that assert them need the response itself.

    Args:
        http_client: The client under test.
        zone_id: The zone to assign the point to.
        point_id: The point to assign.
        role: The role it plays in the zone.

    Returns:
        The unchecked response.
    """
    return await http_client.post(
        assignments_url(zone_id),
        json={"point_id": point_id, "role": role},
    )


async def create_assignment(
    http_client: httpx.AsyncClient,
    zone_id: str,
    point_id: str,
    role: str,
) -> dict[str, Any]:
    """Assign a point to a zone, requiring the assignment to be accepted.

    Args:
        http_client: The client under test.
        zone_id: The zone to assign the point to.
        point_id: The point to assign.
        role: The role it plays in the zone.

    Returns:
        The decoded response body of the created assignment.
    """
    response = await assign(http_client, zone_id, point_id, role)
    assert response.status_code == 201, response.text
    return response.json()


class Growbox(NamedTuple):
    """A site with one facility and one climate zone inside it."""

    site: dict[str, Any]
    facility: dict[str, Any]
    control_zone: dict[str, Any]


async def create_growbox(http_client: httpx.AsyncClient, **overrides: Any) -> Growbox:
    """Build the topology every point and assignment test starts from.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the *site* body, so a
            second growbox can be built alongside the first.

    Returns:
        The created site, facility and control zone.
    """
    site = await create_site(http_client, **overrides)
    facility = await create_facility(http_client, site["id"])
    control_zone = await create_control_zone(http_client, facility["id"])
    return Growbox(site, facility, control_zone)


class AutomationGrowbox(NamedTuple):
    """A climate zone wired with the three points a control loop needs."""

    site: dict[str, Any]
    facility: dict[str, Any]
    control_zone: dict[str, Any]
    points: dict[str, dict[str, Any]]


async def create_automation_growbox(
    http_client: httpx.AsyncClient,
    **overrides: Any,
) -> AutomationGrowbox:
    """Build the growbox every control-loop and automation test starts from.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the *site* body, so a
            second growbox can be built alongside the first.

    Returns:
        The created topology and its points, keyed by point code.
    """
    site, facility, control_zone = await create_growbox(http_client, **overrides)
    points: dict[str, dict[str, Any]] = {}
    for role, definition in AUTOMATION_POINTS:
        point = await create_point(
            http_client,
            site["id"],
            facility_id=facility["id"],
            **definition,
        )
        await create_assignment(http_client, control_zone["id"], point["id"], role)
        points[point["code"]] = point
    return AutomationGrowbox(site, facility, control_zone, points)


async def create_climate_growbox(
    http_client: httpx.AsyncClient,
    **overrides: Any,
) -> AutomationGrowbox:
    """Build the four-point climate growbox an operational reader shows.

    The automation growbox plus the secondary humidity measurement: the loop
    ignores it, and the dashboard renders it. Nothing here is cloud-owned
    demonstration data — it is provisioned through the public API exactly as an
    external client provisions its own installation.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the *site* body.

    Returns:
        The created topology and its four points, keyed by point code.
    """
    growbox = await create_automation_growbox(http_client, **overrides)
    humidity = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        **HUMIDITY_POINT,
    )
    await create_assignment(
        http_client,
        growbox.control_zone["id"],
        humidity["id"],
        "secondary_measurement",
    )
    growbox.points[humidity["code"]] = humidity
    return growbox


def control_loop_body(growbox: AutomationGrowbox, **overrides: Any) -> dict[str, Any]:
    """Build a valid control-loop creation body for a wired growbox.

    Args:
        growbox: The growbox whose zone and points the loop binds.
        **overrides: Fields replacing the defaults.

    Returns:
        The request body, ready to be posted.
    """
    return {
        "control_zone_id": growbox.control_zone["id"],
        "measurement_point_id": growbox.points["air_temperature"]["id"],
        "control_point_id": growbox.points["fan_power"]["id"],
        "status_point_id": growbox.points["fan_running"]["id"],
        "lower_threshold": LOWER_THRESHOLD,
        "upper_threshold": UPPER_THRESHOLD,
    } | overrides


async def create_control_loop(
    http_client: httpx.AsyncClient,
    growbox: AutomationGrowbox,
    **overrides: Any,
) -> dict[str, Any]:
    """Configure a control loop and return its representation.

    Args:
        http_client: The client under test.
        growbox: The growbox whose zone and points the loop binds.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created loop.
    """
    response = await http_client.post(
        CONTROL_LOOPS_URL,
        json=control_loop_body(growbox, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def archive(http_client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Retire an entity the only way the API allows.

    Args:
        http_client: The client under test.
        url: The entity URL to patch.

    Returns:
        The decoded response body of the archived entity.
    """
    response = await http_client.patch(url, json={"status": "archived"})
    assert response.status_code == 200, response.text
    return response.json()


async def count_rows(connection: AsyncConnection, table: str) -> int:
    """Count the rows of one table inside the test transaction.

    Args:
        connection: The connection the test transaction runs on.
        table: The table to count. Never taken from request input.

    Returns:
        The number of rows currently visible.
    """
    total = await connection.scalar(text(f"SELECT count(*) FROM {table}"))
    return int(total or 0)
