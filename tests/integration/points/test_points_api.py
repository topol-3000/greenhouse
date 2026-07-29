"""End-to-end coverage of the points API against a real PostgreSQL instance.

Every invariant is covered by a test that asserts the refusal, not only by one
that asserts the happy path.

The most important assertions in this file are the ones about what a point is
*not*: it carries no physical address and no current value. Those two absences
are what let a sensor be replaced in Milestone 6 without invalidating a single
recorded measurement, and they are the first thing a convenient-looking change
would break.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

SITES_URL = "/api/v1/sites"
FACILITIES_URL = "/api/v1/facilities"
POINTS_URL = "/api/v1/points"

POINT_KINDS: list[str] = ["measurement", "control", "status", "derived"]

FORBIDDEN_COLUMNS: list[str] = [
    "device_id",
    "device_channel_id",
    "channel",
    "gpio",
    "register",
    "modbus_address",
    "address",
    "value",
    "last_value",
    "last_reading",
]
"""Columns whose presence on ``points`` would defeat the entity's purpose."""

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
    },
    {
        "code": "fan_running",
        "name": "Fan running",
        "point_kind": "status",
        "metric_type": "fan_running",
        "data_type": "boolean",
    },
]
"""The four points of the Milestone 1 demonstration growbox."""


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


