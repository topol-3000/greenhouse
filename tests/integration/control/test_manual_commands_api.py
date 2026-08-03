"""The public boundary a customer-facing client switches one actuator through.

Everything here is asserted through ``POST /api/v1/commands`` and the reads
around it, because that is the whole of what a browser is allowed to call. The
Edge half of a manual command — polling, acknowledgement, the terminal outcomes —
is asserted in ``tests/integration/test_manual_command_delivery.py``, which is
where a gateway exists to answer.

What this module owns is the refusals, the idempotency contract and the
separation the unit exists to keep: a desired value is a request, and the point
that reports the actuator back is a different thing entirely.
"""

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    COMMANDS_URL,
    IDEMPOTENCY_HEADER,
    POINTS_URL,
    CommandableGrowbox,
    archive,
    assign,
    count_rows,
    create_assignment,
    create_commandable_growbox,
    create_point,
    manual_command_body,
    provision_gateway,
    relate_reported_point,
    submit_manual_command,
)


@pytest.fixture
async def growbox(http_client: httpx.AsyncClient) -> CommandableGrowbox:
    """Build the growbox whose fan can be commanded.

    Args:
        http_client: The client the topology is provisioned through.

    Returns:
        The provisioned growbox, its points and its gateway.
    """
    return await create_commandable_growbox(http_client)


def refusal(response: httpx.Response) -> tuple[int, str, str]:
    """Reduce a refusal to the three things a client branches on.

    Args:
        response: The refused response.

    Returns:
        The status, the ``error.code`` and the machine-readable reason.

    """
    body: dict[str, Any] = response.json()
    return response.status_code, body["error"]["code"], body["error"]["details"].get("reason", "")


# --- The accepted request -------------------------------------------------


async def test_one_valid_request_creates_one_manual_command(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
) -> None:
    """The whole of what the unit adds, in the shape a client reads it.

    Source, zone and target identity are explicit; the automatic-only
    relationships are null rather than invented; the command is pending because
    accepting a request is not applying it.
    """
    key = str(uuid4())

    response = await submit_manual_command(http_client, growbox, idempotency_key=key)

    assert response.status_code == 201, response.text
    body = response.json()
    command = body["command"]
    assert body["outcome"] == "created"
    assert command["source"] == "manual"
    assert command["control_loop_id"] is None
    assert command["trigger_sample_id"] is None
    assert command["control_zone_id"] == growbox.control_zone["id"]
    assert command["target_point_id"] == growbox.points["fan_power"]["id"]
    assert command["reported_point_id"] == growbox.points["fan_running"]["id"]
    assert command["desired_value"] is True
    assert command["state"] == "pending"
    assert command["idempotency_key"] == key
    assert command["acknowledged_at"] is None
    assert command["executed_at"] is None
    assert command["rejection_reason"] is None
    assert await count_rows(connection, "commands") == 1


async def test_creating_a_command_writes_no_telemetry_and_no_point_state(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
) -> None:
    """A desired value is a request, and requests do not measure anything.

    This is the separation the whole unit rests on: asking the fan to switch on
    leaves both the control point and the status point exactly as unmeasured as
    they were, and only telemetry can change that.
    """
    fan_power_id = growbox.points["fan_power"]["id"]
    fan_running_id = growbox.points["fan_running"]["id"]

    response = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    control_state = await http_client.get(f"{POINTS_URL}/{fan_power_id}/state")
    status_state = await http_client.get(f"{POINTS_URL}/{fan_running_id}/state")

    assert response.status_code == 201, response.text
    assert response.json()["command"]["desired_value"] is True
    for state in (control_state, status_state):
        assert state.json()["value"] is None
        assert state.json()["quality"] == "no_data"
        assert state.json()["revision"] == 0
    assert await count_rows(connection, "telemetry_samples") == 0


async def test_both_boolean_values_are_accepted(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """``false`` is a command, not the absence of one: switching off is an action."""
    switch_off = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        desired_value=False,
    )

    assert switch_off.status_code == 201, switch_off.text
    assert switch_off.json()["command"]["desired_value"] is False


# --- Eligibility ----------------------------------------------------------


