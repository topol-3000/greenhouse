"""The command read endpoints, at the layer that owns them.

Ordering, both filters and the whole readable chain are asserted once, in
``integration/test_automation_demo.py``, where commands actually exist. What is
left here is what that scenario cannot show: the refusals.
"""

from uuid import uuid4

import httpx
import pytest

from tests.integration.factories import COMMANDS_URL


async def test_an_unknown_command_is_reported_missing(http_client: httpx.AsyncClient) -> None:
    """A stale identifier answers 404 rather than an empty representation."""
    response = await http_client.get(f"{COMMANDS_URL}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "command_not_found"


async def test_a_command_can_neither_be_created_nor_changed(
    http_client: httpx.AsyncClient,
) -> None:
    """A command is a consequence of accepted telemetry, so the resource is read-only."""
    created = await http_client.post(COMMANDS_URL, json={})
    patched = await http_client.patch(f"{COMMANDS_URL}/{uuid4()}", json={"desired_value": True})

    assert created.status_code == 405, created.text
    assert patched.status_code == 405, patched.text


@pytest.mark.parametrize(
    "limit",
    [pytest.param(0, id="below the lower bound"), pytest.param(1001, id="above the upper bound")],
)
async def test_a_limit_outside_the_documented_range_is_refused(
    http_client: httpx.AsyncClient,
    limit: int,
) -> None:
    """The window is refused rather than clamped, so a client is never told less silently."""
    response = await http_client.get(COMMANDS_URL, params={"limit": limit})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "limit",
    [pytest.param(1, id="the lower bound"), pytest.param(1000, id="the upper bound")],
)
async def test_both_ends_of_the_documented_range_are_accepted(
    http_client: httpx.AsyncClient,
    limit: int,
) -> None:
    """The bounds themselves are inside the range they bound."""
    response = await http_client.get(COMMANDS_URL, params={"limit": limit})

    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
