"""End-to-end coverage of the zone-point assignment API against real PostgreSQL.

This is where the headline assignment rules live — a zone and a point must
belong to the same site and the same facility, a role must suit the kind of
point that takes it, and a zone has at most one primary measurement. Every one
of them is asserted by its refusal, and where a row could still have been
written the refusal is checked against the table as well.

Two deliberate domain decisions are protected here too, because a naive
implementation would get both wrong: the same point may hold two different roles
in one zone, and a point that belongs to the site as a whole may take part in
any zone on that site.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    CONTROL_ZONES_URL,
    POINTS_URL,
    archive,
    assign,
    assignments_url,
    count_rows,
    create_control_zone,
    create_facility,
    create_growbox,
    create_point,
    create_site,
)

FAN_POWER: dict[str, Any] = {
    "code": "fan_power",
    "name": "Fan power",
    "point_kind": "control",
    "metric_type": "fan_power",
    "data_type": "boolean",
    "unit": None,
}
"""A control point, for the roles a measurement point cannot take."""


async def count_assignments(connection: AsyncConnection) -> int:
    """Return how many assignment rows exist.

    Args:
        connection: The test's connection, inside the rolled-back transaction.

    Returns:
        The number of rows in ``zone_point_assignments``.
    """
    return await count_rows(connection, "zone_point_assignments")


async def test_the_created_link_carries_the_role_and_the_point_metadata(
    http_client: httpx.AsyncClient,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])

    response = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["control_zone_id"] == control_zone["id"]
    assert body["point_id"] == point["id"]
    assert body["role"] == "primary_measurement"
    assert body["id"]
    assert body["created_at"]
    assert body["point_code"] == "air_temperature"
    assert body["point_name"] == "Air temperature"
    assert body["point_kind"] == "measurement"
    assert body["data_type"] == "float"
    assert body["unit"] == "°C"


async def test_a_point_of_the_whole_site_can_join_any_zone(
    http_client: httpx.AsyncClient,
) -> None:
    """A point without a facility belongs to the site; every zone on it may read it."""
    site, _facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], code="outdoor_temperature")

    response = await assign(http_client, control_zone["id"], point["id"], "secondary_measurement")

    assert response.status_code == 201, response.text


async def test_a_point_of_another_site_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The Definition of Done names this rule explicitly."""
    _site, _facility, control_zone = await create_growbox(http_client)
    other_site = await create_site(http_client, name="Allotment", code="allotment")
    other_facility = await create_facility(
        http_client,
        other_site["id"],
        code="mint-growbox",
        name="Mint Growbox",
    )
    foreign_point = await create_point(
        http_client,
        other_site["id"],
        facility_id=other_facility["id"],
    )

    response = await assign(
        http_client,
        control_zone["id"],
        foreign_point["id"],
        "primary_measurement",
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "cross_site_assignment"
    assert await count_assignments(connection) == 0


async def test_a_point_of_another_facility_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site, _facility, control_zone = await create_growbox(http_client)
    other_facility = await create_facility(
        http_client,
        site["id"],
        code="mint-growbox",
        name="Mint Growbox",
    )
    point = await create_point(http_client, site["id"], facility_id=other_facility["id"])

    response = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "cross_facility_assignment"
    assert await count_assignments(connection) == 0


@pytest.mark.parametrize(
    ("point_kind", "role", "point_fields"),
    [
        ("measurement", "control_output", {"data_type": "float", "unit": "°C"}),
        ("control", "primary_measurement", {"data_type": "boolean", "unit": None}),
        ("measurement", "status_feedback", {"data_type": "float", "unit": "°C"}),
    ],
)
async def test_a_role_that_does_not_suit_the_kind_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    point_kind: str,
    role: str,
    point_fields: dict[str, Any],
) -> None:
    """One refusal for each of the three constrained roles.

    Which kinds each role admits is written down once, in
    ``ROLE_ALLOWED_POINT_KINDS``, and asserted entry by entry in
    ``tests/unit/test_zone_point_assignment_schemas.py``. What these cases add
    is that the service consults that mapping and answers 409 when it says no.
    """
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        code="some_point",
        metric_type="some_metric",
        point_kind=point_kind,
        **point_fields,
    )

    response = await assign(http_client, control_zone["id"], point["id"], role)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "role_kind_mismatch"
    assert body["error"]["details"]["role"] == role
    assert body["error"]["details"]["point_kind"] == point_kind
    assert await count_assignments(connection) == 0


