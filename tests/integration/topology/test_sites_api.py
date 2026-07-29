"""End-to-end coverage of the sites API against a real PostgreSQL instance.

Sites are the simplest entity in the topology, so this module carries the
behaviour that every collection shares — the pagination envelope, deterministic
paging, the partial update and the archive-instead-of-delete lifecycle. The
facility, zone and point modules assert only what is specific to them and rely
on this one for the shared mechanics.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest

from tests.integration.factories import SITES_URL, archive, create_site


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


@pytest.mark.parametrize(
    "field",
    [
        {"code": "Basil_Growbox"},
        {"name": ""},
        {"timezone": "Not/AZone"},
    ],
)
async def test_a_field_that_breaks_its_shared_constraint_is_refused(
    http_client: httpx.AsyncClient,
    field: dict[str, Any],
) -> None:
    """One case per shared value type, proving the route validates through them.

    The constraints themselves — the slug pattern, the name bounds and the IANA
    timezone lookup — are exercised case by case in ``tests/unit/test_types.py``.
    What a request can add is that the endpoint really is wired to them and that
    a refusal arrives in the documented envelope.
    """
    response = await http_client.post(SITES_URL, json={"name": "Home", "code": "home"} | field)

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


async def test_paging_a_collection_repeats_and_skips_nothing(
    http_client: httpx.AsyncClient,
) -> None:
    """The documented ``created_at ASC, id ASC`` order, seen from a client.

    Walking the whole collection window by window and getting every row back
    exactly once is what deterministic ordering is *for*; asserting the order
    of a single page would pass even with a non-deterministic tiebreak.
    """
    for index in range(6):
        await create_site(http_client, code=f"site-{index}", name=f"Site {index}")

    collected: list[str] = []
    for offset in (0, 2, 4):
        response = await http_client.get(SITES_URL, params={"limit": 2, "offset": offset})
        page = response.json()
        assert page["total"] == 6
        assert page["limit"] == 2
        assert page["offset"] == offset
        collected.extend(item["code"] for item in page["items"])

    assert collected == [f"site-{index}" for index in range(6)]


async def test_collection_respects_the_status_filter(http_client: httpx.AsyncClient) -> None:
    active = await create_site(http_client, code="home", name="Home")
    archived = await create_site(http_client, code="office", name="Office")
    await archive(http_client, f"{SITES_URL}/{archived['id']}")

    active_response = await http_client.get(SITES_URL, params={"status": "active"})
    archived_response = await http_client.get(SITES_URL, params={"status": "archived"})

    assert [item["id"] for item in active_response.json()["items"]] == [active["id"]]
    assert [item["id"] for item in archived_response.json()["items"]] == [archived["id"]]
    assert active_response.json()["total"] == 1


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
    """The ``TimestampMixin`` contract, asserted once for every entity that uses it."""
    created = await create_site(http_client)

    response = await http_client.patch(
        f"{SITES_URL}/{created['id']}",
        json={"name": "Home lab"},
    )

    body = response.json()
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] > created["updated_at"]


async def test_update_is_persisted(http_client: httpx.AsyncClient) -> None:
    """The request-scoped session commits; a later request sees the change."""
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


async def test_update_of_an_unknown_site_is_reported_as_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    response = await http_client.patch(f"{SITES_URL}/{uuid4()}", json={"name": "Home lab"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "site_not_found"


async def test_archiving_keeps_the_site_readable(http_client: httpx.AsyncClient) -> None:
    created = await create_site(http_client)

    archived = await archive(http_client, f"{SITES_URL}/{created['id']}")
    reread = await http_client.get(f"{SITES_URL}/{created['id']}")

    assert archived["status"] == "archived"
    assert reread.status_code == 200
    assert reread.json()["status"] == "archived"


async def test_archiving_frees_no_code(http_client: httpx.AsyncClient) -> None:
    """Uniqueness covers archived rows too, so a code is never silently reused."""
    created = await create_site(http_client, code="home")
    await archive(http_client, f"{SITES_URL}/{created['id']}")

    response = await http_client.post(SITES_URL, json={"name": "Home", "code": "home"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "site_code_conflict"


async def test_error_responses_leak_no_technical_detail(
    http_client: httpx.AsyncClient,
) -> None:
    """A conflict raised behind a real driver still answers in the safe envelope."""
    await create_site(http_client, code="home")

    response = await http_client.post(SITES_URL, json={"name": "Home", "code": "home"})

    text = response.text.lower()
    assert "traceback" not in text
    assert "insert into" not in text
    assert "asyncpg" not in text
    assert "postgresql" not in text