async def create_growbox(http_client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a site with one facility inside it.

    Args:
        http_client: The client under test.

    Returns:
        A tuple of the created site and facility.
    """
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    return site, facility


def point_body(site_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a valid point creation body.

    Args:
        site_id: The site to create the point on.
        **overrides: Fields replacing the defaults.

    Returns:
        The request body.
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
        site_id: The site to create the point on.
        **overrides: Fields replacing the defaults of the request body.

    Returns:
        The decoded response body of the created point.
    """
    response = await http_client.post(POINTS_URL, json=point_body(site_id, **overrides))
    assert response.status_code == 201, response.text
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


async def test_creation_returns_the_persisted_point(http_client: httpx.AsyncClient) -> None:
    site, facility = await create_growbox(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(
            site["id"],
            facility_id=facility["id"],
            min_value=-20,
            max_value=60,
        ),
    )

    body = response.json()
    assert response.status_code == 201
    assert body["site_id"] == site["id"]
    assert body["facility_id"] == facility["id"]
    assert body["code"] == "air_temperature"
    assert body["name"] == "Air temperature"
    assert body["point_kind"] == "measurement"
    assert body["metric_type"] == "air_temperature"
    assert body["data_type"] == "float"
    assert body["unit"] == "°C"
    assert body["min_value"] == -20
    assert body["max_value"] == 60
    assert body["status"] == "active"
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_a_point_may_belong_to_the_site_alone(http_client: httpx.AsyncClient) -> None:
    """An outdoor sensor belongs to the site, not to any one facility."""
    site = await create_site(http_client)

    created = await create_point(
        http_client,
        site["id"],
        code="outdoor_temperature",
        metric_type="outdoor_temperature",
    )

    assert created["facility_id"] is None


async def test_created_point_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize("point_kind", POINT_KINDS)
async def test_every_documented_kind_is_accepted(
    http_client: httpx.AsyncClient,
    point_kind: str,
) -> None:
    site = await create_site(http_client)

    created = await create_point(http_client, site["id"], point_kind=point_kind)

    assert created["point_kind"] == point_kind


async def test_a_derived_point_is_accepted_without_any_computation(
    http_client: httpx.AsyncClient,
) -> None:
    """``derived`` is a valid kind in M1; what computes it arrives much later."""
    site = await create_site(http_client)

    created = await create_point(
        http_client,
        site["id"],
        code="vapour_pressure_deficit",
        metric_type="vapour_pressure_deficit",
        point_kind="derived",
        unit="kPa",
    )

    assert created["point_kind"] == "derived"


async def test_unknown_kind_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(POINTS_URL, json=point_body(site["id"], point_kind="vibes"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- The state projection -------------------------------------------------


async def test_creation_also_creates_exactly_one_state_row(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    rows = list(
        await connection.execute(
            text(
                "SELECT value, observed_at, received_at, quality, revision "
                "FROM point_current_states WHERE point_id = :point_id"
            ),
            {"point_id": created["id"]},
        )
    )

    assert len(rows) == 1
    value, observed_at, received_at, quality, revision = rows[0]
    assert value is None
    assert observed_at is None
    assert received_at is None
    assert quality == "no_data"
    assert revision == 0


async def test_the_state_endpoint_returns_the_empty_projection(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.get(f"{POINTS_URL}/{created['id']}/state")

    body = response.json()
    assert response.status_code == 200
    assert body["point_id"] == created["id"]
    assert body["value"] is None
    assert body["observed_at"] is None
    assert body["received_at"] is None
    assert body["quality"] == "no_data"
    assert body["revision"] == 0
    assert body["updated_at"]


async def test_the_state_of_an_unknown_point_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{POINTS_URL}/{missing_id}/state")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "point_not_found"
    assert body["error"]["details"] == {"point_id": str(missing_id)}


async def test_every_point_has_its_own_state(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    first = await create_point(http_client, site["id"], code="air_temperature")
    second = await create_point(http_client, site["id"], code="air_humidity", unit="%")

    first_state = await http_client.get(f"{POINTS_URL}/{first['id']}/state")
    second_state = await http_client.get(f"{POINTS_URL}/{second['id']}/state")

    assert first_state.json()["point_id"] == first["id"]
    assert second_state.json()["point_id"] == second["id"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_no_endpoint_writes_a_point_value(
    http_client: httpx.AsyncClient,
    method: str,
) -> None:
    """Milestone 1 creates the projection; nothing in it may write a value."""
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.request(
        method,
        f"{POINTS_URL}/{created['id']}/state",
        json={"value": 21.5, "quality": "good"},
    )

    assert response.status_code == 405, "the state projection is read-only in Milestone 1"


async def test_the_state_stays_empty_after_a_point_is_updated(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"name": "Air temp"})
    response = await http_client.get(f"{POINTS_URL}/{created['id']}/state")

    assert response.json()["value"] is None
    assert response.json()["quality"] == "no_data"
    assert response.json()["revision"] == 0


# --- What a point must never carry ----------------------------------------


@pytest.mark.parametrize("column", FORBIDDEN_COLUMNS)
async def test_the_points_table_carries_no_address_and_no_value(
    connection: AsyncConnection,
    column: str,
) -> None:
    """The single most important invariant this module protects.

    A physical address ties the point to the hardware it is supposed to
    outlive; a current value ties it to one moment in time. The binding arrives
    in Milestone 6 on its own table, and the value lives in
    ``point_current_states``.
    """
    columns = await connection.scalars(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'points'")
    )

    assert column not in set(columns)


async def test_the_points_table_has_exactly_the_documented_columns(
    connection: AsyncConnection,
) -> None:
    columns = await connection.scalars(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'points'")
    )

    assert set(columns) == {
        "id",
        "site_id",
        "facility_id",
        "code",
        "name",
        "point_kind",
        "metric_type",
        "data_type",
        "unit",
        "min_value",
        "max_value",
        "status",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize("column", FORBIDDEN_COLUMNS)
async def test_the_representation_carries_no_address_and_no_value(
    http_client: httpx.AsyncClient,
    column: str,
) -> None:
    site = await create_site(http_client)

    created = await create_point(http_client, site["id"])
    read = await http_client.get(f"{POINTS_URL}/{created['id']}")
    listed = await http_client.get(POINTS_URL)

    assert column not in created
    assert column not in read.json()
    assert all(column not in item for item in listed.json()["items"])


@pytest.mark.parametrize("column", FORBIDDEN_COLUMNS)
async def test_a_physical_address_is_refused_in_the_request(
    http_client: httpx.AsyncClient,
    column: str,
) -> None:
    site = await create_site(http_client)

    response = await http_client.post(POINTS_URL, json=point_body(site["id"], **{column: "17"}))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- Parent resolution ----------------------------------------------------


async def test_unknown_site_in_the_body_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    """A missing entity is 404 wherever its identifier was written."""
    missing_site_id = uuid4()

    response = await http_client.post(POINTS_URL, json=point_body(str(missing_site_id)))

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "site_not_found"
    assert body["error"]["details"] == {"site_id": str(missing_site_id)}


async def test_unknown_facility_in_the_body_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    missing_facility_id = uuid4()

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], facility_id=str(missing_facility_id)),
    )

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "facility_not_found"
    assert body["error"]["details"] == {"facility_id": str(missing_facility_id)}


async def test_a_facility_of_another_site_is_refused(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    farm = await create_site(http_client, code="farm", name="Farm")
    farm_facility = await create_facility(http_client, farm["id"])

    response = await http_client.post(
        POINTS_URL,
        json=point_body(home["id"], facility_id=farm_facility["id"]),
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "facility_not_in_site"
    assert body["error"]["details"] == {
        "site_id": home["id"],
        "facility_id": farm_facility["id"],
    }


async def test_a_refused_cross_site_facility_creates_no_point(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    home = await create_site(http_client, code="home", name="Home")
    farm = await create_site(http_client, code="farm", name="Farm")
    farm_facility = await create_facility(http_client, farm["id"])

    await http_client.post(
        POINTS_URL,
        json=point_body(home["id"], facility_id=farm_facility["id"]),
    )

    assert await count_rows(connection, "points") == 0


async def test_creation_inside_an_archived_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await http_client.patch(f"{SITES_URL}/{site['id']}", json={"status": "archived"})

    response = await http_client.post(POINTS_URL, json=point_body(site["id"]))

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"site_id": site["id"]}


async def test_creation_inside_an_archived_facility_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site, facility = await create_growbox(http_client)
    await http_client.patch(f"{FACILITIES_URL}/{facility['id']}", json={"status": "archived"})

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], facility_id=facility["id"]),
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"facility_id": facility["id"]}


# --- Codes ----------------------------------------------------------------


async def test_duplicate_code_on_the_same_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_point(http_client, site["id"], code="air_temperature")

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], code="air_temperature", name="Second sensor"),
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "point_code_conflict"
    assert body["error"]["details"] == {"site_id": site["id"], "code": "air_temperature"}


async def test_the_same_code_is_free_on_another_site(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    farm = await create_site(http_client, code="farm", name="Farm")
    await create_point(http_client, home["id"], code="air_temperature")

    response = await http_client.post(POINTS_URL, json=point_body(farm["id"]))

    assert response.status_code == 201, "point codes are scoped to their site"
    assert response.json()["code"] == "air_temperature"
    assert response.json()["site_id"] == farm["id"]


async def test_the_code_is_unique_across_the_site_not_the_facility(
    http_client: httpx.AsyncClient,
) -> None:
    """Two facilities on one site cannot both hold an ``air_temperature`` point."""
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    greenhouse = await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    await create_point(http_client, site["id"], facility_id=growbox["id"])

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], facility_id=greenhouse["id"]),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "point_code_conflict"


