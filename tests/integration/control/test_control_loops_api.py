"""Control-loop configuration through the real endpoints.

Only the rules this module owns are asserted: what a loop may be wired to, that
a zone gets one, and that the configuration cannot be edited afterwards. The
threshold ordering is a schema rule and is covered in ``unit/``; the collection
envelope and the archive lifecycle are asserted once on sites.
"""

from typing import Any
from uuid import uuid4

import httpx

from tests.integration.factories import (
    CONTROL_LOOPS_URL,
    LOWER_THRESHOLD,
    UPPER_THRESHOLD,
    control_loop_body,
    create_assignment,
    create_automation_growbox,
    create_control_loop,
    create_point,
)


async def test_a_wired_climate_zone_can_be_given_its_one_hysteresis_loop(
    http_client: httpx.AsyncClient,
) -> None:
    """The demo growbox configuration is accepted and answers with a fixed policy."""
    growbox = await create_automation_growbox(http_client)

    loop = await create_control_loop(http_client, growbox)

    assert loop["policy_type"] == "hysteresis-v1"
    assert loop["control_zone_id"] == growbox.control_zone["id"]
    assert loop["measurement_point_id"] == growbox.points["air_temperature"]["id"]
    assert loop["control_point_id"] == growbox.points["fan_power"]["id"]
    assert loop["status_point_id"] == growbox.points["fan_running"]["id"]
    assert loop["lower_threshold"] == LOWER_THRESHOLD
    assert loop["upper_threshold"] == UPPER_THRESHOLD


async def test_a_configured_loop_is_readable_and_listed_under_its_own_zone(
    http_client: httpx.AsyncClient,
) -> None:
    """A read returns the stored loop, and the list filter keeps zones apart."""
    growbox = await create_automation_growbox(http_client)
    other = await create_automation_growbox(http_client, name="Shed", code="shed")
    loop = await create_control_loop(http_client, growbox)
    await create_control_loop(http_client, other)

    read = await http_client.get(f"{CONTROL_LOOPS_URL}/{loop['id']}")
    assert read.status_code == 200, read.text
    assert read.json() == loop

    listed = await http_client.get(
        CONTROL_LOOPS_URL,
        params={"control_zone_id": growbox.control_zone["id"]},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [loop["id"]]


async def test_an_unknown_loop_is_reported_missing(http_client: httpx.AsyncClient) -> None:
    """A stale identifier answers 404 rather than an empty representation."""
    response = await http_client.get(f"{CONTROL_LOOPS_URL}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "control_loop_not_found"


async def test_an_unknown_point_is_reported_missing(http_client: httpx.AsyncClient) -> None:
    """An identifier that resolves to nothing is missing, not incompatible."""
    growbox = await create_automation_growbox(http_client)

    response = await http_client.post(
        CONTROL_LOOPS_URL,
        json=control_loop_body(growbox, control_point_id=str(uuid4())),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "point_not_found"


async def test_a_fan_point_of_another_zone_is_refused(http_client: httpx.AsyncClient) -> None:
    """A loop drives its own zone; a point outside it resolves but does not fit."""
    growbox = await create_automation_growbox(http_client)
    other = await create_automation_growbox(http_client, name="Shed", code="shed")

    response = await http_client.post(
        CONTROL_LOOPS_URL,
        json=control_loop_body(growbox, control_point_id=other.points["fan_power"]["id"]),
    )

    assert response.status_code == 409, response.text
    error: dict[str, Any] = response.json()["error"]
    assert error["code"] == "invalid_control_loop_point"
    assert error["details"]["role"] == "control_point_id"
    assert error["details"]["reason"] == "point_not_assigned_to_zone"


async def test_a_status_point_reporting_another_actuator_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """The loop reads back the fan it drives, not whatever else feeds the zone status."""
    growbox = await create_automation_growbox(http_client)
    pump = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="pump_running",
        name="Pump Running",
        point_kind="status",
        metric_type="pump_running",
        data_type="boolean",
        unit=None,
    )
    assignment = await create_assignment(
        http_client,
        growbox.control_zone["id"],
        pump["id"],
        "status_feedback",
    )
    assert assignment["point_id"] == pump["id"]

    response = await http_client.post(
        CONTROL_LOOPS_URL,
        json=control_loop_body(growbox, status_point_id=pump["id"]),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "metric_type_mismatch"


async def test_a_zone_cannot_be_given_a_second_loop(http_client: httpx.AsyncClient) -> None:
    """One loop per zone: a second one would silently change a decided policy."""
    growbox = await create_automation_growbox(http_client)
    await create_control_loop(http_client, growbox)

    response = await http_client.post(CONTROL_LOOPS_URL, json=control_loop_body(growbox))

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "control_loop_exists"


async def test_a_non_climate_zone_cannot_host_a_loop(http_client: httpx.AsyncClient) -> None:
    """Fan automation belongs to the zone that owns the climate of the growbox."""
    growbox = await create_automation_growbox(http_client)
    lighting = await http_client.post(
        "/api/v1/control-zones",
        json={
            "facility_id": growbox.facility["id"],
            "name": "Lighting",
            "code": "lighting",
            "zone_type": "lighting",
        },
    )
    assert lighting.status_code == 201, lighting.text

    response = await http_client.post(
        CONTROL_LOOPS_URL,
        json=control_loop_body(growbox, control_zone_id=lighting.json()["id"]),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "zone_not_climate"


async def test_a_configured_loop_cannot_be_edited(http_client: httpx.AsyncClient) -> None:
    """A loop records the configuration a command was decided under, so it is fixed."""
    growbox = await create_automation_growbox(http_client)
    loop = await create_control_loop(http_client, growbox)

    response = await http_client.patch(
        f"{CONTROL_LOOPS_URL}/{loop['id']}",
        json={"upper_threshold": 30.0},
    )

    assert response.status_code == 405, response.text
