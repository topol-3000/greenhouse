from collections.abc import Callable
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from ai_greenhouse.api.errors import VALIDATION_ERROR_CODE
from ai_greenhouse.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT, Page, PageParams
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)

    @application.get("/probe/items", response_model=Page[str])
    async def items(params: Annotated[PageParams, Depends(PageParams)]) -> Page[str]:
        return Page[str](items=[], total=0, limit=params.limit, offset=params.offset)

    return application


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.parametrize(
    ("query", "expected_limit", "expected_offset"),
    [
        ("", DEFAULT_LIMIT, 0),
        (f"?limit={MAX_LIMIT}", MAX_LIMIT, 0),
        (f"?limit={MIN_LIMIT}", MIN_LIMIT, 0),
        ("?limit=25&offset=10", 25, 10),
    ],
)
async def test_window_is_applied_to_the_response_envelope(
    app: FastAPI,
    query: str,
    expected_limit: int,
    expected_offset: int,
) -> None:
    async with client(app) as http_client:
        response = await http_client.get(f"/probe/items{query}")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": expected_limit,
        "offset": expected_offset,
    }


@pytest.mark.parametrize(
    "query",
    [
        "?offset=-1",
        "?limit=abc",
        "?offset=abc",
        "?limit=0",
        f"?limit={MAX_LIMIT + 1}",
        "?limit=10000",
        "?limit=-5",
    ],
)
async def test_unusable_window_is_rejected_with_the_error_envelope(
    app: FastAPI,
    query: str,
) -> None:
    """An out-of-range ``limit`` fails loudly instead of being clamped.

    A client paging with ``limit=500`` and stepping ``offset`` by 500 against a
    silently clamped window would have read two fifths of the collection and
    been told nothing.
    """
    async with client(app) as http_client:
        response = await http_client.get(f"/probe/items{query}")

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == VALIDATION_ERROR_CODE
    assert body["error"]["details"]["errors"][0]["location"][0] == "query"