@pytest.mark.parametrize("code", ["Air", "-air", "air_", "air temperature", ""])
async def test_invalid_code_is_refused(http_client: httpx.AsyncClient, code: str) -> None:
    site = await create_site(http_client)

    response = await http_client.post(POINTS_URL, json=point_body(site["id"], code=code))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])
    await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"status": "archived"})

    response = await http_client.post(POINTS_URL, json=point_body(site["id"]))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "point_code_conflict"


# --- Units and ranges -----------------------------------------------------


@pytest.mark.parametrize("data_type", ["float", "integer"])
async def test_a_dimensionless_numeric_point_is_accepted(
    http_client: httpx.AsyncClient,
    data_type: str,
) -> None:
    """pH and other dimensionless quantities have no unit to give."""
    site = await create_site(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(
            site["id"],
            code="nutrient_ph",
            metric_type="nutrient_ph",
            data_type=data_type,
            unit=None,
            min_value=0,
            max_value=14,
        ),
    )

    body = response.json()
    assert response.status_code == 201, response.text
    assert body["unit"] is None
    assert body["data_type"] == data_type
    assert body["min_value"] == 0
    assert body["max_value"] == 14


async def test_a_boolean_point_with_a_unit_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], code="fan_power", data_type="boolean", unit="°C"),
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "unit_not_allowed"
    assert body["error"]["details"] == {"data_type": "boolean"}


