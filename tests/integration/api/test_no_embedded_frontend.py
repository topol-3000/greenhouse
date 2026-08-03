"""This application serves an API, and no owner-facing frontend.

The owner interface used to be one framework-less page delivered from this
repository. It is now the separate `greenhouse-dashboard` application, which
reaches this one as an ordinary client of the public HTTP API, so the backend
ships no page, no asset and no static mount at all.

These tests own the boundary that replaced the old delivery tests. They assert
the removal where a browser would notice it — the page and its two asset URLs
are ordinary unrouted paths now — and where it could quietly come back: a
mounted static directory, or an asset file checked back into the package. What
the standalone dashboard renders is that repository's concern and is not
asserted here; that the public API still answers everything one of its frames
reads is asserted in ``tests/integration/test_dashboard_read_model.py``.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

import httpx
import pytest
from fastapi import FastAPI
from starlette.routing import Mount

from ai_greenhouse.api.router import API_V1_PREFIX
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL: Final[str] = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "ai_greenhouse"

REMOVED_DASHBOARD_URLS: Final[tuple[str, ...]] = (
    "/",
    "/static/dashboard.css",
    "/static/dashboard.js",
    "/static/",
    "/index.html",
)
"""The page, the two asset routes it named, and two nearby spellings of them."""

FRONTEND_ASSET_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".html", ".htm", ".css", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
)
"""Every extension an embedded page or bundle would arrive under."""

DOCUMENTATION_URLS: Final[tuple[str, ...]] = ("/docs", "/redoc")
"""FastAPI's own API documentation. It is the only HTML this service serves."""


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_the_dashboard_page_and_its_former_assets_are_not_served(app: FastAPI) -> None:
    """Every URL the removed page occupied answers the ordinary unrouted 404.

    Not a redirect, not a placeholder and not a 405: a browser pointed at this
    service gets exactly what it gets for a path that never existed, which is
    also what says no replacement frontend was left behind in its place.
    """
    async with client(app) as http_client:
        responses = {url: await http_client.get(url) for url in REMOVED_DASHBOARD_URLS}
        unknown = await http_client.get("/never-existed")

    assert {url: response.status_code for url, response in responses.items()} == dict.fromkeys(
        REMOVED_DASHBOARD_URLS,
        404,
    )
    for response in responses.values():
        assert response.json() == unknown.json()
        assert not response.headers["content-type"].startswith("text/html")
        assert "location" not in response.headers


async def test_the_only_html_this_service_serves_is_its_own_api_documentation(
    app: FastAPI,
) -> None:
    """`/docs` and `/redoc` return markup. Everything else returns JSON.

    A landing page, an embedded bundle or a proxied dashboard would each have to
    return markup from some other path, so this fails whichever of them is
    reintroduced. The health probe is left unoverridden on purpose: it answers
    JSON whether the database is reachable or not, and the media type is what is
    being asserted.
    """
    async with client(app) as http_client:
        json_urls = ("/health", "/openapi.json", f"{API_V1_PREFIX}/never-existed")
        json_responses = {url: await http_client.get(url) for url in json_urls}
        documentation = {url: await http_client.get(url) for url in DOCUMENTATION_URLS}

    for url, response in json_responses.items():
        assert response.headers["content-type"].startswith("application/json"), url
    for url, response in documentation.items():
        assert response.status_code == 200, url
        assert response.headers["content-type"].startswith("text/html")


def test_startup_mounts_no_static_directory(app: FastAPI) -> None:
    """Nothing in the application maps request text onto the filesystem.

    A ``StaticFiles`` application is mounted, and a mount is the one route kind
    that serves whatever a directory happens to hold. There is none, so there is
    no directory whose contents become a URL.
    """
    assert [route for route in app.routes if isinstance(route, Mount)] == []


def test_the_package_carries_no_frontend_asset() -> None:
    """No page, stylesheet, script or bundle is checked into the backend package.

    The behavioural assertions above prove nothing is *served* today. This one
    prevents the quieter regression: assets copied back into the distribution,
    ready for a route or a mount to find them again.
    """
    assets = sorted(
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in FRONTEND_ASSET_SUFFIXES
    )

    assert assets == []
    assert not (PACKAGE_ROOT / "api" / "static").exists()
    assert not (PACKAGE_ROOT / "api" / "templates").exists()


def test_no_module_serves_a_file_or_renders_a_template() -> None:
    """The backend imports no file-delivery or templating machinery at all.

    ``FileResponse``, ``StaticFiles`` and ``Jinja2Templates`` are the three ways
    FastAPI hands a browser a document. An API-only service needs none of them,
    and each would be the first line of a reintroduced frontend.
    """
    offenders = sorted(
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if any(
            name in path.read_text(encoding="utf-8")
            for name in ("StaticFiles", "FileResponse", "Jinja2Templates", "HTMLResponse")
        )
    )

    assert offenders == []


async def test_the_openapi_document_stays_available_and_describes_the_mounted_api(
    app: FastAPI,
) -> None:
    """Removing the frontend did not disturb the document clients generate from.

    ``/openapi.json`` is fetchable, parses, declares its version and describes
    the operations the application actually mounts. A router removal that broke
    schema generation would surface here rather than at a client's generator.
    """
    async with client(app) as http_client:
        response = await http_client.get("/openapi.json")

    document = json.loads(response.text)

    assert response.status_code == 200
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == app.title
    assert set(document["paths"]) == set(app.openapi()["paths"])
