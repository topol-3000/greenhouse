"""What the crop catalog enforces, through its own endpoints.

The pagination envelope, the slug rules and the collection window are asserted
once on sites and on the shared value types; what is asserted here is only what
belongs to a crop: its globally unique code, its lookups, and the fact that
PostgreSQL refuses a duplicate even when the service never saw one.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.models import Crop
from tests.integration.factories import BASIL_CROP, CROPS_URL, create_crop


async def test_a_crop_is_created_read_and_found_by_its_code(
    http_client: httpx.AsyncClient,
) -> None:
    """One crop, three ways of reaching it, one representation."""
    created = await create_crop(http_client)

    fetched = await http_client.get(f"{CROPS_URL}/{created['id']}")
    filtered = await http_client.get(CROPS_URL, params={"code": BASIL_CROP["code"]})

    assert created["code"] == "basil"
    assert created["display_name"] == "Basil"
    assert created["scientific_name"] == "Ocimum basilicum"
    assert created["status"] == "active"
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created
    assert filtered.json() == {"items": [created], "total": 1, "limit": 50, "offset": 0}


async def test_a_scientific_name_is_optional(http_client: httpx.AsyncClient) -> None:
    """Not every crop a grower registers has a botanical name to hand."""
    created = await create_crop(http_client, code="mystery-herb", scientific_name=None)

    assert created["scientific_name"] is None


async def test_a_duplicate_crop_code_is_refused(http_client: httpx.AsyncClient) -> None:
    """A crop code identifies the crop across the whole catalog."""
    await create_crop(http_client)

    duplicate = await http_client.post(CROPS_URL, json=BASIL_CROP | {"display_name": "Basilico"})
    listed = await http_client.get(CROPS_URL)

    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "crop_code_exists"
    assert duplicate.json()["error"]["details"] == {"code": "basil"}
    assert listed.json()["total"] == 1


async def test_an_unknown_crop_is_reported_as_missing(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(f"{CROPS_URL}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "crop_not_found"


async def test_an_unknown_code_filter_answers_an_empty_page(
    http_client: httpx.AsyncClient,
) -> None:
    """A filter matching nothing is an empty page, not a failure."""
    await create_crop(http_client)

    response = await http_client.get(CROPS_URL, params={"code": "dill"})

    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_postgres_refuses_a_duplicate_code_the_service_never_saw(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """The pre-check improves the error; the unique index is what protects the row.

    Two concurrent requests both pass the pre-check and only one may insert, so
    the guarantee is asserted where it actually lives: against the constraint,
    with the service bypassed entirely.
    """
    await create_crop(http_client)

    session.add(Crop(code=BASIL_CROP["code"], display_name="Basilico"))

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
