"""What the runtime-target read model offers, and what it refuses to offer.

The list is the same bounded newest-first window the command history uses, so
the window itself is not re-asserted here. What is asserted is what belongs to a
target: that its band comes back as JSON numbers, that active and closed history
are separable, that the order is deterministic, that there is no way to write
one, and that PostgreSQL allows only one active target per control loop whatever
the service does.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.cultivation.models import RuntimeTarget, RuntimeTargetMetric
from tests.integration.factories import (
    GROW_CYCLES_URL,
    RUNTIME_TARGETS_URL,
    CycleEnvironment,
    activate_grow_cycle,
    create_cycle_environment,
    create_grow_cycle,
)


@pytest.fixture
async def environment(http_client: httpx.AsyncClient) -> CycleEnvironment:
    """Provision the topology, automation and catalog every target test uses.

    Args:
        http_client: The client under test.

    Returns:
        The wired growbox, its control loop, the crop and the recipe.
    """
    return await create_cycle_environment(http_client)


async def run_one_cycle(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    code: str,
    *,
    finish: bool,
) -> dict[str, Any]:
    """Plan, activate and optionally complete one cycle.

    Args:
        http_client: The client under test.
        environment: The facility, zone and version the cycle joins.
        code: Code of the cycle to plan.
        finish: Whether to complete it, which closes its target.

    Returns:
        The runtime target the activation materialized.
    """
    cycle = await create_grow_cycle(http_client, environment, code=code, name=code)
    activated = await activate_grow_cycle(http_client, cycle["id"])
    if finish:
        completed = await http_client.post(f"{GROW_CYCLES_URL}/{cycle['id']}/complete")
        assert completed.status_code == 200, completed.text
    return activated["active_runtime_target"]


async def test_one_target_is_read_back_with_its_band_as_json_numbers(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    requirement = environment.temperature_requirement
    target = await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=False)

    fetched = await http_client.get(f"{RUNTIME_TARGETS_URL}/{target['id']}")
    body = fetched.json()

    assert fetched.status_code == 200, fetched.text
    assert body == target
    assert body["lower_value"] == requirement["min_value"] == 22.0
    assert body["upper_value"] == requirement["max_value"] == 26.0
    assert isinstance(body["lower_value"], float)
    assert isinstance(body["upper_value"], float)


async def test_an_unknown_target_is_reported_as_missing(http_client: httpx.AsyncClient) -> None:
    response = await http_client.get(f"{RUNTIME_TARGETS_URL}/{uuid4()}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "runtime_target_not_found"


async def test_targets_come_back_newest_first(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """History reads backwards: the band in effect now is the first row."""
    older = await run_one_cycle(http_client, environment, "first-cycle", finish=True)
    newer = await run_one_cycle(http_client, environment, "second-cycle", finish=False)

    listed = await http_client.get(RUNTIME_TARGETS_URL)

    assert [item["id"] for item in listed.json()["items"]] == [newer["id"], older["id"]]


async def test_active_and_closed_history_are_separable(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    closed = await run_one_cycle(http_client, environment, "first-cycle", finish=True)
    open_target = await run_one_cycle(http_client, environment, "second-cycle", finish=False)

    active = await http_client.get(RUNTIME_TARGETS_URL, params={"active": "true"})
    historical = await http_client.get(RUNTIME_TARGETS_URL, params={"active": "false"})

    assert [item["id"] for item in active.json()["items"]] == [open_target["id"]]
    assert [item["id"] for item in historical.json()["items"]] == [closed["id"]]
    assert historical.json()["items"][0]["effective_to"] is not None


async def test_targets_are_filtered_by_control_loop(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    here = await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=False)
    elsewhere = await create_cycle_environment(
        http_client,
        site_code="annex",
        crop_code="dill",
        recipe_code="dill-default",
    )
    await run_one_cycle(http_client, elsewhere, "dill-cycle", finish=False)

    listed = await http_client.get(
        RUNTIME_TARGETS_URL,
        params={"control_loop_id": environment.control_loop["id"]},
    )

    assert [item["id"] for item in listed.json()["items"]] == [here["id"]]


async def test_an_unknown_loop_filter_answers_an_empty_list(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=False)

    listed = await http_client.get(RUNTIME_TARGETS_URL, params={"control_loop_id": str(uuid4())})

    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": []}


@pytest.mark.parametrize("limit", [0, 1001])
async def test_a_window_outside_the_bounded_limit_is_refused(
    http_client: httpx.AsyncClient,
    limit: int,
) -> None:
    response = await http_client.get(RUNTIME_TARGETS_URL, params={"limit": limit})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


async def test_there_is_no_way_to_write_a_target(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
) -> None:
    """A target is what activating a cycle produced, never what a client asked for."""
    target = await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=False)
    url = f"{RUNTIME_TARGETS_URL}/{target['id']}"

    created = await http_client.post(RUNTIME_TARGETS_URL, json={})
    patched = await http_client.patch(url, json={"upper_value": 30})
    replaced = await http_client.put(url, json={"upper_value": 30})
    deleted = await http_client.delete(url)

    assert created.status_code == 405, created.text
    assert patched.status_code == 405, patched.text
    assert replaced.status_code == 405, replaced.text
    assert deleted.status_code == 405, deleted.text


async def test_postgres_allows_one_active_target_per_control_loop(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    session: AsyncSession,
) -> None:
    """The partial index is the authority, so it is asserted below the service.

    Two concurrent activations resolving to one loop both pass any application
    check. What stops the second is this index, and nothing else has to.
    """
    target = await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=False)

    session.add(
        RuntimeTarget(
            control_loop_id=UUID(environment.control_loop["id"]),
            grow_cycle_id=UUID(target["grow_cycle_id"]),
            target_requirement_id=UUID(target["target_requirement_id"]),
            metric_type=RuntimeTargetMetric.AIR_TEMPERATURE,
            lower_value=Decimal("18"),
            upper_value=Decimal("21"),
            unit="°C",
            effective_from=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_postgres_accepts_a_second_closed_target_for_one_loop(
    http_client: httpx.AsyncClient,
    environment: CycleEnvironment,
    session: AsyncSession,
) -> None:
    """The index is partial: history is unlimited, only the open row is unique."""
    target = await run_one_cycle(http_client, environment, "basil-demo-cycle", finish=True)

    session.add(
        RuntimeTarget(
            control_loop_id=UUID(environment.control_loop["id"]),
            grow_cycle_id=UUID(target["grow_cycle_id"]),
            target_requirement_id=UUID(target["target_requirement_id"]),
            metric_type=RuntimeTargetMetric.AIR_TEMPERATURE,
            lower_value=Decimal("18"),
            upper_value=Decimal("21"),
            unit="°C",
            effective_from=datetime(2026, 8, 1, tzinfo=UTC),
            effective_to=datetime(2026, 8, 2, tzinfo=UTC),
        )
    )
    await session.flush()
    await session.rollback()
