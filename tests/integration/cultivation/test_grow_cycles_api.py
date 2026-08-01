"""What planning a grow cycle enforces, through its own endpoints.

The pagination envelope, the slug rules and the collection window are asserted
once on sites and on the shared value types; what is asserted here is only what
belongs to a cycle: that it derives its stage rather than accepting one, that it
is placed in exactly one zone of its own facility, that planning creates nothing
operational, and that PostgreSQL refuses a duplicate code even when the service
never saw one.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.cultivation.models import GrowCycle, GrowCycleStatus
from tests.integration.factories import (
    GROW_CYCLES_URL,
    CycleEnvironment,
    count_rows,
    create_control_zone,
    create_cycle_environment,
    create_grow_cycle,
    grow_cycle_body,
)


@pytest.fixture
async def environment(http_client: httpx.AsyncClient) -> CycleEnvironment:
    """Provision the topology, automation and catalog every cycle test starts from.

    Args:
        http_client: The client under test.

    Returns:
        The wired growbox, its control loop, the crop and the recipe.
    """
    return await create_cycle_environment(http_client)


async def test_a_planned_cycle_joins_a_facility_a_zone_and_a_recipe_version(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """One cycle, three ways of reaching it, one representation."""
    created = await create_grow_cycle(http_client, environment)

    fetched = await http_client.get(f"{GROW_CYCLES_URL}/{created['id']}")
    filtered = await http_client.get(GROW_CYCLES_URL, params={"code": "basil-demo-cycle"})

    assert created["code"] == "basil-demo-cycle"
    assert created["name"] == "Basil Grow Cycle"
    assert created["status"] == "planned"
    assert created["facility_id"] == environment.facility_id
    assert created["climate_zone_id"] == environment.climate_zone_id
    assert created["recipe_version_id"] == environment.recipe_version_id
    assert created["started_at"] is None
    assert created["ended_at"] is None
    assert created["active_runtime_target"] is None
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created
    assert filtered.json() == {"items": [created], "total": 1, "limit": 50, "offset": 0}


async def test_the_current_stage_is_derived_from_the_recipe_version(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """A caller states a version; which stage that means is the catalog's answer."""
    stage = environment.recipe["version"]["stage"]

    created = await create_grow_cycle(http_client, environment)

    assert created["current_stage_id"] == stage["id"]
    assert created["current_stage"] == {
        "id": stage["id"],
        "recipe_version_id": environment.recipe_version_id,
        "code": "vegetative",
        "name": "Vegetative",
        "sequence_number": 1,
    }


async def test_planning_persists_exactly_one_climate_zone_assignment(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
) -> None:
    await create_grow_cycle(http_client, environment)

    assert await count_rows(connection, "grow_cycle_zone_assignments") == 1


async def test_planning_creates_nothing_operational(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
) -> None:
    """A cycle that may never start must not reserve a loop or open a stage."""
    await create_grow_cycle(http_client, environment)

    assert await count_rows(connection, "grow_stage_instances") == 0
    assert await count_rows(connection, "runtime_targets") == 0
    assert await count_rows(connection, "commands") == 0