async def test_a_boolean_point_without_a_unit_is_accepted(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)

    created = await create_point(
        http_client,
        site["id"],
        code="fan_power",
        name="Fan power",
        point_kind="control",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )

    assert created["unit"] is None


async def test_a_string_point_needs_no_unit(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    created = await create_point(
        http_client,
        site["id"],
        code="controller_mode",
        metric_type="controller_mode",
        point_kind="status",
        data_type="string",
        unit=None,
    )

    assert created["data_type"] == "string"
    assert created["unit"] is None


async def test_an_inverted_range_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], min_value=10, max_value=5),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("data_type", ["boolean", "string"])
async def test_a_range_on_a_non_numeric_point_is_refused(
    http_client: httpx.AsyncClient,
    data_type: str,
) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], data_type=data_type, unit=None, min_value=0, max_value=1),
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "range_not_allowed"
    assert body["error"]["details"] == {"data_type": data_type}


async def test_a_fractional_range_survives_the_round_trip(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)

    created = await create_point(http_client, site["id"], min_value=-19.5, max_value=60.25)
    reread = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert reread.json()["min_value"] == -19.5
    assert reread.json()["max_value"] == 60.25


# --- Listing --------------------------------------------------------------


async def test_collection_returns_the_pagination_envelope(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_point(http_client, site["id"], code="point_1")
    await create_point(http_client, site["id"], code="point_2")

    response = await http_client.get(POINTS_URL)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["code"] for item in body["items"]] == ["point_1", "point_2"]


