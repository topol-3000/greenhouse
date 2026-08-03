"""End-to-end coverage of the points API against a real PostgreSQL instance.

The most important assertions in this file are the ones about what a point is
*not*: it carries no physical address and no current value. Those two absences
are what let a sensor be replaced without invalidating a single
recorded measurement, and they are the first thing a convenient-looking change
would break.

The rest of the module covers what is specific to a point: its parents may be a
site alone or a site and a facility, its code is unique per site, the unit and
range rules follow from ``data_type``, and the fields that define its meaning
are fixed for life. The mechanics a point shares with every other collection —
the pagination envelope, deterministic paging, the timestamp refresh, the
archive lifecycle — are asserted once in ``test_sites_api.py``.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    FACILITIES_URL,
    POINTS_URL,
    SITES_URL,
    archive,
    count_rows,
    create_facility,
    create_point,
    create_site,
    point_body,
)

DOCUMENTED_COLUMNS: set[str] = {
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
    "reported_point_id",
    "status",
    "created_at",
    "updated_at",
}
"""Exactly the columns documented for ``points``.

Asserting the whole set, rather than the absence of a list of guessed names,
is what makes this a contract: a ``gpio`` column added in a hurry fails here
without anyone having had to think of the word ``gpio`` in advance.

``reported_point_id`` is a reference to another *point* and not to hardware,
which is why it is allowed here: it says which logical point reports a control
point's state back, and still says nothing about a device, a channel or an
address.
"""


async def create_growbox(
    http_client: httpx.AsyncClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a site with one facility inside it.

    Args:
        http_client: The client under test.

    Returns:
        A tuple of the created site and facility.
    """
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    return site, facility


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


async def test_created_point_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


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


async def test_a_derived_point_is_accepted_without_any_computation(
    http_client: httpx.AsyncClient,
) -> None:
    """``derived`` is a valid kind; nothing computes one yet."""
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


# --- What a point must never carry ----------------------------------------


async def test_the_points_table_has_exactly_the_documented_columns(
    connection: AsyncConnection,
) -> None:
    """The single most important invariant this module protects.

    A physical address ties the point to the hardware it is supposed to
    outlive; a current value ties it to one moment in time. A binding would live
    on its own table, and the value lives in
    ``point_current_states``.
    """
    columns = await connection.scalars(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'points'")
    )

    assert set(columns) == DOCUMENTED_COLUMNS


async def test_the_representation_has_exactly_the_documented_fields(
    http_client: httpx.AsyncClient,
) -> None:
    """What the table refuses to store, the API must also refuse to expose."""
    site = await create_site(http_client)

    created = await create_point(http_client, site["id"])
    read = await http_client.get(f"{POINTS_URL}/{created['id']}")
    listed = await http_client.get(POINTS_URL)

    assert set(created) == DOCUMENTED_COLUMNS
    assert set(read.json()) == DOCUMENTED_COLUMNS
    assert all(set(item) == DOCUMENTED_COLUMNS for item in listed.json()["items"])


@pytest.mark.parametrize("field", ["gpio", "value"])
async def test_a_physical_address_or_a_value_is_refused_in_the_request(
    http_client: httpx.AsyncClient,
    field: str,
) -> None:
    """One case for each of the two absences; the schema forbids extras wholesale."""
    site = await create_site(http_client)

    response = await http_client.post(POINTS_URL, json=point_body(site["id"], **{field: "17"}))

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


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_no_endpoint_writes_a_point_value(
    http_client: httpx.AsyncClient,
    method: str,
) -> None:
    """Creating a point creates the projection; nothing may write a value into it."""
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])

    response = await http_client.request(
        method,
        f"{POINTS_URL}/{created['id']}/state",
        json={"value": 21.5, "quality": "good"},
    )

    assert response.status_code == 405, "the state projection is read-only over HTTP"


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


async def test_a_facility_of_another_site_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
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
    assert await count_rows(connection, "points") == 0


async def test_creation_inside_an_archived_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await archive(http_client, f"{SITES_URL}/{site['id']}")

    response = await http_client.post(POINTS_URL, json=point_body(site["id"]))

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"site_id": site["id"]}