async def test_an_unknown_zone_or_point_is_reported_missing(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """A stale identifier is a 404, wherever in the request it was written."""
    unknown_zone = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        control_zone_id=str(uuid4()),
    )
    unknown_point = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        target_point_id=str(uuid4()),
    )

    assert unknown_zone.status_code == 404, unknown_zone.text
    assert unknown_zone.json()["error"]["code"] == "control_zone_not_found"
    assert unknown_point.status_code == 404, unknown_point.text
    assert unknown_point.json()["error"]["code"] == "point_not_found"


@pytest.mark.parametrize(
    "point_code",
    [
        pytest.param("air_temperature", id="a measurement point"),
        pytest.param("fan_running", id="a status point"),
    ],
)
async def test_a_point_that_is_not_the_control_output_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    point_code: str,
) -> None:
    """Only the zone's control output can be commanded.

    A measurement is an input and a status point is a reading; neither becomes
    commandable by being in the same zone as something that is. Both are members
    of this zone in good standing, and both are refused at the assignment role —
    which is where the zone actually says what a point does.
    """
    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        target_point_id=growbox.points[point_code]["id"],
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "assignment_role_not_control_output",
    )


async def test_a_control_point_of_another_zone_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """Zone membership is read from the assignment, not from the facility."""
    other_zone = await http_client.post(
        "/api/v1/control-zones",
        json={
            "facility_id": growbox.facility["id"],
            "name": "Second Climate",
            "code": "second-climate",
            "zone_type": "climate",
        },
    )
    assert other_zone.status_code == 201, other_zone.text

    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        control_zone_id=other_zone.json()["id"],
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "point_not_assigned_to_zone",
    )


async def test_a_control_point_assigned_in_the_wrong_role_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """The assignment's role decides, and a control point can hold another one.

    The same fan is added to a second zone as a safety interlock. It is the same
    active boolean control point with the same reported point, and it is refused
    there — because what makes a point commandable is the part it plays in the
    zone the request names.
    """
    other_zone = await http_client.post(
        "/api/v1/control-zones",
        json={
            "facility_id": growbox.facility["id"],
            "name": "Safety",
            "code": "safety",
            "zone_type": "safety",
        },
    )
    assert other_zone.status_code == 201, other_zone.text
    zone_id = other_zone.json()["id"]
    assigned = await assign(
        http_client,
        zone_id,
        growbox.points["fan_power"]["id"],
        "safety_interlock",
    )
    assert assigned.status_code == 201, assigned.text

    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        control_zone_id=zone_id,
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "assignment_role_not_control_output",
    )


async def test_an_archived_control_point_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """A retired actuator stops being commandable the moment it is retired."""
    await archive(http_client, f"{POINTS_URL}/{growbox.points['fan_power']['id']}")

    response = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))

    assert refusal(response) == (409, "invalid_manual_command_target", "point_not_active")


async def test_a_non_boolean_control_point_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """This unit is on/off control, and the refusal says so rather than rounding.

    The point is a genuine control output of its zone with a genuine status
    point behind it. What it is not is boolean, and a boundary that accepted it
    would be promising dimming it does not implement.
    """
    growbox = await create_commandable_growbox(http_client)
    dimmer = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="light_level",
        name="Light Level",
        point_kind="control",
        metric_type="light_level",
        data_type="float",
        unit="%",
    )
    await create_assignment(
        http_client,
        growbox.control_zone["id"],
        dimmer["id"],
        "control_output",
    )

    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        target_point_id=dimmer["id"],
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "point_data_type_not_boolean",
    )


async def test_a_control_point_without_a_reported_point_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """The relationship is required, and its absence is never worked around.

    Every other rule passes here: an active boolean control point, assigned to
    the zone as its control output, with a status point sitting beside it in the
    same zone under a matching name. Nothing infers the relationship from any of
    that, so the command is refused until someone configures it.
    """
    growbox = await create_commandable_growbox(http_client)
    cleared = await http_client.patch(
        f"{POINTS_URL}/{growbox.points['fan_power']['id']}",
        json={"reported_point_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["reported_point_id"] is None

    response = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "reported_point_not_configured",
    )


async def test_an_archived_reported_point_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """A relationship valid when it was configured is re-checked when it is used."""
    await archive(http_client, f"{POINTS_URL}/{growbox.points['fan_running']['id']}")

    response = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "reported_point_not_active",
    )


