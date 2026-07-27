from collections.abc import Callable

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from ai_greenhouse.api.dependencies import (
    DatabaseHealthProbe,
    get_database_health_probe,
)
from ai_greenhouse.api.router import API_V1_PREFIX, api_v1_router
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def override_health_probe(app: FastAPI) -> None:
    async def healthy_probe() -> None:
        return None

    async def provide_probe() -> DatabaseHealthProbe:
        return healthy_probe

    app.dependency_overrides[get_database_health_probe] = provide_probe


def test_versioned_prefix() -> None:
    assert API_V1_PREFIX == "/api/v1"
    assert api_v1_router.prefix == API_V1_PREFIX


def test_versioned_router_carries_no_domain_routes_in_this_story(settings: Settings) -> None:
    app = create_app(settings)

    mounted = {getattr(route, "path", "") for route in app.routes}
    assert not [path for path in mounted if path.startswith(API_V1_PREFIX)]


async def test_versioned_router_is_mounted(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_v1_router, "routes", list(api_v1_router.routes))
    probe_router = APIRouter()

    @probe_router.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"mounted": True}

    api_v1_router.include_router(probe_router)
    app = create_app(settings)

    async with client(app) as http_client:
        response = await http_client.get(f"{API_V1_PREFIX}/probe")

    assert response.status_code == 200
    assert response.json() == {"mounted": True}


async def test_health_stays_at_the_root_and_is_unchanged(settings: Settings) -> None:
    app = create_app(settings)
    override_health_probe(app)

    async with client(app) as http_client:
        root_response = await http_client.get("/health")
        versioned_response = await http_client.get(f"{API_V1_PREFIX}/health")

    assert root_response.status_code == 200
    assert root_response.json() == {
        "status": "ok",
        "service": "ai-greenhouse-api",
        "database": "ok",
    }
    assert versioned_response.status_code == 404, "/health must not be duplicated under /api/v1"
