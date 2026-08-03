"""The explicit relationship between a control point and what reports it back.

A control point says which status point reports its actual state, and it says so
because someone configured it. Nothing here reads a code, a name, a unit or a
metric, and that is the whole reason the relationship exists: two points that
look related are not related, and two points that look unrelated can be.

Each rule is asserted by its refusal, at the layer that refuses it — the service
where the rule spans two rows, and PostgreSQL where it does not.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    POINTS_URL,
    archive,
    configuration_url,
    create_climate_growbox,
    create_facility,
    create_point,
    create_site,
    relate_reported_point,
)

CONTROL_POINT: dict[str, Any] = {
    "code": "fan_power",
    "name": "Fan Power",
    "point_kind": "control",
    "metric_type": "fan_power",
    "data_type": "boolean",
    "unit": None,
}

STATUS_POINT: dict[str, Any] = {
    "code": "fan_running",
    "name": "Fan Running",
    "point_kind": "status",
    "metric_type": "fan_running",
    "data_type": "boolean",
    "unit": None,
}


def reason(response: httpx.Response) -> tuple[int, str, str]:
    """Reduce a refusal to the status, the code and the machine-readable cause."""
    body: dict[str, Any] = response.json()
    return response.status_code, body["error"]["code"], body["error"]["details"].get("reason", "")


async def create_pair(
    http_client: httpx.AsyncClient,
    **status_overrides: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create a facility with one control point and one candidate feedback point.

    Args:
        http_client: The client under test.
        **status_overrides: Fields replacing the defaults of the status point,
            so a test can make exactly one thing wrong with it.

    Returns:
        The facility, the control point and the candidate point.
    """
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    control = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        **CONTROL_POINT,
    )
    candidate = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        **(STATUS_POINT | status_overrides),
    )
    return facility, control, candidate


# --- Configuring the relationship -----------------------------------------


async def test_a_valid_relationship_is_configured_and_returned(
    http_client: httpx.AsyncClient,
) -> None:
    """The write boundary that establishes it, and the read that publishes it."""
    _, control, status = await create_pair(http_client)

    related = await relate_reported_point(http_client, control["id"], status["id"])
    read = await http_client.get(f"{POINTS_URL}/{control['id']}")

    assert related.status_code == 200, related.text
    assert related.json()["reported_point_id"] == status["id"]
    assert read.json()["reported_point_id"] == status["id"]


async def test_the_relationship_can_be_established_at_creation(
    http_client: httpx.AsyncClient,
) -> None:
    """A point provisioned today does not have to be patched tomorrow."""
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    status = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        **STATUS_POINT,
    )

    response = await http_client.post(
        POINTS_URL,
        json={
            "site_id": site["id"],
            "facility_id": facility["id"],
            **CONTROL_POINT,
            "reported_point_id": status["id"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["reported_point_id"] == status["id"]


async def test_the_relationship_can_be_cleared_and_replaced(
    http_client: httpx.AsyncClient,
) -> None:
    """Configuration is correctable, and an omitted field is not a cleared one.

    Sending ``null`` clears the relationship; patching something else while
    leaving it out keeps it. That difference is the whole reason the service
    reads which fields were submitted rather than which are ``None``.
    """
    _, control, status = await create_pair(http_client)
    await relate_reported_point(http_client, control["id"], status["id"])

    renamed = await http_client.patch(f"{POINTS_URL}/{control['id']}", json={"name": "Fan"})
    cleared = await http_client.patch(
        f"{POINTS_URL}/{control['id']}",
        json={"reported_point_id": None},
    )

    assert renamed.json()["reported_point_id"] == status["id"]
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["reported_point_id"] is None


# --- Refusals -------------------------------------------------------------


async def test_a_missing_related_point_is_reported_missing(
    http_client: httpx.AsyncClient,
) -> None:
    """A stale identifier is a 404, like every other reference that does not resolve."""
    _, control, _ = await create_pair(http_client)

    response = await relate_reported_point(http_client, control["id"], str(uuid4()))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "point_not_found"


async def test_a_point_cannot_report_itself(http_client: httpx.AsyncClient) -> None:
    """A control point is not its own feedback, whatever it is called."""
    _, control, _ = await create_pair(http_client)

    response = await relate_reported_point(http_client, control["id"], control["id"])

    assert reason(response) == (409, "invalid_reported_point", "reported_point_is_self")


async def test_only_a_control_point_may_carry_the_relationship(
    http_client: httpx.AsyncClient,
) -> None:
    """A measurement point has no state to report back, so it names no reporter."""
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    measurement = await create_point(http_client, site["id"], facility_id=facility["id"])
    status = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        **STATUS_POINT,
    )

    response = await relate_reported_point(http_client, measurement["id"], status["id"])

    assert reason(response) == (409, "invalid_reported_point", "point_kind_not_control")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {"code": "second_fan", "point_kind": "control", "metric_type": "fan_power"},
            "reported_point_kind_mismatch",
            id="a second control point",
        ),
        pytest.param(
            {"code": "fan_speed", "data_type": "float", "unit": "%"},
            "reported_point_data_type_mismatch",
            id="a non-boolean status point",
        ),
    ],
)
async def test_a_point_of_the_wrong_kind_or_type_is_refused(
    http_client: httpx.AsyncClient,
    overrides: dict[str, Any],
    expected: str,
) -> None:
    """The relationship is between a control point and a boolean status point.

    Anything else would let a client publish a relationship the manual command
    boundary would then have to refuse, which is a worse place to find out.
    """
    _, control, candidate = await create_pair(http_client, **overrides)

    response = await relate_reported_point(http_client, control["id"], candidate["id"])

    assert reason(response) == (409, "invalid_reported_point", expected)