@pytest.mark.parametrize(
    ("point_kind", "role", "point_fields"),
    [
        ("derived", "primary_measurement", {"data_type": "float", "unit": "°C"}),
        ("control", "control_output", {"data_type": "boolean", "unit": None}),
        ("status", "status_feedback", {"data_type": "boolean", "unit": None}),
        ("measurement", "safety_interlock", {"data_type": "float", "unit": "°C"}),
    ],
)
async def test_a_role_that_suits_the_kind_is_accepted(
    http_client: httpx.AsyncClient,
    point_kind: str,
    role: str,
    point_fields: dict[str, Any],
) -> None:
    """A constrained role admitting its second kind, and an open role admitting any.

    ``derived`` is the kind a narrow reading of "primary measurement" would
    lock out, and ``safety_interlock`` is deliberately unconstrained.
    """
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        code="some_point",
        metric_type="some_metric",
        point_kind=point_kind,
        **point_fields,
    )

    response = await assign(http_client, control_zone["id"], point["id"], role)

    assert response.status_code == 201, response.text


async def test_a_second_primary_measurement_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """A zone is controlled against one process variable, so it has one primary."""
    site, facility, control_zone = await create_growbox(http_client)
    first = await create_point(http_client, site["id"], facility_id=facility["id"])
    second = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        code="air_humidity",
        name="Air humidity",
        metric_type="air_humidity",
        unit="%",
    )
    assert (
        await assign(http_client, control_zone["id"], first["id"], "primary_measurement")
    ).status_code == 201

    response = await assign(http_client, control_zone["id"], second["id"], "primary_measurement")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "primary_measurement_exists"
    assert body["error"]["details"]["assigned_point_id"] == first["id"]


async def test_another_zone_may_have_its_own_primary_measurement(
    http_client: httpx.AsyncClient,
) -> None:
    """The limit is per zone, not per point or per facility."""
    site, facility, control_zone = await create_growbox(http_client)
    other_zone = await create_control_zone(
        http_client,
        facility["id"],
        code="root-zone",
        name="Root Zone",
        zone_type="irrigation",
    )
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    assert (
        await assign(http_client, control_zone["id"], point["id"], "primary_measurement")
    ).status_code == 201

    response = await assign(http_client, other_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 201, response.text


async def test_the_same_point_may_hold_two_roles_in_one_zone(
    http_client: httpx.AsyncClient,
) -> None:
    """A control point can be both the zone's output and its safety interlock."""
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"], **FAN_POWER)

    first = await assign(http_client, control_zone["id"], point["id"], "control_output")
    second = await assign(http_client, control_zone["id"], point["id"], "safety_interlock")

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]


