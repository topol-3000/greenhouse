"""Delivery of the dashboard page and its two assets.

These tests own one question: does the application serve the page the browser
needs, from the same origin as the API and with nothing else exposed. What the
page then *renders* is client-side behaviour and is not asserted here; the data
it renders from is asserted in ``tests/integration/test_dashboard_demo.py``.
"""

import httpx

from ai_greenhouse.api.routes.dashboard import SCRIPT_PATH, STYLESHEET_PATH


async def test_the_dashboard_page_is_served_with_its_two_assets(
    http_client: httpx.AsyncClient,
) -> None:
    """``GET /`` answers with the page, and the page names assets that exist."""
    page = await http_client.get("/")
    stylesheet = await http_client.get(STYLESHEET_PATH)
    script = await http_client.get(SCRIPT_PATH)

    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert STYLESHEET_PATH in page.text
    assert SCRIPT_PATH in page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")


async def test_only_the_two_named_assets_are_reachable(
    http_client: httpx.AsyncClient,
) -> None:
    """Three routes, not a directory: no request text is mapped onto a path."""
    unknown = await http_client.get("/static/unknown.js")
    directory = await http_client.get("/static/")

    assert unknown.status_code == 404
    assert directory.status_code == 404


async def test_the_page_and_the_api_share_one_origin_and_no_cors_headers(
    http_client: httpx.AsyncClient,
) -> None:
    """The browser reads ``/api/v1`` from the origin that served ``/``.

    Nothing grants a cross-origin reader access, which is the point: a page
    served by this application needs no such grant, and a second origin that
    would is out of scope.
    """
    page = await http_client.get("/", headers={"Origin": "http://elsewhere.test"})
    api = await http_client.get("/api/v1/sites", headers={"Origin": "http://elsewhere.test"})

    assert page.status_code == 200
    assert api.status_code == 200
    assert "access-control-allow-origin" not in page.headers
    assert "access-control-allow-origin" not in api.headers