async def test_creation_inside_an_archived_facility_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site, facility = await create_growbox(http_client)
    await archive(http_client, f"{FACILITIES_URL}/{facility['id']}")

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


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_point(http_client, site["id"])
    await archive(http_client, f"{POINTS_URL}/{created['id']}")

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


async def test_a_non_numeric_point_needs_no_unit(http_client: httpx.AsyncClient) -> None:
    """``boolean`` and ``string`` points are dimensionless by nature."""
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


async def test_an_inverted_range_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        POINTS_URL,
        json=point_body(site["id"], min_value=10, max_value=5),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_fractional_range_survives_the_round_trip(
    http_client: httpx.AsyncClient,
) -> None:
    """``numeric`` columns and JSON numbers must agree on the fractional part."""
    site = await create_site(http_client)

    created = await create_point(http_client, site["id"], min_value=-19.5, max_value=60.25)
    reread = await http_client.get(f"{POINTS_URL}/{created['id']}")

    assert reread.json()["min_value"] == -19.5
    assert reread.json()["max_value"] == 60.25


# --- Listing --------------------------------------------------------------


async def test_collection_filters_by_facility(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    in_growbox = await create_point(http_client, site["id"], facility_id=growbox["id"])
    await create_point(http_client, site["id"], code="outdoor_temperature")

    response = await http_client.get(POINTS_URL, params={"facility_id": growbox["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [in_growbox["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    """Every filter narrows the same query; they must intersect, not replace."""
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
    retired = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        code="old_air_temperature",
    )
    await archive(http_client, f"{POINTS_URL}/{retired['id']}")

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

    for response in (kind_response, status_response, site_response):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


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


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"unit": "°C"}, "unit_not_allowed"),
        ({"min_value": 0}, "range_not_allowed"),
    ],
)
async def test_the_data_type_rules_are_enforced_on_update_too(
    http_client: httpx.AsyncClient,
    change: dict[str, Any],
    expected_code: str,
) -> None:
    """A point cannot acquire through ``PATCH`` what creation would have refused."""
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

    response = await http_client.patch(f"{POINTS_URL}/{created['id']}", json=change)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


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


async def test_changing_a_parent_is_refused(http_client: httpx.AsyncClient) -> None:
    """Neither parent may be reassigned, whether the point had one or not."""
    site, facility = await create_growbox(http_client)
    farm = await create_site(http_client, code="farm", name="Farm")
    created = await create_point(http_client, site["id"])

    site_response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"site_id": farm["id"]},
    )
    facility_response = await http_client.patch(
        f"{POINTS_URL}/{created['id']}",
        json={"facility_id": facility["id"]},
    )

    assert site_response.json()["error"]["details"]["fields"] == ["site_id"]
    assert facility_response.json()["error"]["details"]["fields"] == ["facility_id"]
    for response in (site_response, facility_response):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "immutable_field"


async def test_resubmitting_an_unchanged_immutable_field_is_refused_too(
    http_client: httpx.AsyncClient,
) -> None:
    """The refusal is about the field being present, not about the value differing."""
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
    """A rejected field does not let the acceptable fields of the same body through."""
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


# --- Transactional integrity ----------------------------------------------


async def test_every_refusal_leaves_the_two_tables_in_step(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """A point and its state are written in one transaction, or not at all.

    Each refusal below is raised at a different moment — parent lookup, unit
    rule, range rule — and none may leave a state row without a point or a
    point without its state.
    """
    site = await create_site(http_client)
    await create_point(http_client, site["id"], code="air_temperature")
    refused_bodies: list[dict[str, Any]] = [
        point_body(str(uuid4()), code="a_point"),
        point_body(site["id"], code="a_point", facility_id=str(uuid4())),
        point_body(site["id"], code="a_point", data_type="boolean"),
        point_body(site["id"], code="a_point", data_type="string", min_value=0),
        point_body(site["id"], code="a_point", min_value=10, max_value=5),
        point_body(site["id"], code="air_temperature", name="Second sensor"),
    ]

    for body in refused_bodies:
        response = await http_client.post(POINTS_URL, json=body)
        assert response.status_code >= 400, response.text

    assert await count_rows(connection, "points") == 1
    assert await count_rows(connection, "point_current_states") == 1


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
