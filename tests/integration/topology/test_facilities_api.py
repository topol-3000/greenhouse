"""End-to-end coverage of the facilities API against a real PostgreSQL instance.

What is specific to a facility is that it lives *inside* a site: its code is
unique only within that site, it cannot be created inside an archived one, and
it cannot be moved to another one afterwards. Those rules are what this module
asserts. The mechanics a facility shares with every other collection — the
pagination envelope, deterministic paging, the timestamp refresh, the archive
lifecycle — are asserted once in ``test_sites_api.py``.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    FACILITIES_URL,
    SITES_URL,
    archive,
    create_facility,
    create_site,
)


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
    listing = await http_client.get(FACILITIES_URL, params={"site_id": site["id"]})

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "facility_code_conflict"
    assert body["error"]["details"] == {"site_id": site["id"], "code": "basil-growbox"}
    assert listing.json()["total"] == 1, "the refused request must leave nothing behind"


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


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    """Uniqueness within the site covers archived rows too."""
    site = await create_site(http_client)
    created = await create_facility(http_client, site["id"], code="basil-growbox")
    await archive(http_client, f"{FACILITIES_URL}/{created['id']}")

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


async def test_creation_inside_an_archived_site_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    site = await create_site(http_client)
    await archive(http_client, f"{SITES_URL}/{site['id']}")

    response = await http_client.post(
        FACILITIES_URL,
        json={
            "site_id": site["id"],
            "name": "Basil Growbox",
            "code": "basil-growbox",
            "facility_type": "growbox",
        },
    )
    listing = await http_client.get(FACILITIES_URL, params={"site_id": site["id"]})

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "parent_archived"
    assert body["error"]["details"] == {"site_id": site["id"]}
    assert listing.json()["total"] == 0, "the refused request must leave nothing behind"


async def test_unknown_facility_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{FACILITIES_URL}/{missing_id}")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "facility_not_found"
    assert body["error"]["details"] == {"facility_id": str(missing_id)}


async def test_collection_filters_by_site(http_client: httpx.AsyncClient) -> None:
    home = await create_site(http_client, code="home", name="Home")
    office = await create_site(http_client, code="office", name="Office")
    at_home = await create_facility(http_client, home["id"], code="home-growbox")
    await create_facility(http_client, office["id"], code="office-growbox")

    response = await http_client.get(FACILITIES_URL, params={"site_id": home["id"]})

    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [at_home["id"]]


async def test_collection_combines_the_filters(http_client: httpx.AsyncClient) -> None:
    """Every filter narrows the same query; they must intersect, not replace."""
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
    archived = await create_facility(http_client, home["id"], code="old-growbox")
    await archive(http_client, f"{FACILITIES_URL}/{archived['id']}")

    response = await http_client.get(
        FACILITIES_URL,
        params={"site_id": home["id"], "facility_type": "growbox", "status": "active"},
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
    """A filter names no entity, so an identifier that matches nothing is not a 404."""
    site = await create_site(http_client)
    await create_facility(http_client, site["id"])

    response = await http_client.get(FACILITIES_URL, params={"site_id": str(uuid4())})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


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
    """A facility cannot cross a site boundary; the structural link is fixed."""
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