async def test_a_point_no_gateway_can_reach_is_refused(
    http_client: httpx.AsyncClient,
) -> None:
    """A command nothing could ever deliver is refused instead of left pending.

    This is a configuration fact and not a liveness guess: no gateway is
    authorized for these points at all, so the command has no boundary to leave
    the cloud through. A silent gateway that simply has not polled yet is a
    different situation, and produces an ordinary pending command.
    """
    growbox = await create_commandable_growbox(http_client)
    unreachable = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="heater_power",
        name="Heater Power",
        point_kind="control",
        metric_type="heater_power",
        data_type="boolean",
        unit=None,
    )
    reported = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="heater_running",
        name="Heater Running",
        point_kind="status",
        metric_type="heater_running",
        data_type="boolean",
        unit=None,
    )
    await create_assignment(
        http_client,
        growbox.control_zone["id"],
        unreachable["id"],
        "control_output",
    )
    related = await relate_reported_point(http_client, unreachable["id"], reported["id"])
    assert related.status_code == 200, related.text

    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        target_point_id=unreachable["id"],
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "no_gateway_for_command_points",
    )


async def test_a_persuasive_name_does_not_make_a_point_commandable(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The rule this unit exists to hold, stated as the test that would catch its loss.

    Every string on this point says "fan power": its code, its name and its
    metric. It is a measurement point, it is not assigned as a control output,
    and it names no reported point. All three refusals are structural, and none
    of the strings takes part in any of them.
    """
    growbox = await create_commandable_growbox(http_client)
    impostor = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="fan_power_2",
        name="Fan Power",
        point_kind="measurement",
        metric_type="fan_power",
        data_type="boolean",
        unit=None,
    )
    await create_assignment(
        http_client,
        growbox.control_zone["id"],
        impostor["id"],
        "secondary_measurement",
    )
    await provision_gateway(
        http_client,
        growbox.site["id"],
        [impostor["id"]],
        code="impostor-gateway",
    )

    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        target_point_id=impostor["id"],
    )

    assert refusal(response) == (
        409,
        "invalid_manual_command_target",
        "assignment_role_not_control_output",
    )
    assert await count_rows(connection, "commands") == 0


async def test_a_refused_request_leaves_no_partial_command(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
) -> None:
    """Creation is atomic, so a refusal is indistinguishable from never asking.

    The key is deliberately reused afterwards. A refused request must not have
    consumed it: the client fixes the request and retries with the same key.
    """
    key = str(uuid4())

    refused = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=key,
        target_point_id=growbox.points["air_temperature"]["id"],
    )
    commands_after_refusal = await count_rows(connection, "commands")
    accepted = await submit_manual_command(http_client, growbox, idempotency_key=key)

    assert refused.status_code == 409, refused.text
    assert commands_after_refusal == 0
    assert accepted.status_code == 201, accepted.text
    assert await count_rows(connection, "commands") == 1


# --- Idempotency ----------------------------------------------------------


async def test_the_same_key_and_request_returns_the_same_command(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
) -> None:
    """A retry is answered, not obeyed a second time.

    HTTP 200 rather than 201, ``existing`` rather than ``created``, the same
    command identifier, and one row. There is no second command for the Edge to
    poll, which is what makes a lost response safe to retry.
    """
    key = str(uuid4())

    first = await submit_manual_command(http_client, growbox, idempotency_key=key)
    replay = await submit_manual_command(http_client, growbox, idempotency_key=key)

    assert first.status_code == 201, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == "existing"
    assert replay.json()["command"] == first.json()["command"]
    assert await count_rows(connection, "commands") == 1


@pytest.mark.parametrize(
    "difference",
    [
        pytest.param({"desired_value": False}, id="another desired value"),
        pytest.param({"control_zone_id": str(uuid4())}, id="another zone"),
    ],
)
async def test_the_same_key_with_a_different_request_is_a_conflict(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    connection: AsyncConnection,
    difference: dict[str, Any],
) -> None:
    """A key names one request. Reusing it for another is a client defect.

    Answering with the stored command would carry out something the caller did
    not ask for — switching a fan *on* when the second request said *off* — so
    the conflict is reported and the stored command is named in the details.
    """
    key = str(uuid4())
    first = await submit_manual_command(http_client, growbox, idempotency_key=key)
    assert first.status_code == 201, first.text

    conflicting = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=key,
        **difference,
    )

    assert conflicting.status_code == 409, conflicting.text
    body = conflicting.json()
    assert body["error"]["code"] == "idempotency_key_conflict"
    assert body["error"]["details"]["command_id"] == first.json()["command"]["id"]
    assert await count_rows(connection, "commands") == 1


async def test_a_key_that_is_not_a_uuid_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """The key's format is part of the contract, and a missing one is not guessed.

    The server never invents a replacement, so a request without a usable key is
    refused rather than quietly given one.
    """
    missing = await http_client.post(COMMANDS_URL, json=manual_command_body(growbox))
    malformed = await http_client.post(
        COMMANDS_URL,
        json=manual_command_body(growbox),
        headers={IDEMPOTENCY_HEADER: "not-a-uuid"},
    )

    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == "validation_error"
    assert malformed.status_code == 422, malformed.text


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"desired_value": "on"}, id="a string instead of a boolean"),
        pytest.param({"desired_value": 75}, id="a percentage instead of a boolean"),
        pytest.param({"payload": {"speed": 3}}, id="an unknown field"),
    ],
)
async def test_a_request_that_is_not_boolean_on_off_is_refused(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
    body: dict[str, Any],
) -> None:
    """The boundary is on/off, and it refuses everything that is not.

    A numeric level, a free-form object and a string are all 422 here, so no
    client can discover a dimming contract this backend does not implement.
    """
    response = await submit_manual_command(
        http_client,
        growbox,
        idempotency_key=str(uuid4()),
        **body,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


# --- Reading a command back -----------------------------------------------


async def test_a_lost_creation_response_is_recovered_through_the_key(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """The recovery path a client with no command identifier has to have.

    The key is the only thing it kept, and the uniqueness of the key is what
    makes the answer zero or one command rather than a list to disambiguate.
    """
    key = str(uuid4())
    created = await submit_manual_command(http_client, growbox, idempotency_key=key)
    assert created.status_code == 201, created.text

    found = await http_client.get(COMMANDS_URL, params={"idempotency_key": key})
    never_sent = await http_client.get(COMMANDS_URL, params={"idempotency_key": str(uuid4())})

    assert found.status_code == 200, found.text
    assert [command["id"] for command in found.json()["items"]] == [created.json()["command"]["id"]]
    assert never_sent.json()["items"] == []


async def test_the_filters_separate_the_two_kinds_of_command(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """Zone, target and source are exact filters, and they combine.

    Only manual commands exist here, so the automatic filter proving empty is
    the assertion: ``source`` is read from the stored discriminator and not
    guessed from a null control loop.
    """
    created = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    assert created.status_code == 201, created.text
    command_id = created.json()["command"]["id"]

    by_zone = await http_client.get(
        COMMANDS_URL,
        params={"control_zone_id": growbox.control_zone["id"]},
    )
    by_point = await http_client.get(
        COMMANDS_URL,
        params={"target_point_id": growbox.points["fan_power"]["id"]},
    )
    by_source = await http_client.get(COMMANDS_URL, params={"source": "manual"})
    automatic = await http_client.get(COMMANDS_URL, params={"source": "control_loop"})
    other_point = await http_client.get(
        COMMANDS_URL,
        params={"target_point_id": growbox.points["fan_running"]["id"]},
    )

    for response in (by_zone, by_point, by_source):
        assert [command["id"] for command in response.json()["items"]] == [command_id]
    assert automatic.json()["items"] == []
    assert other_point.json()["items"] == []


async def test_a_created_command_is_readable_by_its_own_identifier(
    http_client: httpx.AsyncClient,
    growbox: CommandableGrowbox,
) -> None:
    """The identifier the creation response returns resolves, and a stale one 404s."""
    created = await submit_manual_command(http_client, growbox, idempotency_key=str(uuid4()))
    assert created.status_code == 201, created.text
    command: dict[str, Any] = created.json()["command"]

    read = await http_client.get(f"{COMMANDS_URL}/{command['id']}")
    missing = await http_client.get(f"{COMMANDS_URL}/{UUID(int=0)}")

    assert read.status_code == 200, read.text
    assert read.json() == command
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "command_not_found"
