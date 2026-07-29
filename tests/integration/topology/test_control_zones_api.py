"""End-to-end coverage of the control zones API against a real PostgreSQL instance.

Every invariant is covered by a test that asserts the refusal, not only by one
that asserts the happy path. Two deliberate domain decisions are protected here
as well, because a naive implementation would forbid both: zones may overlap,
and a code is only unique within its facility.
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
CONTROL_ZONES_URL = "/api/v1/control-zones"

ZONE_TYPES: list[str] = [
    "climate",
    "irrigation",
    "lighting",
    "measurement",
    "nutrient_solution",
    "safety",
]


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


async def create_growbox(http_client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Create a site with one facility inside it and return the facility.

    Args:
        http_client: The client under test.
        **overrides: Fields replacing the defaults of the facility body.

    Returns:
        The decoded response body of the created facility.
    """
    site = await create_site(http_client)
    return await create_facility(http_client, site["id"], **overrides)


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
    columns = await connection.scalars(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'control_zones'"
        )
    )

    assert "site_id" not in set(columns), "the site is reached through facility_id alone"


async def test_created_zone_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize("zone_type", ZONE_TYPES)
async def test_every_documented_type_is_accepted(
    http_client: httpx.AsyncClient,
    zone_type: str,
) -> None:
    facility = await create_growbox(http_client)

    created = await create_control_zone(
        http_client,
        facility["id"],
        code=zone_type.replace("_", "-"),
        zone_type=zone_type,
    )

    assert created["zone_type"] == zone_type


async def test_unknown_zone_type_is_refused(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Vibes Zone",
            "code": "vibes-zone",
            "zone_type": "vibes",
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

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "control_zone_code_conflict"
    assert body["error"]["details"] == {
        "facility_id": facility["id"],
        "code": "main-climate",
    }


async def test_duplicate_code_does_not_create_a_second_zone(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"], code="main-climate")

    await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Second Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )
    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    assert listing.json()["total"] == 1


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


async def test_two_climate_zones_may_overlap_in_one_facility(
    http_client: httpx.AsyncClient,
) -> None:
    """Overlapping zones are a domain feature, resolved later by priority and policy."""
    facility = await create_growbox(http_client)
    first = await create_control_zone(
        http_client,
        facility["id"],
        code="upper-climate",
        name="Upper Climate",
        zone_type="climate",
    )

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Lower Climate",
            "code": "lower-climate",
            "zone_type": "climate",
        },
    )

    assert response.status_code == 201
    assert response.json()["zone_type"] == "climate"
    assert response.json()["id"] != first["id"]


async def test_zones_of_different_types_may_overlap_in_one_facility(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)

    for zone_type in ZONE_TYPES:
        await create_control_zone(
            http_client,
            facility["id"],
            code=zone_type.replace("_", "-"),
            zone_type=zone_type,
        )

    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    assert listing.json()["total"] == len(ZONE_TYPES)


async def test_creation_inside_an_archived_facility_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await http_client.patch(
        f"{FACILITIES_URL}/{facility['id']}",
        json={"status": "archived"},
    )

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
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"facility_id": facility["id"]}


async def test_refused_creation_leaves_the_facility_empty(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await http_client.patch(
        f"{FACILITIES_URL}/{facility['id']}",
        json={"status": "archived"},
    )

    await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )
    listing = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": facility["id"]})

    assert listing.json()["total"] == 0


