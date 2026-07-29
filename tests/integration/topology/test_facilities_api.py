"""End-to-end coverage of the facilities API against a real PostgreSQL instance.

Every invariant is covered by a test that asserts the refusal, not only by one
that asserts the happy path.
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


async def test_creation_returns_the_persisted_facility(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert body["site_id"] == site["id"]
    assert body["name"] == "Basil Growbox"
    assert body["code"] == "basil-growbox"
    assert body["facility_type"] == "growbox"
    assert body["status"] == "active"
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_created_facility_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    response = await http_client.get(f"{FACILITIES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize(
    "facility_type",
    ["growbox", "greenhouse", "rack", "seedling_room", "utility"],
)
async def test_every_documented_type_is_accepted(
    http_client: httpx.AsyncClient,
    facility_type: str,
) -> None:
    site = await create_site(http_client)

    created = await create_facility(
        http_client,
        site["id"],
        code=facility_type.replace("_", "-"),
        facility_type=facility_type,
    )

    assert created["facility_type"] == facility_type


async def test_unknown_facility_type_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Death Star",
            "code": "death-star",
            "facility_type": "spaceship",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_site_in_the_body_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    """A missing entity is 404 wherever its identifier was written."""
    missing_site_id = uuid4()

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": str(missing_site_id),
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "site_not_found"
    assert body["error"]["details"] == {"site_id": str(missing_site_id)}


async def test_duplicate_code_on_the_same_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_facility(http_client, site["id"], code="basil-growbox")

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Second growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "facility_code_conflict"
    assert body["error"]["details"] == {"site_id": site["id"], "code": "basil-growbox"}


async def test_duplicate_code_does_not_create_a_second_facility(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_facility(http_client, site["id"], code="basil-growbox")

    await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Second growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )
    listing = await http_client.get(FACILITIES_URL, params={"site_id": site["id"]})

    assert listing.json()["total"] == 1


async def test_the_same_code_is_free_on_another_site(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    await create_facility(http_client, home["id"], code="basil-growbox")

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": office["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    assert response.status_code == 201, "facility codes are scoped to their site"
    assert response.json()["code"] == "basil-growbox"
    assert response.json()["site_id"] == office["id"]


async def test_creation_inside_an_archived_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await http_client.patch(f"{SITES_URL}/{site['id']}", json={"status": "archived"})

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"site_id": site["id"]}


async def test_refused_creation_leaves_the_site_empty(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    await http_client.patch(f"{SITES_URL}/{site['id']}", json={"status": "archived"})

    await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )
    listing = await http_client.get(FACILITIES_URL, params={"site_id": site["id"]})

    assert listing.json()["total"] == 0


@pytest.mark.parametrize("code", ["Growbox", "-growbox", "growbox_", "my growbox", ""])
async def test_invalid_code_is_refused(http_client: httpx.AsyncClient, code: str) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": code,
            "facility_type": "growbox",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_name_is_stripped(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    created = await create_facility(http_client, site["id"], name="   Basil Growbox   ")

    assert created["name"] == "Basil Growbox"


@pytest.mark.parametrize("name", ["", "   ", "a" * 201])
async def test_name_outside_its_bounds_is_refused(
    http_client: httpx.AsyncClient,
    name: str,
) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": name,
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_facility_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{FACILITIES_URL}/{missing_id}")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "facility_not_found"
    assert body["error"]["details"] == {"facility_id": str(missing_id)}


async def test_collection_returns_the_pagination_envelope(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_facility(http_client, site["id"], code="growbox-1", name="Growbox 1")
    await create_facility(http_client, site["id"], code="growbox-2", name="Growbox 2")

    response = await http_client.get(FACILITIES_URL)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["code"] for item in body["items"]] == ["growbox-1", "growbox-2"]


async def test_collection_filters_by_site(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    at_home = await create_facility(http_client, home["id"], code="home-growbox")
    await create_facility(http_client, office["id"], code="office-growbox")

    response = await http_client.get(FACILITIES_URL, params={"site_id": home["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [at_home["id"]]


async def test_collection_filters_by_facility_type(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    growbox = await create_facility(
        http_client,
        site["id"],
        code="basil-growbox",
        facility_type="growbox",
    )
    await create_facility(http_client, site["id"], code="water-node", facility_type="utility")

    response = await http_client.get(FACILITIES_URL, params={"facility_type": "growbox"})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [growbox["id"]]


async def test_collection_filters_by_status(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    active = await create_facility(http_client, site["id"], code="growbox-1")
    archived = await create_facility(http_client, site["id"], code="growbox-2")
    await http_client.patch(
        f"{FACILITIES_URL}/{archived['id']}",
        json={"status": "archived"},
    )

    active_response = await http_client.get(FACILITIES_URL, params={"status": "active"})
    archived_response = await http_client.get(FACILITIES_URL, params={"status": "archived"})

    assert [item["id"] for item in active_response.json()["items"]] == [active["id"]]
    assert [item["id"] for item in archived_response.json()["items"]] == [archived["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    wanted = await create_facility(
        http_client,
        home["id"],
        code="basil-growbox",
        facility_type="growbox",
    )
    await create_facility(http_client, home["id"], code="water-node", facility_type="utility")
    await create_facility(
        http_client,
        office["id"],
        code="basil-growbox",
        facility_type="growbox",
    )

    response = await http_client.get(
        FACILITIES_URL,
        params={"site_id": home["id"], "facility_type": "growbox"},
    )

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [wanted["id"]]


async def test_unknown_filter_values_are_refused(http_client: httpx.AsyncClient) -> None:
    type_response = await http_client.get(
        FACILITIES_URL,
        params={"facility_type": "spaceship"},
    )
    status_response = await http_client.get(FACILITIES_URL, params={"status": "retired"})
    site_response = await http_client.get(FACILITIES_URL, params={"site_id": "not-a-uuid"})

    for response in (type_response, status_response, site_response):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_filtering_by_an_unknown_site_returns_an_empty_page(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_facility(http_client, site["id"])

    response = await http_client.get(FACILITIES_URL, params={"site_id": str(uuid4())})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


async def test_collection_respects_limit_and_offset(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    for index in range(5):
        await create_facility(http_client, site["id"], code=f"growbox-{index}")

    response = await http_client.get(
        FACILITIES_URL,
        params={"site_id": site["id"], "limit": 2, "offset": 2},
    )

    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["code"] for item in body["items"]] == ["growbox-2", "growbox-3"]


async def test_pages_do_not_repeat_or_skip_a_facility(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    for index in range(6):
        await create_facility(http_client, site["id"], code=f"growbox-{index}")

    collected: list[str] = []
    for offset in (0, 2, 4):
        response = await http_client.get(
            FACILITIES_URL,
            params={"site_id": site["id"], "limit": 2, "offset": offset},
        )
        collected.extend(item["code"] for item in response.json()["items"])

    assert collected == [f"growbox-{index}" for index in range(6)]


async def test_collection_ordering_is_stable_across_requests(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    for index in range(6):
        await create_facility(http_client, site["id"], code=f"growbox-{index}")

    orderings = []
    for _ in range(3):
        response = await http_client.get(FACILITIES_URL, params={"site_id": site["id"]})
        orderings.append([item["id"] for item in response.json()["items"]])

    assert orderings[0] == orderings[1] == orderings[2]


async def test_partial_update_changes_only_the_submitted_fields(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    response = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"name": "Basil Growbox v2", "facility_type": "rack", "status": "archived"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "Basil Growbox v2"
    assert body["facility_type"] == "rack"
    assert body["status"] == "archived"
    assert body["code"] == created["code"]
    assert body["site_id"] == created["site_id"]


async def test_partial_update_refreshes_updated_at_only(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    response = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"name": "Basil Growbox v2"},
    )

    body = response.json()
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]


async def test_update_is_persisted(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"name": "Basil Growbox v2"},
    )
    response = await http_client.get(f"{FACILITIES_URL}/{created['id']}")

    assert response.json()["name"] == "Basil Growbox v2"


async def test_changing_the_code_is_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"], code="basil-growbox")

    response = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"code": "moved"},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["code"]


async def test_changing_the_site_is_refused(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    created = await create_facility(http_client, home["id"])

    response = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"site_id": office["id"]},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["site_id"]
    assert body["error"]["details"]["facility_id"] == created["id"]


async def test_resubmitting_the_current_site_is_refused_too(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    response = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"site_id": site["id"]},
    )

    assert response.status_code == 409, "site_id is never accepted, even unchanged"
    assert response.json()["error"]["code"] == "immutable_field"


async def test_refused_code_change_leaves_the_facility_untouched(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"], code="basil-growbox")

    await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"code": "moved", "name": "Basil Growbox v2"},
    )
    response = await http_client.get(f"{FACILITIES_URL}/{created['id']}")

    body = response.json()
    assert body["code"] == "basil-growbox"
    assert body["name"] == "Basil Growbox"


async def test_refused_site_change_leaves_the_facility_untouched(
    http_client: httpx.AsyncClient,
) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    created = await create_facility(http_client, home["id"])

    await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"site_id": office["id"], "name": "Basil Growbox v2"},
    )
    response = await http_client.get(f"{FACILITIES_URL}/{created['id']}")

    body = response.json()
    assert body["site_id"] == home["id"]
    assert body["name"] == "Basil Growbox"


async def test_update_of_an_unknown_facility_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.patch(
        f"{FACILITIES_URL}/{uuid4()}",
        json={"name": "Basil Growbox v2"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "facility_not_found"


async def test_archiving_keeps_the_facility_readable(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    archived = await http_client.patch(
        f"{FACILITIES_URL}/{created['id']}",
        json={"status": "archived"},
    )
    reread = await http_client.get(f"{FACILITIES_URL}/{created['id']}")

    assert archived.status_code == 200
    assert reread.status_code == 200
    assert reread.json()["status"] == "archived"


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"], code="basil-growbox")
    await http_client.patch(f"{FACILITIES_URL}/{created['id']}", json={"status": "archived"})

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "facility_code_conflict"


async def test_facilities_cannot_be_deleted(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"])

    response = await http_client.delete(f"{FACILITIES_URL}/{created['id']}")

    assert response.status_code == 405, "archiving is the only way to retire a facility"


async def test_unknown_fields_are_refused(http_client: httpx.AsyncClient) -> None:
    site = await create_site(http_client)

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
            "usable_area": 1.2,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_error_responses_leak_no_technical_detail(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await create_facility(http_client, site["id"], code="basil-growbox")

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )

    text_body = response.text.lower()
    assert "traceback" not in text_body
    assert "insert into" not in text_body
    assert "asyncpg" not in text_body
    assert "postgresql" not in text_body


async def test_a_referenced_site_cannot_be_deleted_in_the_database(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """``ON DELETE RESTRICT`` guards the site even against direct SQL.

    The API exposes no ``DELETE`` at all, so the refusal is asserted at the
    level where a deletion could still be attempted. This is the last statement
    of the test: the failure leaves the transaction unusable, and the fixture
    rolls it back.
    """
    site = await create_site(http_client)
    await create_facility(http_client, site["id"])

    with pytest.raises(IntegrityError):
        await connection.execute(
            text("DELETE FROM sites WHERE id = :site_id"),
            {"site_id": site["id"]},
        )
