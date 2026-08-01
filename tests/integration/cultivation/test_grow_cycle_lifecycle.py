"""What a grow cycle's four states allow, and what each transition writes.

Every transition is asserted by what it leaves in the database, not only by the
representation it returns: the guarantee is that the cycle, its stage instance
and its runtime target move together, and a response alone cannot show that.

Activation is also asserted by what it does *not* do. No command is written and
no telemetry is read. The target is consumed only when a later accepted current
temperature reaches the existing automation path.

Three refusals here are reached by editing a row directly rather than through an
endpoint. That is deliberate and it is the point of them: the catalog offers no
way to archive a published crop or recipe, and the control module refuses to
configure a loop measuring the wrong thing, so the only way to observe that
activation re-checks those facts is to break them underneath it.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import (
    CONTROL_LOOPS_URL,
    CONTROL_ZONES_URL,
    CROPS_URL,
    FACILITIES_URL,
    GROW_CYCLES_URL,
    GROWING_RECIPES_URL,
    RUNTIME_TARGETS_URL,
    CycleEnvironment,
    activate_grow_cycle,
    archive,
    count_rows,
    create_control_zone,
    create_cycle_environment,
    create_grow_cycle,
    create_point,
)


@pytest.fixture
async def environment(http_client: httpx.AsyncClient) -> CycleEnvironment:
    """Provision the topology, automation and catalog every lifecycle test uses.

    Args:
        http_client: The client under test.

    Returns:
        The wired growbox, its control loop, the crop and the recipe.
    """
    return await create_cycle_environment(http_client)


@pytest.fixture
async def planned(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> dict[str, Any]:
    """Plan one cycle in that environment.

    Args:
        http_client: The client under test.
        environment: The facility, zone and version the cycle joins.

    Returns:
        The created planned cycle.
    """
    return await create_grow_cycle(http_client, environment)


async def stage_instance_times(connection: AsyncConnection) -> list[tuple[Any, ...]]:
    """Read the timestamps of every stage instance inside the test transaction.

    Args:
        connection: The connection the test transaction runs on.

    Returns:
        One ``(started_at, ended_at)`` tuple per row, oldest first.
    """
    result = await connection.execute(
        text("SELECT started_at, ended_at FROM grow_stage_instances ORDER BY started_at, id")
    )
    return [tuple(row) for row in result]


def instant(value: str) -> datetime:
    """Parse an instant out of a JSON response for comparison with a stored one.

    Args:
        value: The serialised timestamp.

    Returns:
        The timezone-aware instant it denotes.
    """
    return datetime.fromisoformat(value)


async def test_activation_opens_a_stage_and_materializes_one_band(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """The band comes from the recipe requirement, not from anything the caller sent."""
    requirement = environment.temperature_requirement

    activated = await activate_grow_cycle(http_client, planned["id"])
    target = activated["active_runtime_target"]

    assert activated["status"] == "active"
    assert activated["started_at"] is not None
    assert activated["ended_at"] is None
    assert target["control_loop_id"] == environment.control_loop["id"]
    assert target["grow_cycle_id"] == planned["id"]
    assert target["target_requirement_id"] == requirement["id"]
    assert target["metric_type"] == "air_temperature"
    assert target["lower_value"] == requirement["min_value"]
    assert target["upper_value"] == requirement["max_value"]
    assert target["unit"] == "°C"
    assert target["effective_to"] is None
    assert await count_rows(connection, "grow_stage_instances") == 1
    assert await count_rows(connection, "runtime_targets") == 1


async def test_the_cycle_its_stage_and_its_band_share_one_start(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """When this cycle started has one answer whichever of the three rows is read."""
    activated = await activate_grow_cycle(http_client, planned["id"])

    started_at = instant(activated["started_at"])
    stage_started_at, _ = (await stage_instance_times(connection))[0]

    assert stage_started_at == started_at
    assert instant(activated["active_runtime_target"]["effective_from"]) == started_at


async def test_activation_writes_no_command_and_reads_no_telemetry(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """A target states what a zone should be; acting on it is not part of this unit."""
    await activate_grow_cycle(http_client, planned["id"])

    assert await count_rows(connection, "commands") == 0
    assert await count_rows(connection, "telemetry_samples") == 0


async def test_the_control_loop_is_left_exactly_as_it_was_configured(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
) -> None:
    """Activation adds a target without copying its bounds back into the loop."""
    await activate_grow_cycle(http_client, planned["id"])

    loop = await http_client.get(f"{CONTROL_LOOPS_URL}/{environment.control_loop['id']}")

    assert loop.json() == environment.control_loop


async def test_repeating_activation_changes_nothing(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """A retried request answers with the running cycle rather than refusing it."""
    first = await activate_grow_cycle(http_client, planned["id"])

    second = await activate_grow_cycle(http_client, planned["id"])

    assert second == first
    assert await count_rows(connection, "grow_stage_instances") == 1
    assert await count_rows(connection, "runtime_targets") == 1


async def test_completion_closes_the_cycle_its_stage_and_its_band_together(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    activated = await activate_grow_cycle(http_client, planned["id"])
    target_id = activated["active_runtime_target"]["id"]

    completed = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")
    body = completed.json()
    _, stage_ended_at = (await stage_instance_times(connection))[0]
    closed = await http_client.get(f"{RUNTIME_TARGETS_URL}/{target_id}")

    assert completed.status_code == 200, completed.text
    assert body["status"] == "completed"
    assert body["started_at"] == activated["started_at"]
    assert body["active_runtime_target"] is None
    assert stage_ended_at == instant(body["ended_at"])
    assert instant(closed.json()["effective_to"]) == instant(body["ended_at"])


async def test_repeating_completion_preserves_the_timestamps(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
) -> None:
    await activate_grow_cycle(http_client, planned["id"])
    first = (await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")).json()

    second = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")

    assert second.status_code == 200, second.text
    assert second.json() == first


async def test_aborting_an_active_cycle_closes_everything_it_opened(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    activated = await activate_grow_cycle(http_client, planned["id"])
    target_id = activated["active_runtime_target"]["id"]

    aborted = (await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/abort")).json()
    _, stage_ended_at = (await stage_instance_times(connection))[0]
    closed = (await http_client.get(f"{RUNTIME_TARGETS_URL}/{target_id}")).json()

    assert aborted["status"] == "aborted"
    assert aborted["active_runtime_target"] is None
    assert stage_ended_at == instant(aborted["ended_at"])
    assert instant(closed["effective_to"]) == instant(aborted["ended_at"])


async def test_aborting_a_planned_cycle_opens_nothing_to_close(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """A cycle that never ran gets no stage in its history and no band in its past."""
    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/abort")
    body = response.json()

    assert response.status_code == 200, response.text
    assert body["status"] == "aborted"
    assert body["started_at"] is None
    assert body["ended_at"] is not None
    assert await count_rows(connection, "grow_stage_instances") == 0
    assert await count_rows(connection, "runtime_targets") == 0


async def test_repeating_abort_preserves_the_timestamps(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
) -> None:
    first = (await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/abort")).json()

    second = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/abort")

    assert second.status_code == 200, second.text
    assert second.json() == first


@pytest.mark.parametrize(
    ("first_transition", "second_transition"),
    [("abort", "complete"), ("complete", "abort")],
)
async def test_the_opposite_terminal_transition_is_refused(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    first_transition: str,
    second_transition: str,
) -> None:
    """A finished cycle has one ending, and it is the one it already has."""
    await activate_grow_cycle(http_client, planned["id"])
    await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/{first_transition}")

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/{second_transition}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_grow_cycle_transition"


async def test_completing_a_planned_cycle_is_refused(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """Completion is what ends an activation; a cycle that never ran cannot finish."""
    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_grow_cycle_transition"
    assert response.json()["error"]["details"]["status"] == "planned"
    assert await count_rows(connection, "grow_stage_instances") == 0


@pytest.mark.parametrize("terminal", ["complete", "abort"])
async def test_a_terminal_cycle_cannot_be_reactivated(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
    terminal: str,
) -> None:
    await activate_grow_cycle(http_client, planned["id"])
    await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/{terminal}")

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/activate")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_grow_cycle_transition"
    assert await count_rows(connection, "grow_stage_instances") == 1
    assert await count_rows(connection, "runtime_targets") == 1


@pytest.mark.parametrize("transition", ["activate", "complete", "abort"])
async def test_a_transition_on_an_unknown_cycle_is_reported_as_missing(
    http_client: httpx.AsyncClient,
    transition: str,
) -> None:
    response = await http_client.post(f"{GROW_CYCLES_URL}/{uuid4()}/{transition}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "grow_cycle_not_found"


async def test_a_zone_without_a_control_loop_cannot_be_activated(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
) -> None:
    """The loop is resolved at activation, from the topology as it stands then."""
    unwired = await create_control_zone(
        http_client,
        environment.facility_id,
        code="second-climate",
        name="Second Climate",
    )
    cycle = await create_grow_cycle(
        http_client,
        environment,
        code="loopless-cycle",
        climate_zone_id=unwired["id"],
    )

    response = await http_client.post(f"{GROW_CYCLES_URL}/{cycle['id']}/activate")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "grow_cycle_loop_unavailable"
    assert response.json()["error"]["details"]["reason"] == "no_control_loop"
    assert await count_rows(connection, "runtime_targets") == 0


async def test_a_cycle_planned_before_its_loop_existed_still_activates(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
) -> None:
    """Creation reserves nothing, so a loop configured later is the one resolved."""
    late = await create_control_zone(
        http_client,
        environment.facility_id,
        code="late-climate",
        name="Late Climate",
    )
    cycle = await create_grow_cycle(
        http_client,
        environment,
        code="late-loop-cycle",
        climate_zone_id=late["id"],
    )
    for role, point_code in (
        ("primary_measurement", "air_temperature"),
        ("control_output", "fan_power"),
        ("status_feedback", "fan_running"),
    ):
        assignment = await http_client.post(
            f"{CONTROL_ZONES_URL}/{late['id']}/points",
            json={"point_id": environment.growbox.points[point_code]["id"], "role": role},
        )
        assert assignment.status_code == 201, assignment.text
    loop = await http_client.post(
        CONTROL_LOOPS_URL,
        json={
            "control_zone_id": late["id"],
            "measurement_point_id": environment.growbox.points["air_temperature"]["id"],
            "control_point_id": environment.growbox.points["fan_power"]["id"],
            "status_point_id": environment.growbox.points["fan_running"]["id"],
            "lower_threshold": 24.0,
            "upper_threshold": 26.0,
        },
    )
    assert loop.status_code == 201, loop.text

    activated = await activate_grow_cycle(http_client, cycle["id"])

    assert activated["active_runtime_target"]["control_loop_id"] == loop.json()["id"]
    assert await count_rows(connection, "runtime_targets") == 1


async def test_postgres_refuses_a_second_loop_for_one_zone(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """Ambiguity is unreachable because a zone may hold only one loop.

    The refusal that makes "exactly one compatible loop" resolvable lives in the
    control module's unique constraint, so it is asserted against that rather
    than simulated. What the resolution does when handed two candidates anyway
    is asserted in ``tests/unit/test_grow_cycle_resolution.py``.
    """
    second = await http_client.post(
        CONTROL_LOOPS_URL,
        json={
            "control_zone_id": environment.climate_zone_id,
            "measurement_point_id": environment.growbox.points["air_temperature"]["id"],
            "control_point_id": environment.growbox.points["fan_power"]["id"],
            "status_point_id": environment.growbox.points["fan_running"]["id"],
            "lower_threshold": 20.0,
            "upper_threshold": 22.0,
        },
    )

    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "control_loop_exists"


@pytest.mark.parametrize(
    ("metric_type", "unit", "reason"),
    [
        ("air_humidity", "%", "measurement_metric_mismatch"),
        ("air_temperature", "°F", "measurement_unit_mismatch"),
    ],
)
async def test_a_loop_measuring_the_wrong_thing_cannot_carry_a_band(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
    metric_type: str,
    unit: str,
    reason: str,
) -> None:
    """Nothing converts a metric or a unit, so the loop's own point has to match.

    The control module refuses to configure such a loop, so the row is edited
    underneath the cycle. Activation checking again is exactly what this proves.
    """
    odd = await create_point(
        http_client,
        environment.growbox.site["id"],
        facility_id=environment.facility_id,
        code="odd_measurement",
        name="Odd Measurement",
        point_kind="measurement",
        metric_type=metric_type,
        data_type="float",
        unit=unit,
    )
    await connection.execute(
        text("UPDATE control_loops SET measurement_point_id = :point_id WHERE id = :loop_id"),
        {"point_id": odd["id"], "loop_id": environment.control_loop["id"]},
    )

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/activate")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "grow_cycle_loop_unavailable"
    assert response.json()["error"]["details"]["reason"] == reason
    assert await count_rows(connection, "runtime_targets") == 0


@pytest.mark.parametrize("resource", ["facility", "control_zone"])
async def test_an_archived_growbox_stops_a_cycle_from_starting(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
    resource: str,
) -> None:
    """A cycle may be planned beside a resource that is retired afterwards.

    What it may not do is start growing in one, which is why the topology is
    read again at activation rather than trusted from creation.
    """
    urls = {
        "facility": f"{FACILITIES_URL}/{environment.facility_id}",
        "control_zone": f"{CONTROL_ZONES_URL}/{environment.climate_zone_id}",
    }
    await archive(http_client, urls[resource])

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/activate")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "parent_archived"
    assert response.json()["error"]["details"]["resource"] == resource
    assert await count_rows(connection, "runtime_targets") == 0


@pytest.mark.parametrize(
    ("table", "resource"),
    [("crops", "crop"), ("growing_recipes", "growing_recipe")],
)
async def test_an_archived_catalog_entry_stops_a_cycle_from_starting(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
    table: str,
    resource: str,
) -> None:
    """The catalog exposes no archive endpoint, so the status is set underneath.

    Activation reads the crop and the recipe again for exactly this case: a
    retired crop must not be grown, and only the database can put one in that
    state today.
    """
    identifier = {
        "crops": environment.crop["id"],
        "growing_recipes": environment.recipe["id"],
    }[table]
    await connection.execute(
        text(f"UPDATE {table} SET status = 'archived' WHERE id = :identifier"),
        {"identifier": identifier},
    )

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/activate")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "parent_archived"
    assert response.json()["error"]["details"]["resource"] == resource
    assert await count_rows(connection, "runtime_targets") == 0


async def test_a_second_cycle_cannot_take_over_a_driven_loop(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """One loop is grown against one band, so the rival is refused whole."""
    await activate_grow_cycle(http_client, planned["id"])
    rival = await create_grow_cycle(http_client, environment, code="rival-cycle", name="Rival")

    response = await http_client.post(f"{GROW_CYCLES_URL}/{rival['id']}/activate")
    rival_after = await http_client.get(f"{GROW_CYCLES_URL}/{rival['id']}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "grow_cycle_target_conflict"
    assert rival_after.json()["status"] == "planned"
    assert rival_after.json()["started_at"] is None
    assert await count_rows(connection, "grow_stage_instances") == 1
    assert await count_rows(connection, "runtime_targets") == 1


async def test_a_finished_cycle_frees_its_loop_for_the_next_one(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """Closing ``effective_to`` is what takes the row out of the partial index."""
    await activate_grow_cycle(http_client, planned["id"])
    await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")
    successor = await create_grow_cycle(http_client, environment, code="next-cycle", name="Next")

    activated = await activate_grow_cycle(http_client, successor["id"])

    assert activated["active_runtime_target"]["control_loop_id"] == environment.control_loop["id"]
    assert await count_rows(connection, "runtime_targets") == 2


async def test_a_broken_stage_instance_refuses_completion_without_writing(
    http_client: httpx.AsyncClient,
    planned: dict[str, Any],
    connection: AsyncConnection,
) -> None:
    """A cycle whose children were removed underneath it is reported, not crashed.

    The row is deleted directly because the API has no way to produce this
    state. What matters is that the refusal leaves the cycle, its status and its
    still-active band exactly as they were.
    """
    await activate_grow_cycle(http_client, planned["id"])
    await connection.execute(
        text("DELETE FROM grow_stage_instances WHERE grow_cycle_id = :cycle_id"),
        {"cycle_id": planned["id"]},
    )

    response = await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")
    after = await http_client.get(f"{GROW_CYCLES_URL}/{planned['id']}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "grow_stage_instance_not_found"
    assert after.json()["status"] == "active"
    assert after.json()["active_runtime_target"]["effective_to"] is None
    assert await count_rows(connection, "runtime_targets") == 1


async def test_the_catalog_and_the_crop_are_untouched_by_a_cycle(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    planned: dict[str, Any],
) -> None:
    """A cycle references the catalog; running one never edits it."""
    await activate_grow_cycle(http_client, planned["id"])
    await http_client.post(f"{GROW_CYCLES_URL}/{planned['id']}/complete")

    crop = await http_client.get(f"{CROPS_URL}/{environment.crop['id']}")
    recipe = await http_client.get(f"{GROWING_RECIPES_URL}/{environment.recipe['id']}")

    assert crop.json() == environment.crop
    assert recipe.json() == environment.recipe