async def test_the_identical_link_twice_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    assert (
        await assign(http_client, control_zone["id"], point["id"], "secondary_measurement")
    ).status_code == 201

    response = await assign(
        http_client,
        control_zone["id"],
        point["id"],
        "secondary_measurement",
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "assignment_exists"
    assert await count_assignments(connection) == 1


async def test_the_database_refuses_a_duplicate_link_as_well(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The service check is a courtesy; the unique constraint is the guarantee."""
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    created = await assign(
        http_client,
        control_zone["id"],
        point["id"],
        "secondary_measurement",
    )
    assert created.status_code == 201, created.text

    with pytest.raises(IntegrityError):
        await connection.execute(
            text(
                "INSERT INTO zone_point_assignments "
                "(id, control_zone_id, point_id, role, created_at) "
                "VALUES (:id, :zone, :point, :role, now())"
            ),
            {
                "id": str(uuid4()),
                "zone": control_zone["id"],
                "point": point["id"],
                "role": "secondary_measurement",
            },
        )


async def test_an_archived_zone_refuses_new_links(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    await archive(http_client, f"{CONTROL_ZONES_URL}/{control_zone['id']}")

    response = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "parent_archived"
    assert await count_assignments(connection) == 0


async def test_an_archived_point_cannot_be_linked(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    await archive(http_client, f"{POINTS_URL}/{point['id']}")

    response = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"point_id": point["id"]}
    assert await count_assignments(connection) == 0


async def test_an_unknown_zone_is_reported_as_missing(http_client: httpx.AsyncClient) -> None:
    site, facility, _control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])

    response = await assign(http_client, str(uuid4()), point["id"], "primary_measurement")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "control_zone_not_found"


async def test_an_unknown_point_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    """A missing point is 404 whether its identifier came from body or path."""
    _site, _facility, control_zone = await create_growbox(http_client)

    response = await assign(http_client, control_zone["id"], str(uuid4()), "primary_measurement")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "point_not_found"


async def test_listing_returns_the_links_with_their_points(
    http_client: httpx.AsyncClient,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    temperature = await create_point(http_client, site["id"], facility_id=facility["id"])
    fan = await create_point(http_client, site["id"], facility_id=facility["id"], **FAN_POWER)
    await assign(http_client, control_zone["id"], temperature["id"], "primary_measurement")
    await assign(http_client, control_zone["id"], fan["id"], "control_output")

    response = await http_client.get(assignments_url(control_zone["id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["point_code"] for item in body["items"]] == ["air_temperature", "fan_power"]
    assert [item["role"] for item in body["items"]] == ["primary_measurement", "control_output"]
    assert body["items"][1]["unit"] is None
    assert body["items"][1]["data_type"] == "boolean"


async def test_listing_covers_only_the_zone_asked_about(
    http_client: httpx.AsyncClient,
) -> None:
    site, facility, control_zone = await create_growbox(http_client)
    other_zone = await create_control_zone(
        http_client,
        facility["id"],
        code="root-zone",
        name="Root Zone",
        zone_type="irrigation",
    )
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    response = await http_client.get(assignments_url(other_zone["id"]))

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_listing_an_unknown_zone_is_reported_as_missing(
    http_client: httpx.AsyncClient,
) -> None:
    """The nested collection resolves its zone rather than answering an empty page."""
    response = await http_client.get(assignments_url(str(uuid4())))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "control_zone_not_found"


async def test_deleting_a_link_leaves_the_point_alone(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The only ``DELETE`` in the API removes a link, never an entity."""
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    created = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")
    assignment_id: str = created.json()["id"]

    response = await http_client.delete(f"{assignments_url(control_zone['id'])}/{assignment_id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert await count_assignments(connection) == 0
    listed = await http_client.get(assignments_url(control_zone["id"]))
    assert listed.json()["total"] == 0
    survivor = await http_client.get(f"{POINTS_URL}/{point['id']}")
    assert survivor.status_code == 200
    assert survivor.json()["status"] == "active"


async def test_deleting_frees_the_role_again(http_client: httpx.AsyncClient) -> None:
    """The primary-measurement limit counts live rows, not rows that ever existed."""
    site, facility, control_zone = await create_growbox(http_client)
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    created = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")
    await http_client.delete(f"{assignments_url(control_zone['id'])}/{created.json()['id']}")

    response = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    assert response.status_code == 201, response.text


async def test_deleting_an_unknown_link_is_reported_as_missing(
    http_client: httpx.AsyncClient,
) -> None:
    _site, _facility, control_zone = await create_growbox(http_client)

    response = await http_client.delete(f"{assignments_url(control_zone['id'])}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "assignment_not_found"


async def test_a_zone_cannot_delete_another_zones_link(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The assignment identifier is only meaningful under the zone that owns it."""
    site, facility, control_zone = await create_growbox(http_client)
    other_zone = await create_control_zone(
        http_client,
        facility["id"],
        code="root-zone",
        name="Root Zone",
        zone_type="irrigation",
    )
    point = await create_point(http_client, site["id"], facility_id=facility["id"])
    created = await assign(http_client, control_zone["id"], point["id"], "primary_measurement")

    response = await http_client.delete(
        f"{assignments_url(other_zone['id'])}/{created.json()['id']}"
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "assignment_not_found"
    assert await count_assignments(connection) == 1