async def test_collection_filters_by_site(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    farm = await create_site(http_client, code="farm", name="Farm")
    at_home = await create_point(http_client, home["id"])
    await create_point(http_client, farm["id"])

    response = await http_client.get(POINTS_URL, params={"site_id": home["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [at_home["id"]]


async def test_collection_filters_by_facility(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    in_growbox = await create_point(http_client, site["id"], facility_id=growbox["id"])
    await create_point(http_client, site["id"], code="outdoor_temperature")

    response = await http_client.get(POINTS_URL, params={"facility_id": growbox["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [in_growbox["id"]]


async def test_collection_filters_by_kind(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    measurement = await create_point(http_client, site["id"], code="air_temperature")
    await create_point(
        http_client,
        site["id"],
        code="fan_power",
        point_kind="control",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )

    response = await http_client.get(POINTS_URL, params={"point_kind": "measurement"})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [measurement["id"]]


async def test_collection_filters_by_metric_type(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    temperature = await create_point(http_client, site["id"], code="air_temperature")
    await create_point(
        http_client,
        site["id"],
        code="air_humidity",
        metric_type="air_humidity",
        unit="%",
    )

    response = await http_client.get(POINTS_URL, params={"metric_type": "air_temperature"})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [temperature["id"]]


async def test_collection_filters_by_status(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    active = await create_point(http_client, site["id"], code="point_1")
    archived = await create_point(http_client, site["id"], code="point_2")
    await http_client.patch(f"{POINTS_URL}/{archived['id']}", json={"status": "archived"})

    active_response = await http_client.get(POINTS_URL, params={"status": "active"})
    archived_response = await http_client.get(POINTS_URL, params={"status": "archived"})

    assert [item["id"] for item in active_response.json()["items"]] == [active["id"]]
    assert [item["id"] for item in archived_response.json()["items"]] == [archived["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    site, facility = await create_growbox(http_client)
    wanted = await create_point(http_client, site["id"], facility_id=facility["id"])
    await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        code="fan_power",
        point_kind="control",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )
    await create_point(http_client, site["id"], code="outdoor_temperature")

    response = await http_client.get(
        POINTS_URL,
        params={
            "site_id": site["id"],
            "facility_id": facility["id"],
            "point_kind": "measurement",
            "metric_type": "air_temperature",
            "status": "active",
        },
    )

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [wanted["id"]]


async def test_unknown_filter_values_are_refused(http_client: httpx.AsyncClient) -> None:
    kind_response = await http_client.get(POINTS_URL, params={"point_kind": "vibes"})
    status_response = await http_client.get(POINTS_URL, params={"status": "retired"})
    site_response = await http_client.get(POINTS_URL, params={"site_id": "not-a-uuid"})
    metric_response = await http_client.get(POINTS_URL, params={"metric_type": "Air Temp"})

    for response in (kind_response, status_response, site_response, metric_response):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_filtering_by_an_unknown_site_returns_an_empty_page(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_point(http_client, site["id"])

    response = await http_client.get(POINTS_URL, params={"site_id": str(uuid4())})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


async def test_collection_respects_limit_and_offset(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    for index in range(5):
        await create_point(http_client, site["id"], code=f"point_{index}")

    response = await http_client.get(POINTS_URL, params={"limit": 2, "offset": 2})

    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["code"] for item in body["items"]] == ["point_2", "point_3"]


async def test_pages_do_not_repeat_or_skip_a_point(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    for index in range(6):
        await create_point(http_client, site["id"], code=f"point_{index}")

    collected: list[str] = []
    for offset in (0, 2, 4):
        response = await http_client.get(POINTS_URL, params={"limit": 2, "offset": offset})
        collected.extend(item["code"] for item in response.json()["items"])

    assert collected == [f"point_{index}" for index in range(6)]


async def test_collection_ordering_is_stable_across_requests(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    for index in range(6):
        await create_point(http_client, site["id"], code=f"point_{index}")

    orderings = []
    for _ in range(3):
        response = await http_client.get(POINTS_URL)
        orderings.append([item["id"] for item in response.json()["items"]])

    assert orderings[0] == orderings[1] == orderings[2]


# --- Updating -------------------------------------------------------------


async def test_partial_update_changes_only_the_submitted_fields(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"], min_value=-20, max_value=60)

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={
            "name": "Air temperature v2",
            "unit": "K",
            "min_value": 253,
            "max_value": 333,
            "status": "archived",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "Air temperature v2"
    assert body["unit"] == "K"
    assert body["min_value"] == 253
    assert body["max_value"] == 333
    assert body["status"] == "archived"
    assert body["code"] == created["code"]
    assert body["point_kind"] == created["point_kind"]
    assert body["metric_type"] == created["metric_type"]
    assert body["data_type"] == created["data_type"]


async def test_update_is_persisted(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"name": "Air temp v2"})
    response = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert response.json()["name"] == "Air temp v2"


async def test_partial_update_refreshes_updated_at_only(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"name": "Air temp v2"},
    )

    body = response.json()
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]


async def test_a_range_can_be_cleared_with_an_explicit_null(
    http_client: httpx.AsyncClient,
) -> None:
    """``min_value`` is nullable, so ``null`` removes the bound rather than being ignored."""
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"], min_value=-20, max_value=60)

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"min_value": None},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["min_value"] is None
    assert body["max_value"] == 60, "an omitted field is left alone"


async def test_an_update_that_would_invert_the_range_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """The submitted end is judged against the stored one, not on its own."""
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"], min_value=0, max_value=60)

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"max_value": -5})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "invalid_value_range"


async def test_clearing_the_unit_of_a_numeric_point_is_accepted(
    http_client: httpx.AsyncClient,
) -> None:
    """A numeric point may become dimensionless; nothing else about it changes."""
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"unit": None})

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["unit"] is None
    assert body["data_type"] == created["data_type"]


async def test_giving_a_boolean_point_a_unit_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(
        http_client,
        site["id"],
        code="fan_power",
        point_kind="control",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"unit": "°C"})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "unit_not_allowed"


async def test_bounding_a_boolean_point_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(
        http_client,
        site["id"],
        code="fan_power",
        point_kind="control",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json={"min_value": 0})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "range_not_allowed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "moved"),
        ("point_kind", "control"),
        ("metric_type", "relative_humidity"),
        ("data_type", "integer"),
    ],
)
async def test_changing_a_field_that_defines_the_meaning_is_refused(
    http_client: httpx.AsyncClient,
    field: str,
    value: str,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json={field: value})

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == [field]


async def test_changing_the_site_is_refused(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    farm = await create_site(http_client, code="farm", name="Farm")
    created = await create_point(http_client, home["id"])

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"site_id": farm["id"]},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["site_id"]


async def test_changing_the_facility_is_refused(http_client: httpx.AsyncClient) -> None:
    site, facility = await create_growbox(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"facility_id": facility["id"]},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["facility_id"]


async def test_resubmitting_an_unchanged_immutable_field_is_refused_too(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"code": created["code"]},
    )

    assert response.status_code == 409, "code is never accepted, even unchanged"
    assert response.json()["error"]["code"] == "immutable_field"


async def test_a_refused_change_leaves_the_point_untouched(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"data_type": "integer", "name": "Air temperature v2"},
    )
    response = await http_client.get(f"{POINTS_URL}/{created['id']}")

    body = response.json()
    assert body["data_type"] == "float"
    assert body["name"] == "Air temperature"


async def test_update_of_an_unknown_point_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.patch(f"{POINTS_URL}/{uuid4()}", json={"name": "Air temp"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "point_not_found"


async def test_archiving_keeps_the_point_readable(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    archived = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"status": "archived"},
    )
    reread = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert archived.status_code == 200
    assert reread.status_code == 200
    assert reread.json()["status"] == "archived"


async def test_points_cannot_be_deleted(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.delete(f"{POINTS_URL}/{created['id']}")

    assert response.status_code == 405, "archiving is the only way to retire a point"


# --- Transactional integrity ----------------------------------------------


async def test_a_failed_creation_leaves_no_orphan_state_row(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site = await create_site(http_client)
    await create_point(http_client, site["id"], code="air_temperature")

    refused = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], code="air_temperature", name="Second sensor"),
    )

    assert refused.status_code == 409
    assert await count_rows(connection, "points") == 1
    assert await count_rows(connection, "point_current_states") == 1


async def test_every_refusal_leaves_the_two_tables_in_step(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site = await create_site(http_client)
    refused_bodies: list[dict[str, Any]] = [
        point_body(str(uuid4())),
        point_body(site["id"], facility_id=str(uuid4())),
        point_body(site["id"], data_type="boolean"),
        point_body(site["id"], data_type="string", min_value=0),
        point_body(site["id"], min_value=10, max_value=5),
    ]

    for body in refused_bodies:
        response = await http_client.post(POINTS_URL, json=body)
        assert response.status_code >= 400, response.text

    assert await count_rows(connection, "points") == 0
    assert await count_rows(connection, "point_current_states") == 0


async def test_a_referenced_site_cannot_be_deleted_in_the_database(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """``ON DELETE RESTRICT`` guards the site even against direct SQL.

    This is the last statement of the test: the failure leaves the transaction
    unusable, and the fixture rolls it back.
    """
    site = await create_site(http_client)
    await create_point(http_client, site["id"])

    with pytest.raises(IntegrityError):
        await connection.execute(
            text("DELETE FROM sites WHERE id = :site_id"),
            {"site_id": site["id"]},
        )


# --- The demonstration growbox --------------------------------------------


async def test_the_four_demo_points_are_created_with_their_state(
    http_client: httpx.AsyncClient,
) -> None:
    """The Milestone 1 demonstration growbox, end to end."""
    site, facility = await create_growbox(http_client)

    for demo_point in DEMO_POINTS:
        response = await http_client.post(
            POINTS_URL,
            json={"site_id": site["id"], "facility_id": facility["id"]} | demo_point,
        )
        assert response.status_code == 201, response.text

    listing = await http_client.get(POINTS_URL, params={"facility_id": facility["id"]})
    body = listing.json()

    assert body["total"] == 4
    assert [(item["code"], item["point_kind"], item["data_type"]) for item in body["items"]] == [
        ("air_temperature", "measurement", "float"),
        ("air_humidity", "measurement", "float"),
        ("fan_power", "control", "boolean"),
        ("fan_running", "status", "boolean"),
    ]

    for item in body["items"]:
        state = await http_client.get(f"{POINTS_URL}/{item['id']}/state")
        assert state.json()["quality"] == "no_data"
        assert state.json()["value"] is None


async def test_error_responses_leak_no_technical_detail(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_point(http_client, site["id"])

    response = await http_client.post(POINTS_URL, json=point_body(site["id"]))

    text_body = response.text.lower()
    assert "traceback" not in text_body
    assert "insert into" not in text_body
    assert "asyncpg" not in text_body
    assert "postgresql" not in text_body