async def test_a_planned_start_is_recorded_and_starts_nothing(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    created = await create_grow_cycle(
        http_client,
        environment,
        planned_start_at="2026-08-04T06:00:00Z",
    )

    assert created["planned_start_at"] == "2026-08-04T06:00:00Z"
    assert created["status"] == "planned"
    assert created["started_at"] is None


async def test_cycles_are_filtered_by_code_facility_and_status(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """Each filter narrows the collection; an unknown value is an empty page."""
    other = await create_cycle_environment(
        http_client,
        site_code="annex",
        crop_code="dill",
        recipe_code="dill-default",
    )
    here = await create_grow_cycle(http_client, environment)
    there = await create_grow_cycle(http_client, other, code="dill-cycle", name="Dill Cycle")

    by_code = await http_client.get(GROW_CYCLES_URL, params={"code": "dill-cycle"})
    by_facility = await http_client.get(
        GROW_CYCLES_URL,
        params={"facility_id": environment.facility_id},
    )
    by_status = await http_client.get(GROW_CYCLES_URL, params={"status": "planned"})
    by_absent_status = await http_client.get(GROW_CYCLES_URL, params={"status": "completed"})

    assert [item["id"] for item in by_code.json()["items"]] == [there["id"]]
    assert [item["id"] for item in by_facility.json()["items"]] == [here["id"]]
    assert [item["id"] for item in by_status.json()["items"]] == [here["id"], there["id"]]
    assert by_absent_status.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_paging_is_deterministic_and_oldest_first(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    first = await create_grow_cycle(http_client, environment, code="cycle-one")
    second = await create_grow_cycle(http_client, environment, code="cycle-two")
    third = await create_grow_cycle(http_client, environment, code="cycle-three")

    page_one = await http_client.get(GROW_CYCLES_URL, params={"limit": 2, "offset": 0})
    page_two = await http_client.get(GROW_CYCLES_URL, params={"limit": 2, "offset": 2})

    assert [item["id"] for item in page_one.json()["items"]] == [first["id"], second["id"]]
    assert [item["id"] for item in page_two.json()["items"]] == [third["id"]]
    assert page_one.json()["total"] == 3


async def test_an_invalid_status_filter_is_refused(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(GROW_CYCLES_URL, params={"status": "paused"})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_duplicate_cycle_code_is_refused(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    await create_grow_cycle(http_client, environment)

    duplicate = await http_client.post(
        GROW_CYCLES_URL,
        json=grow_cycle_body(environment, name="Second attempt"),
    )
    listed = await http_client.get(GROW_CYCLES_URL)

    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "grow_cycle_code_exists"
    assert duplicate.json()["error"]["details"] == {"code": "basil-demo-cycle"}
    assert listed.json()["total"] == 1


async def test_postgres_refuses_a_duplicate_code_the_service_never_saw(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    session: AsyncSession,
) -> None:
    """The pre-check improves the error; the unique index is what protects the row.

    Two concurrent requests both pass the pre-check and only one may insert, so
    the guarantee is asserted where it actually lives: against the constraint,
    with the service bypassed entirely.
    """
    created = await create_grow_cycle(http_client, environment)

    session.add(
        GrowCycle(
            code="basil-demo-cycle",
            name="Second attempt",
            facility_id=created["facility_id"],
            recipe_version_id=created["recipe_version_id"],
            current_stage_id=created["current_stage_id"],
            status=GrowCycleStatus.PLANNED,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.parametrize(
    ("field", "error_code"),
    [
        ("facility_id", "facility_not_found"),
        ("climate_zone_id", "control_zone_not_found"),
        ("recipe_version_id", "recipe_version_not_found"),
    ],
)
async def test_a_missing_reference_is_reported_as_that_resource(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
    field: str,
    error_code: str,
) -> None:
    response = await http_client.post(
        GROW_CYCLES_URL,
        json=grow_cycle_body(environment, **{field: str(uuid4())}),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == error_code
    assert await count_rows(connection, "grow_cycles") == 0


async def test_an_unknown_cycle_is_reported_as_missing(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(f"{GROW_CYCLES_URL}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "grow_cycle_not_found"


async def test_a_zone_of_another_facility_is_refused(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    connection: AsyncConnection,
) -> None:
    """A cycle names a facility and a zone; the two have to be the same growbox."""
    elsewhere = await create_cycle_environment(
        http_client,
        site_code="annex",
        crop_code="dill",
        recipe_code="dill-default",
    )

    response = await http_client.post(
        GROW_CYCLES_URL,
        json=grow_cycle_body(environment, climate_zone_id=elsewhere.climate_zone_id),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_grow_cycle_zone"
    assert response.json()["error"]["details"]["reason"] == "zone_not_in_facility"
    assert await count_rows(connection, "grow_cycles") == 0


async def test_a_zone_that_is_not_a_climate_zone_is_refused(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """The assignment's role is ``climate``; a lighting zone cannot carry it."""
    lighting: dict[str, Any] = await create_control_zone(
        http_client,
        environment.facility_id,
        code="main-lighting",
        name="Main Lighting",
        zone_type="lighting",
    )

    response = await http_client.post(
        GROW_CYCLES_URL,
        json=grow_cycle_body(environment, climate_zone_id=lighting["id"]),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "zone_not_climate"


async def test_the_collection_offers_no_way_to_delete_a_cycle(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """A cycle is history once it exists; every transition is an explicit action."""
    created = await create_grow_cycle(http_client, environment)

    deleted = await http_client.delete(f"{GROW_CYCLES_URL}/{created['id']}")
    patched = await http_client.patch(
        f"{GROW_CYCLES_URL}/{created['id']}",
        json={"status": "active"},
    )

    assert deleted.status_code == 405, deleted.text
    assert patched.status_code == 405, patched.text
