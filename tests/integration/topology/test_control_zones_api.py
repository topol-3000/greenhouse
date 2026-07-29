"""End-to-end coverage of the control zones API against a real PostgreSQL instance.

A zone is the entity most likely to be modelled wrongly, so three deliberate
decisions are protected here: a zone carries no site of its own, zones may
overlap inside one facility, and a zone code is unique only within its facility.
The mechanics a zone shares with every other collection are asserted once in
``test_sites_api.py``.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    CONTROL_ZONES_URL,
    FACILITIES_URL,
    archive,
    create_control_zone,
    create_facility,
    create_site,
)

ZONE_TYPES: list[str] = [
    "climate",
    "irrigation",
    "lighting",
    "measurement",
    "nutrient_solution",
    "safety",
]


async def create_growbox(http_client: httpx.AsyncClient, **overrides: object) -> dict[str, object]:
    """Create a site with one facility in it and return the facility.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the facility body.

    Returns:
        The decoded response body of the created facility.
    """
    site = await create_site(http_client)
    return await create_facility(http_client, site["id"], **overrides)


async def test_creation_returns_the_persisted_zone(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert body["facility_id"] == facility["id"]
    assert body["name"] == "Main Climate"
    assert body["code"] == "main-climate"
    assert body["zone_type"] == "climate"
    assert body["status"] == "active"
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_created_zone_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_the_representation_carries_no_site(http_client: httpx.AsyncClient) -> None:
    """The site is derived through the facility and never denormalised onto a zone."""
    facility = await create_growbox(http_client)

    created = await create_control_zone(http_client, facility["id"])
    read = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")
    listed = await http_client.get(CONTROL_ZONES_URL)

    assert "site_id" not in created
    assert "site_id" not in read.json()
    assert all("site_id" not in item for item in listed.json()["items"])


async def test_the_table_has_no_site_column(connection: AsyncConnection) -> None:
    """The invariant holds in the schema too, not only in the serialisation."""
    columns = await connection.scalars(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'control_zones'"
        )
    )

    assert "site_id" not in set(columns), "the site is reached through facility_id alone"


async def test_a_site_cannot_be_supplied_instead_of_a_facility(
    http_client: httpx.AsyncClient,
) -> None:
    """A zone hangs off a facility only; ``site_id`` is not an accepted parent."""
    site = await create_site(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "site_id": site["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_facility_in_the_body_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    """A missing entity is 404 wherever its identifier was written."""
    missing_facility_id = uuid4()

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": str(missing_facility_id),
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "facility_not_found"
    assert body["error"]["details"] == {"facility_id": str(missing_facility_id)}


async def test_duplicate_code_in_the_same_facility_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"], code="main-climate")

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Second Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )
    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "control_zone_code_conflict"
    assert body["error"]["details"] == {
        "facility_id": facility["id"],
        "code": "main-climate",
    }
    assert listing.json()["total"] == 1, "the refused request must leave nothing behind"


async def test_the_same_code_is_free_in_another_facility(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    greenhouse = await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    await create_control_zone(http_client, growbox["id"], code="main-climate")

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": greenhouse["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    assert response.status_code == 201, "zone codes are scoped to their facility"
    assert response.json()["code"] == "main-climate"
    assert response.json()["facility_id"] == greenhouse["id"]


async def test_zones_may_overlap_in_one_facility(http_client: httpx.AsyncClient) -> None:
    """Overlapping zones are a domain feature, resolved later by priority and policy.

    Neither the type nor the count is constrained: two climate zones may cover
    the same growbox, and every documented type may coexist in it.
    """
    facility = await create_growbox(http_client)

    for zone_type in ZONE_TYPES:
        await create_control_zone(
            http_client,
            facility["id"],
            code=zone_type.replace("_", "-"),
            zone_type=zone_type,
        )
    second_climate = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Lower Climate",
            "code": "lower-climate",
            "zone_type": "climate",
        },
    )
    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    assert second_climate.status_code == 201
    assert listing.json()["total"] == len(ZONE_TYPES) + 1


async def test_creation_inside_an_archived_facility_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await archive(http_client, f"{FACILITIES_URL}/{facility['id']}")

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )
    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"facility_id": facility["id"]}
    assert listing.json()["total"] == 0, "the refused request must leave nothing behind"


async def test_unknown_zone_is_reported_as_not_found(http_client: httpx.AsyncClient) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{CONTROL_ZONES_URL}/{missing_id}")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "control_zone_not_found"
    assert body["error"]["details"] == {"control_zone_id": str(missing_id)}


async def test_collection_filters_by_facility(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    greenhouse = await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    in_growbox = await create_control_zone(http_client, growbox["id"], code="growbox-climate")
    await create_control_zone(http_client, greenhouse["id"], code="greenhouse-climate")

    response = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": growbox["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [in_growbox["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    """Every filter narrows the same query; they must intersect, not replace."""
    facility = await create_growbox(http_client)
    wanted = await create_control_zone(
        http_client,
        facility["id"],
        code="main-climate",
        zone_type="climate",
    )
    await create_control_zone(
        http_client,
        facility["id"],
        code="main-irrigation",
        zone_type="irrigation",
    )
    archived = await create_control_zone(
        http_client,
        facility["id"],
        code="old-climate",
        zone_type="climate",
    )
    await archive(http_client, f"{CONTROL_ZONES_URL}/{archived['id']}")

    response = await http_client.get(
        CONTROL_ZONES_URL,
        params={"facility_id": facility["id"], "zone_type": "climate", "status": "active"},
    )

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [wanted["id"]]


async def test_unknown_filter_values_are_refused(http_client: httpx.AsyncClient) -> None:
    type_response = await http_client.get(CONTROL_ZONES_URL, params={"zone_type": "vibes"})
    status_response = await http_client.get(CONTROL_ZONES_URL, params={"status": "retired"})
    facility_response = await http_client.get(
        CONTROL_ZONES_URL,
        params={"facility_id": "not-a-uuid"},
    )

    for response in (type_response, status_response, facility_response):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_partial_update_changes_only_the_submitted_fields(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"name": "Main Climate v2", "zone_type": "measurement", "status": "archived"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "Main Climate v2"
    assert body["zone_type"] == "measurement"
    assert body["status"] == "archived"
    assert body["code"] == created["code"]
    assert body["facility_id"] == created["facility_id"]


async def test_changing_the_code_is_refused(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"], code="main-climate")

    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"code": "moved"},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["code"]


async def test_changing_the_facility_is_refused(http_client: httpx.AsyncClient) -> None:
    """A zone cannot cross a facility boundary; the structural link is fixed."""
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    greenhouse = await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    created = await create_control_zone(http_client, growbox["id"])

    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"facility_id": greenhouse["id"]},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["facility_id"]
    assert body["error"]["details"]["control_zone_id"] == created["id"]


async def test_a_referenced_facility_cannot_be_deleted_in_the_database(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """``ON DELETE RESTRICT`` guards the facility even against direct SQL.

    The API exposes no ``DELETE`` at all, so the refusal is asserted at the
    level where a deletion could still be attempted. This is the last statement
    of the test: the failure leaves the transaction unusable, and the fixture
    rolls it back.
    """
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"])

    with pytest.raises(IntegrityError):
        await connection.execute(
            text("DELETE FROM facilities WHERE id = :facility_id"),
            {"facility_id": facility["id"]},
        )
