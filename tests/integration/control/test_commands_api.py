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


async def test_a_stored_command_cannot_be_changed(http_client: httpx.AsyncClient) -> None:
    """A command records what was asked for, so nothing edits one afterwards.

    Only its delivery state moves, and only through an Edge acknowledgement. A
    manual command is created through ``POST``; there is no way to re-decide one
    that already exists.
    """
    patched = await http_client.patch(f"{COMMANDS_URL}/{uuid4()}", json={"desired_value": True})
    deleted = await http_client.delete(f"{COMMANDS_URL}/{uuid4()}")

    assert patched.status_code == 405, patched.text
    assert deleted.status_code == 405, deleted.text


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