@pytest.mark.parametrize("code", ["Climate", "-climate", "climate_", "my climate", ""])
async def test_invalid_code_is_refused(http_client: httpx.AsyncClient, code: str) -> None:
    facility = await create_growbox(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": code,
            "zone_type": "climate",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_name_is_stripped(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)

    created = await create_control_zone(http_client, facility["id"], name="   Main Climate   ")

    assert created["name"] == "Main Climate"


@pytest.mark.parametrize("name", ["", "   ", "a" * 201])
async def test_name_outside_its_bounds_is_refused(
    http_client: httpx.AsyncClient,
    name: str,
) -> None:
    facility = await create_growbox(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": name,
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_zone_is_reported_as_not_found(http_client: httpx.AsyncClient) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{CONTROL_ZONES_URL}/{missing_id}")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "control_zone_not_found"
    assert body["error"]["details"] == {"control_zone_id": str(missing_id)}


async def test_collection_returns_the_pagination_envelope(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"], code="zone-1", name="Zone 1")
    await create_control_zone(http_client, facility["id"], code="zone-2", name="Zone 2")

    response = await http_client.get(CONTROL_ZONES_URL)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["code"] for item in body["items"]] == ["zone-1", "zone-2"]


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


async def test_collection_filters_by_zone_type(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    climate = await create_control_zone(
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

    response = await http_client.get(CONTROL_ZONES_URL, params={"zone_type": "climate"})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [climate["id"]]


async def test_collection_filters_by_status(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    active = await create_control_zone(http_client, facility["id"], code="zone-1")
    archived = await create_control_zone(http_client, facility["id"], code="zone-2")
    await http_client.patch(
        f"{CONTROL_ZONES_URL}/{archived['id']}",
        json={"status": "archived"},
    )

    active_response = await http_client.get(CONTROL_ZONES_URL, params={"status": "active"})
    archived_response = await http_client.get(CONTROL_ZONES_URL, params={"status": "archived"})

    assert [item["id"] for item in active_response.json()["items"]] == [active["id"]]
    assert [item["id"] for item in archived_response.json()["items"]] == [archived["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(http_client, site["id"], code="basil-growbox")
    greenhouse = await create_facility(
        http_client,
        site["id"],
        code="tomato-greenhouse",
        name="Tomato Greenhouse",
        facility_type="greenhouse",
    )
    wanted = await create_control_zone(
        http_client,
        growbox["id"],
        code="main-climate",
        zone_type="climate",
    )
    await create_control_zone(
        http_client,
        growbox["id"],
        code="main-irrigation",
        zone_type="irrigation",
    )
    await create_control_zone(
        http_client,
        greenhouse["id"],
        code="main-climate",
        zone_type="climate",
    )

    response = await http_client.get(
        CONTROL_ZONES_URL,
        params={"facility_id": growbox["id"], "zone_type": "climate"},
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


async def test_filtering_by_an_unknown_facility_returns_an_empty_page(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"])

    response = await http_client.get(CONTROL_ZONES_URL, params={"facility_id": str(uuid4())})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


async def test_collection_respects_limit_and_offset(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    for index in range(5):
        await create_control_zone(http_client, facility["id"], code=f"zone-{index}")

    response = await http_client.get(
        CONTROL_ZONES_URL,
        params={"facility_id": facility["id"], "limit": 2, "offset": 2},
    )

    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["code"] for item in body["items"]] == ["zone-2", "zone-3"]


async def test_pages_do_not_repeat_or_skip_a_zone(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    for index in range(6):
        await create_control_zone(http_client, facility["id"], code=f"zone-{index}")

    collected: list[str] = []
    for offset in (0, 2, 4):
        response = await http_client.get(
            CONTROL_ZONES_URL,
            params={"facility_id": facility["id"], "limit": 2, "offset": offset},
        )
        collected.extend(item["code"] for item in response.json()["items"])

    assert collected == [f"zone-{index}" for index in range(6)]


async def test_collection_ordering_is_stable_across_requests(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    for index in range(6):
        await create_control_zone(http_client, facility["id"], code=f"zone-{index}")

    orderings = []
    for _ in range(3):
        response = await http_client.get(
            CONTROL_ZONES_URL,
            params={"facility_id": facility["id"]},
        )
        orderings.append([item["id"] for item in response.json()["items"]])

    assert orderings[0] == orderings[1] == orderings[2]


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


async def test_partial_update_refreshes_updated_at_only(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"name": "Main Climate v2"},
    )

    body = response.json()
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]


async def test_update_is_persisted(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"name": "Main Climate v2"},
    )
    response = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    assert response.json()["name"] == "Main Climate v2"


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


async def test_resubmitting_the_current_facility_is_refused_too(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"facility_id": facility["id"]},
    )

    assert response.status_code == 409, "facility_id is never accepted, even unchanged"
    assert response.json()["error"]["code"] == "immutable_field"


async def test_refused_code_change_leaves_the_zone_untouched(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"], code="main-climate")

    await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"code": "moved", "name": "Main Climate v2"},
    )
    response = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    body = response.json()
    assert body["code"] == "main-climate"
    assert body["name"] == "Main Climate"


async def test_refused_facility_change_leaves_the_zone_untouched(
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
    created = await create_control_zone(http_client, growbox["id"])

    await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"facility_id": greenhouse["id"], "name": "Main Climate v2"},
    )
    response = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    body = response.json()
    assert body["facility_id"] == growbox["id"]
    assert body["name"] == "Main Climate"


async def test_update_of_an_unknown_zone_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{uuid4()}",
        json={"name": "Main Climate v2"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "control_zone_not_found"


async def test_archiving_keeps_the_zone_readable(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    archived = await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"status": "archived"},
    )
    reread = await http_client.get(f"{CONTROL_ZONES_URL}/{created['id']}")

    assert archived.status_code == 200
    assert reread.status_code == 200
    assert reread.json()["status"] == "archived"


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"], code="main-climate")
    await http_client.patch(
        f"{CONTROL_ZONES_URL}/{created['id']}",
        json={"status": "archived"},
    )

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "control_zone_code_conflict"


async def test_control_zones_cannot_be_deleted(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)
    created = await create_control_zone(http_client, facility["id"])

    response = await http_client.delete(f"{CONTROL_ZONES_URL}/{created['id']}")

    assert response.status_code == 405, "archiving is the only way to retire a zone"


async def test_unknown_fields_are_refused(http_client: httpx.AsyncClient) -> None:
    facility = await create_growbox(http_client)

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
            "priority": 3,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


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


async def test_error_responses_leak_no_technical_detail(
    http_client: httpx.AsyncClient,
) -> None:
    facility = await create_growbox(http_client)
    await create_control_zone(http_client, facility["id"], code="main-climate")

    response = await http_client.post(
        CONTROL_ZONES_URL,
        json={
            "facility_id": facility["id"],
            "name": "Main Climate",
            "code": "main-climate",
            "zone_type": "climate",
        },
    )

    text_body = response.text.lower()
    assert "traceback" not in text_body
    assert "insert into" not in text_body
    assert "asyncpg" not in text_body
    assert "postgresql" not in text_body


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
