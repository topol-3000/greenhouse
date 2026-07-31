"""Delivery of the dashboard page and its two assets.

These tests own two questions: does the application serve the page the browser
needs, from the same origin as the API and with nothing else exposed, and does
that page stay a read model of whatever a producer wrote. What the page then
*renders* is client-side behaviour and is not asserted here; the data it renders
from is asserted in ``tests/integration/test_dashboard_read_model.py``.

The producer-independence assertions read the served assets rather than the
files on disk, because what a browser receives is what can carry a control.
"""

import re

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


async def test_the_page_offers_no_simulation_lifecycle_control(
    http_client: httpx.AsyncClient,
) -> None:
    """Executable simulation is Simulation Lab's, so the page cannot start one.

    Asserted on the delivered markup: a button the browser never receives is a
    button nobody can press, and this fails whether a control comes back as
    markup or as text describing the product as a simulator.
    """
    page = (await http_client.get("/")).text
    lowered = page.lower()

    assert "simulation" not in lowered
    assert "simulated" not in lowered
    assert 'data-field="run-status"' not in page
    # Retry re-reads the API. It is the only action the page offers at all, so a
    # Start, a Stop or anything else that acts on a producer fails here.
    assert set(re.findall(r'data-action="(\w+)"', page)) == {"retry"}
    assert page.count("<button") == 1


async def test_the_script_only_reads_and_never_addresses_a_simulation_run(
    http_client: httpx.AsyncClient,
) -> None:
    """The client is a read model: every request it makes is a ``GET`` of a read API.

    A lifecycle action would need a write, and a run-gated refresh would need the
    run. Neither survives here, so a reintroduced Start button would have nothing
    to call and a poll cannot be made conditional on a producer's state again.
    """
    script = (await http_client.get(SCRIPT_PATH)).text
    lowered = script.lower()

    assert "simulation" not in lowered
    assert "/simulation-runs" not in script
    assert "simulation_run" not in script
    assert re.findall(r'method:\s*"(\w+)"', script) == ["GET"]
    assert '"POST"' not in script
    assert "fetch(" in script
    assert script.count("fetch(") == 1
    assert 'data-action="retry"' in script
    # A command applied by an external gateway has no ``executed_at``, so a
    # page that dated activity by that field alone would show the epoch for
    # every command a real producer applied.
    assert "acknowledged_at" in script
