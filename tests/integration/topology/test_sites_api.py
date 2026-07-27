"""End-to-end coverage of the sites API against a real PostgreSQL instance.

Every invariant is covered by a test that asserts the refusal, not only by one
that asserts the happy path.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest

SITES_URL = "/api/v1/sites"


async def create_site(
    http_client: httpx.AsyncClient,
    **overrides: Any,
) -> dict[str, Any]:
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


async def test_creation_returns_the_persisted_site(http_client: httpx.AsyncClient) -> None:
    response = await http_client.post(
        SITES_URL,
        json={"name": "Home", "code": "home", "timezone": "Europe/Kiev"},
    )

    body = response.json()
    assert response.status_code == 201
    assert body["name"] == "Home"
    assert body["code"] == "home"
    assert body["timezone"] == "Europe/Kiev"
    assert body["status"] == "active"
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_created_site_is_readable_by_id(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    response = await http_client.get(f"{SITES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_timezone_defaults_to_utc(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    assert created["timezone"] == "UTC"


async def test_duplicate_code_is_refused(http_client: httpx.AsyncClient) -> None:
    await create_site(http_client, code="home")

    response = await http_client.post(
        SITES_URL,
        json={"name": "Second home", "code": "home"},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "site_code_conflict"
    assert body["error"]["details"] == {"code": "home"}


async def test_duplicate_code_does_not_create_a_second_site(
    http_client: httpx.AsyncClient,
) -> None:
    await create_site(http_client, code="home")

    await http_client.post(SITES_URL, json={"name": "Second home", "code": "home"})
    listing = await http_client.get(SITES_URL)

    assert listing.json()["total"] == 1


@pytest.mark.parametrize(
    "code",
    ["Home", "-home", "home-", "_home", "home_", "my home", "my.home", "grüne-box", ""],
)
async def test_invalid_code_is_refused(http_client: httpx.AsyncClient, code: str) -> None:
    response = await http_client.post(SITES_URL, json={"name": "Home", "code": code})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("timezone", ["Not/AZone", "Europe/Nowhere", "", "../etc/passwd"])
async def test_invalid_timezone_is_refused(
    http_client: httpx.AsyncClient,
    timezone: str,
) -> None:
    response = await http_client.post(
        SITES_URL,
        json={"name": "Home", "code": "home", "timezone": timezone},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_name_is_stripped(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client, name="   Home   ")

    assert created["name"] == "Home"


@pytest.mark.parametrize("name", ["", "   ", "a" * 201])
async def test_name_outside_its_bounds_is_refused(
    http_client: httpx.AsyncClient,
    name: str,
) -> None:
    response = await http_client.post(SITES_URL, json={"name": name, "code": "home"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_site_is_reported_as_not_found(http_client: httpx.AsyncClient) -> None:
    missing_id = uuid4()

    response = await http_client.get(f"{SITES_URL}/{missing_id}")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "site_not_found"
    assert body["error"]["details"] == {"site_id": str(missing_id)}


async def test_collection_returns_the_pagination_envelope(
    http_client: httpx.AsyncClient,
) -> None:
    await create_site(http_client, code="home", name="Home")
    await create_site(http_client, code="office", name="Office")

    response = await http_client.get(SITES_URL)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["code"] for item in body["items"]] == ["home", "office"]


async def test_collection_respects_limit_and_offset(http_client: httpx.AsyncClient) -> None:
    for index in range(5):
        await create_site(http_client, code=f"site-{index}", name=f"Site {index}")

    response = await http_client.get(SITES_URL, params={"limit": 2, "offset": 2})

    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["code"] for item in body["items"]] == ["site-2", "site-3"]


async def test_collection_respects_the_status_filter(http_client: httpx.AsyncClient) -> None:
    active = await create_site(http_client, code="home", name="Home")
    archived = await create_site(http_client, code="office", name="Office")
    await http_client.patch(f"{SITES_URL}/{archived['id']}", json={"status": "archived"})

    active_response = await http_client.get(SITES_URL, params={"status": "active"})
    archived_response = await http_client.get(SITES_URL, params={"status": "archived"})

    assert [item["id"] for item in active_response.json()["items"]] == [active["id"]]
    assert [item["id"] for item in archived_response.json()["items"]] == [archived["id"]]
    assert active_response.json()["total"] == 1


async def test_unknown_status_filter_is_refused(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(SITES_URL, params={"status": "retired"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_collection_ordering_is_stable_across_requests(
    http_client: httpx.AsyncClient,
) -> None:
    for index in range(6):
        await create_site(http_client, code=f"site-{index}", name=f"Site {index}")

    orderings = []
    for _ in range(3):
        response = await http_client.get(SITES_URL)
        orderings.append([item["id"] for item in response.json()["items"]])

    assert orderings[0] == orderings[1] == orderings[2]


async def test_pages_do_not_repeat_or_skip_a_site(http_client: httpx.AsyncClient) -> None:
    for index in range(6):
        await create_site(http_client, code=f"site-{index}", name=f"Site {index}")

    collected: list[str] = []
    for offset in (0, 2, 4):
        response = await http_client.get(SITES_URL, params={"limit": 2, "offset": offset})
        collected.extend(item["code"] for item in response.json()["items"])

    assert collected == [f"site-{index}" for index in range(6)]


async def test_partial_update_changes_only_the_submitted_fields(
    http_client: httpx.AsyncClient,
) -> None:
    created = await create_site(http_client, name="Home", code="home")

    response = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"name": "Home lab", "timezone": "Europe/Kiev"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "Home lab"
    assert body["timezone"] == "Europe/Kiev"
    assert body["code"] == "home"
    assert body["status"] == "active"


async def test_partial_update_refreshes_updated_at_only(
    http_client: httpx.AsyncClient,
) -> None:
    created = await create_site(http_client)

    response = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"name": "Home lab"},
    )

    body = response.json()
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]


async def test_update_is_persisted(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    await http_client.patch(f"{SITES_URL}/{created['id']}", json={"name": "Home lab"})
    response = await http_client.get(f"{SITES_URL}/{created['id']}")

    assert response.json()["name"] == "Home lab"


async def test_changing_the_code_is_refused(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client, code="home")

    response = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"code": "moved"},
    )

    body = response.json()
    assert response.status_code == 409
    assert body["error"]["code"] == "immutable_field"
    assert body["error"]["details"]["fields"] == ["code"]


async def test_refused_code_change_leaves_the_site_untouched(
    http_client: httpx.AsyncClient,
) -> None:
    created = await create_site(http_client, code="home", name="Home")

    await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"code": "moved", "name": "Home lab"},
    )
    response = await http_client.get(f"{SITES_URL}/{created['id']}")

    body = response.json()
    assert body["code"] == "home"
    assert body["name"] == "Home"


async def test_update_with_an_invalid_timezone_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    created = await create_site(http_client)

    response = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"timezone": "Not/AZone"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_of_an_unknown_site_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.patch(f"{SITES_URL}/{uuid4()}", json={"name": "Home lab"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "site_not_found"


async def test_archiving_keeps_the_site_readable(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    archived = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"status": "archived"},
    )
    reread = await http_client.get(f"{SITES_URL}/{created['id']}")

    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert reread.status_code == 200
    assert reread.json()["status"] == "archived"


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client, code="home")
    await http_client.patch(f"{SITES_URL}/{created['id']}", json={"status": "archived"})

    response = await http_client.post(SITES_URL, json={"name": "Home", "code": "home"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "site_code_conflict"


async def test_sites_cannot_be_deleted(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    response = await http_client.delete(f"{SITES_URL}/{created['id']}")

    assert response.status_code == 405, "archiving is the only way to retire a site"


async def test_unknown_fields_are_refused(http_client: httpx.AsyncClient) -> None:
    response = await http_client.post(
        SITES_URL,
        json={"name": "Home", "code": "home", "owner": "someone"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_error_responses_leak_no_technical_detail(
    http_client: httpx.AsyncClient,
) -> None:
    await create_site(http_client, code="home")

    response = await http_client.post(SITES_URL, json={"name": "Home", "code": "home"})

    text = response.text.lower()
    assert "traceback" not in text
    assert "insert into" not in text
    assert "asyncpg" not in text
    assert "postgresql" not in text