async def test_an_archived_point_cannot_be_related(http_client: httpx.AsyncClient) -> None:
    """A retired point is not configuration a new relationship may be built on."""
    _, control, status = await create_pair(http_client)
    await archive(http_client, f"{POINTS_URL}/{status['id']}")

    response = await relate_reported_point(http_client, control["id"], status["id"])

    assert reason(response) == (409, "invalid_reported_point", "reported_point_not_active")


@pytest.mark.parametrize(
    "same_site",
    [pytest.param(True, id="another facility"), pytest.param(False, id="another site")],
)
async def test_a_point_outside_the_facility_is_refused(
    http_client: httpx.AsyncClient,
    same_site: bool,
) -> None:
    """A fan is never reported by a point in a different growbox.

    Both halves of the rule are covered: another facility inside the same site,
    and another site entirely. In the cross-site case the outsider carries the
    *identical* code and metric as the valid candidate — codes are unique per
    site, so nothing stops that — and it is still refused, because what decides
    is where the point lives and not what it is called.
    """
    facility, control, _ = await create_pair(http_client)
    site_id: str = (
        facility["site_id"]
        if same_site
        else (await create_site(http_client, code="other-site", name="Other"))["id"]
    )
    other_facility = await create_facility(
        http_client,
        site_id,
        code="other-growbox",
        name="Other Growbox",
    )
    outsider = await create_point(
        http_client,
        site_id,
        facility_id=other_facility["id"],
        **(STATUS_POINT | ({"code": "other_fan_running"} if same_site else {})),
    )

    response = await relate_reported_point(http_client, control["id"], outsider["id"])

    assert reason(response) == (409, "invalid_reported_point", "reported_point_not_in_facility")


async def test_postgresql_refuses_a_relationship_the_service_never_writes(
    connection: AsyncConnection,
    http_client: httpx.AsyncClient,
) -> None:
    """The half of the rule that fits in one row is held by the schema too.

    Only a control point may carry the relationship, and no point reports
    itself. Both are checked against the constraint directly, because a future
    write path that skipped the service would still have to get past them.
    """
    site = await create_site(http_client)
    facility = await create_facility(http_client, site["id"])
    measurement = await create_point(http_client, site["id"], facility_id=facility["id"])
    control = await create_point(
        http_client,
        site["id"],
        facility_id=facility["id"],
        **CONTROL_POINT,
    )

    for point_id, related_id in (
        (measurement["id"], control["id"]),
        (control["id"], control["id"]),
    ):
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    text("UPDATE points SET reported_point_id = :related WHERE id = :point"),
                    {"related": related_id, "point": point_id},
                )


# --- Publication ----------------------------------------------------------


async def test_the_configuration_document_publishes_the_relationship(
    http_client: httpx.AsyncClient,
) -> None:
    """A client reads the relationship from the growbox, before any command exists.

    This is the discovery step the customer-facing flow starts from: find the
    control point of a zone, and find the point that reports it back, in the one
    document that already describes the growbox.
    """
    growbox = await create_climate_growbox(http_client)
    related = await relate_reported_point(
        http_client,
        growbox.points["fan_power"]["id"],
        growbox.points["fan_running"]["id"],
    )
    assert related.status_code == 200, related.text

    response = await http_client.get(configuration_url(growbox.facility["id"]))

    assert response.status_code == 200, response.text
    published = {point["code"]: point["reported_point_id"] for point in response.json()["points"]}
    assert published["fan_power"] == growbox.points["fan_running"]["id"]
    assert published["fan_running"] is None
    assert published["air_temperature"] is None


async def test_the_zone_composition_publishes_the_relationship(
    http_client: httpx.AsyncClient,
) -> None:
    """A client that reads one zone rather than the whole facility sees it too."""
    growbox = await create_climate_growbox(http_client)
    await relate_reported_point(
        http_client,
        growbox.points["fan_power"]["id"],
        growbox.points["fan_running"]["id"],
    )

    response = await http_client.get(f"/api/v1/control-zones/{growbox.control_zone['id']}/points")

    assert response.status_code == 200, response.text
    published = {item["point_code"]: item["reported_point_id"] for item in response.json()["items"]}
    assert published["fan_power"] == growbox.points["fan_running"]["id"]
    assert published["fan_running"] is None
