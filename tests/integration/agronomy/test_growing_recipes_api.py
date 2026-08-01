"""What a published recipe version is, and everything it refuses to be.

A recipe graph is created in one request and never changed afterwards, so the
rules that matter are the ones that decide whether it is created at all. Each is
asserted by its refusal, and every refusal also asserts that the four tables are
still empty: a rejected requirement that left a recipe and a version behind
would be a worse failure than the one it reported.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.agronomy.models import RequirementKind, TargetRequirement
from ai_greenhouse.agronomy.schemas import GrowingRecipeCreate
from ai_greenhouse.agronomy.service import GrowingRecipeService
from tests.integration.factories import (
    CROPS_URL,
    GROWING_RECIPES_URL,
    RECIPE_VERSIONS_URL,
    count_rows,
    create_crop,
    create_recipe,
    recipe_body,
    requirement_bodies,
)

CATALOG_TABLES: tuple[str, ...] = (
    "growing_recipes",
    "recipe_versions",
    "recipe_stages",
    "target_requirements",
)
"""The tables a recipe graph lands in, deepest last."""

TEMPERATURE: str = "air_temperature"
HUMIDITY: str = "air_humidity"
PHOTOPERIOD: str = "photoperiod"


async def catalog_counts(connection: AsyncConnection) -> dict[str, int]:
    """Count every row of the recipe graph.

    Args:
        connection: The connection the test transaction runs on.

    Returns:
        The row count of each catalog table, keyed by table name.
    """
    return {table: await count_rows(connection, table) for table in CATALOG_TABLES}


def stage_body(**overrides: Any) -> dict[str, Any]:
    """Build a version body whose stage carries the given overrides.

    Args:
        **overrides: Fields replacing the defaults of the stage.

    Returns:
        The ``version`` fragment of a recipe creation body.
    """
    return {
        "version_number": 1,
        "stage": {
            "code": "vegetative",
            "name": "Vegetative",
            "requirements": requirement_bodies(),
        }
        | overrides,
    }


async def test_the_representative_graph_is_published_and_read_back_whole(
    http_client: httpx.AsyncClient,
) -> None:
    """Create once, then read the same complete graph from both read endpoints."""
    crop = await create_crop(http_client)

    created = await create_recipe(http_client, crop["id"])
    fetched = await http_client.get(f"{GROWING_RECIPES_URL}/{created['id']}")
    version = await http_client.get(f"{RECIPE_VERSIONS_URL}/{created['version']['id']}")
    listed = await http_client.get(GROWING_RECIPES_URL)

    assert created["crop_id"] == crop["id"]
    assert created["code"] == "basil-default"
    assert created["status"] == "active"
    assert created["version"]["version_number"] == 1
    assert created["version"]["status"] == "published"
    assert created["version"]["published_at"] is not None
    assert created["version"]["stage"]["code"] == "vegetative"
    assert created["version"]["stage"]["sequence_number"] == 1
    assert fetched.json() == created
    assert version.json() == created["version"]
    assert listed.json()["items"] == [created]


async def test_the_published_version_carries_exactly_the_three_requirements(
    http_client: httpx.AsyncClient,
) -> None:
    """The numbers a consumer reads, in a deterministic order, as JSON numbers."""
    crop = await create_crop(http_client)

    created = await create_recipe(http_client, crop["id"])
    requirements = created["version"]["stage"]["requirements"]

    assert [
        (
            requirement["metric_type"],
            requirement["requirement_kind"],
            requirement["unit"],
            requirement["min_value"],
            requirement["max_value"],
            requirement["target_value"],
        )
        for requirement in requirements
    ] == [
        (HUMIDITY, "range", "%", 55, 70, None),
        (TEMPERATURE, "range", "°C", 22, 26, None),
        (PHOTOPERIOD, "duration_per_day", "h/day", None, None, 16),
    ]
    assert all(isinstance(requirement["min_value"], float | None) for requirement in requirements)


async def test_recipes_are_filtered_by_code_and_by_crop_and_page_deterministically(
    http_client: httpx.AsyncClient,
) -> None:
    """Three recipes over two crops, sliced three ways."""
    basil = await create_crop(http_client)
    mint = await create_crop(http_client, code="mint", display_name="Mint", scientific_name=None)
    first = await create_recipe(http_client, basil["id"])
    second = await create_recipe(http_client, basil["id"], code="basil-intense")
    third = await create_recipe(http_client, mint["id"], code="mint-default")

    by_code = await http_client.get(GROWING_RECIPES_URL, params={"code": "basil-intense"})
    by_crop = await http_client.get(GROWING_RECIPES_URL, params={"crop_id": mint["id"]})
    page_one = await http_client.get(GROWING_RECIPES_URL, params={"limit": 2, "offset": 0})
    page_two = await http_client.get(GROWING_RECIPES_URL, params={"limit": 2, "offset": 2})

    assert by_code.json()["items"] == [second]
    assert by_crop.json() == {"items": [third], "total": 1, "limit": 50, "offset": 0}
    assert page_one.json()["items"] == [first, second]
    assert page_two.json()["items"] == [third]
    assert page_one.json()["total"] == page_two.json()["total"] == 3


async def test_a_duplicate_recipe_code_leaves_the_first_graph_alone(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """The refusal must not write half of a second graph beside the first."""
    crop = await create_crop(http_client)
    await create_recipe(http_client, crop["id"])

    duplicate = await http_client.post(
        GROWING_RECIPES_URL,
        json=recipe_body(crop["id"], name="Another basil recipe"),
    )

    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "recipe_code_exists"
    assert duplicate.json()["error"]["details"] == {"code": "basil-default"}
    assert await catalog_counts(connection) == {
        "growing_recipes": 1,
        "recipe_versions": 1,
        "recipe_stages": 1,
        "target_requirements": 3,
    }


async def test_a_recipe_for_an_unknown_crop_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """A crop named by the body is as missing as one named by the path."""
    response = await http_client.post(GROWING_RECIPES_URL, json=recipe_body(str(uuid4())))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "crop_not_found"
    assert await catalog_counts(connection) == dict.fromkeys(CATALOG_TABLES, 0)


async def test_an_unknown_recipe_and_version_are_reported_as_missing(
    http_client: httpx.AsyncClient,
) -> None:
    recipe = await http_client.get(f"{GROWING_RECIPES_URL}/{uuid4()}")
    version = await http_client.get(f"{RECIPE_VERSIONS_URL}/{uuid4()}")

    assert recipe.status_code == 404, recipe.text
    assert recipe.json()["error"]["code"] == "recipe_not_found"
    assert version.status_code == 404, version.text
    assert version.json()["error"]["code"] == "recipe_version_not_found"


@pytest.mark.parametrize(
    ("description", "overrides"),
    [
        ("unknown recipe field", {"status": "active"}),
        ("unknown version field", {"version": stage_body() | {"status": "published"}}),
        ("unknown stage field", {"version": stage_body(duration_days=21)}),
        (
            "unknown requirement field",
            {"version": stage_body(requirements=[{**requirement_bodies()[0], "point_id": "x"}])},
        ),
        ("missing stage", {"version": {"version_number": 1}}),
        (
            "a collection of stages",
            {"version": {"version_number": 1, "stages": [stage_body()["stage"]]}},
        ),
    ],
)
async def test_a_body_the_contract_does_not_describe_is_refused(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    description: str,
    overrides: dict[str, Any],
) -> None:
    """Unknown keys are refused rather than dropped, at every level of the body."""
    crop = await create_crop(http_client)

    response = await http_client.post(
        GROWING_RECIPES_URL,
        json=recipe_body(crop["id"], **overrides),
    )

    assert response.status_code == 422, f"{description}: {response.text}"
    assert response.json()["error"]["code"] == "validation_error"
    assert await catalog_counts(connection) == dict.fromkeys(CATALOG_TABLES, 0)


@pytest.mark.parametrize(
    ("reason", "overrides"),
    [
        ("unsupported_version_number", {"version": stage_body() | {"version_number": 2}}),
        ("unsupported_version_number", {"version": stage_body() | {"version_number": 0}}),
        ("unsupported_stage_code", {"version": stage_body(code="flowering")}),
        ("unsupported_stage_sequence_number", {"version": stage_body(sequence_number=2)}),
        (
            "duplicate_metric_type",
            {"version": stage_body(requirements=[*requirement_bodies(), requirement_bodies()[0]])},
        ),
        (
            "unsupported_metric_type",
            {
                "version": stage_body(
                    requirements=[
                        *requirement_bodies(),
                        {
                            "metric_type": "co2",
                            "requirement_kind": "range",
                            "min_value": 400,
                            "max_value": 1200,
                            "unit": "ppm",
                        },
                    ]
                )
            },
        ),
        (
            "missing_metric_type",
            {"version": stage_body(requirements=requirement_bodies(photoperiod={}))},
        ),
        ("missing_metric_type", {"version": stage_body(requirements=[])}),
        (
            "unsupported_requirement_kind",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_temperature={
                            "metric_type": TEMPERATURE,
                            "requirement_kind": "duration_per_day",
                            "target_value": 24,
                            "unit": "°C",
                        }
                    )
                )
            },
        ),
        (
            "unsupported_unit",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_temperature={
                            "metric_type": TEMPERATURE,
                            "requirement_kind": "range",
                            "min_value": 72,
                            "max_value": 79,
                            "unit": "°F",
                        }
                    )
                )
            },
        ),
        (
            "invalid_range",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_temperature={
                            "metric_type": TEMPERATURE,
                            "requirement_kind": "range",
                            "min_value": 26,
                            "max_value": 22,
                            "unit": "°C",
                        }
                    )
                )
            },
        ),
        (
            "invalid_range",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_temperature={
                            "metric_type": TEMPERATURE,
                            "requirement_kind": "range",
                            "min_value": 24,
                            "max_value": 24,
                            "unit": "°C",
                        }
                    )
                )
            },
        ),
        (
            "missing_range_bounds",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_humidity={
                            "metric_type": HUMIDITY,
                            "requirement_kind": "range",
                            "min_value": 55,
                            "unit": "%",
                        }
                    )
                )
            },
        ),
        (
            "target_value_not_allowed",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        air_humidity={
                            "metric_type": HUMIDITY,
                            "requirement_kind": "range",
                            "min_value": 55,
                            "max_value": 70,
                            "target_value": 60,
                            "unit": "%",
                        }
                    )
                )
            },
        ),
        (
            "range_bounds_not_allowed",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        photoperiod={
                            "metric_type": PHOTOPERIOD,
                            "requirement_kind": "duration_per_day",
                            "target_value": 16,
                            "min_value": 14,
                            "max_value": 18,
                            "unit": "h/day",
                        }
                    )
                )
            },
        ),
        (
            "missing_target_value",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        photoperiod={
                            "metric_type": PHOTOPERIOD,
                            "requirement_kind": "duration_per_day",
                            "unit": "h/day",
                        }
                    )
                )
            },
        ),
        (
            "invalid_duration",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        photoperiod={
                            "metric_type": PHOTOPERIOD,
                            "requirement_kind": "duration_per_day",
                            "target_value": 0,
                            "unit": "h/day",
                        }
                    )
                )
            },
        ),
        (
            "invalid_duration",
            {
                "version": stage_body(
                    requirements=requirement_bodies(
                        photoperiod={
                            "metric_type": PHOTOPERIOD,
                            "requirement_kind": "duration_per_day",
                            "target_value": 25,
                            "unit": "h/day",
                        }
                    )
                )
            },
        ),
    ],
)
async def test_an_unpublishable_version_is_refused_before_anything_is_written(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    reason: str,
    overrides: dict[str, Any],
) -> None:
    """Every structural rule, by its refusal, and by what it leaves behind: nothing."""
    crop = await create_crop(http_client)

    response = await http_client.post(
        GROWING_RECIPES_URL,
        json=recipe_body(crop["id"], **overrides),
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_recipe_version"
    assert response.json()["error"]["details"]["reason"] == reason
    assert await catalog_counts(connection) == dict.fromkeys(CATALOG_TABLES, 0)


async def test_a_failure_after_the_graph_is_staged_rolls_all_of_it_back(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    """The aggregate is one transaction, and the request boundary owns it.

    The service is called the way a handler calls it — it flushes and does not
    commit — and then the transaction is rolled back the way ``get_session``
    rolls it back when the handler raises. What must not survive that is *any*
    part of the graph.
    """
    crop = await create_crop(http_client)
    service = GrowingRecipeService(session)

    await service.create_recipe(GrowingRecipeCreate(**recipe_body(crop["id"])))
    staged = await catalog_counts(connection)
    await session.rollback()

    assert staged == {
        "growing_recipes": 1,
        "recipe_versions": 1,
        "recipe_stages": 1,
        "target_requirements": 3,
    }
    assert await catalog_counts(connection) == dict.fromkeys(CATALOG_TABLES, 0)


async def test_postgres_refuses_a_second_requirement_for_one_metric(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """One requirement per metric is a constraint, not only a service rule."""
    crop = await create_crop(http_client)
    created = await create_recipe(http_client, crop["id"])
    stage_id = created["version"]["stage"]["id"]

    session.add(
        TargetRequirement(
            recipe_stage_id=stage_id,
            metric_type=TEMPERATURE,
            requirement_kind=RequirementKind.RANGE,
            unit="°C",
            min_value=18,
            max_value=20,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_postgres_refuses_values_that_do_not_match_the_requirement_kind(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    """A range with a target, written straight to the table, is still refused."""
    crop = await create_crop(http_client)
    created = await create_recipe(http_client, crop["id"])
    stage_id = created["version"]["stage"]["id"]

    session.add(
        TargetRequirement(
            recipe_stage_id=stage_id,
            metric_type="co2",
            requirement_kind=RequirementKind.RANGE,
            unit="ppm",
            min_value=400,
            max_value=1200,
            target_value=800,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.parametrize("method", ["patch", "put", "delete"])
async def test_nothing_a_version_carries_can_be_changed_or_removed(
    http_client: httpx.AsyncClient,
    method: str,
    connection: AsyncConnection,
) -> None:
    """The catalog offers no way to edit what has been published, at any level."""
    crop = await create_crop(http_client)
    created = await create_recipe(http_client, crop["id"])
    urls: tuple[str, ...] = (
        f"{CROPS_URL}/{crop['id']}",
        GROWING_RECIPES_URL,
        f"{GROWING_RECIPES_URL}/{created['id']}",
        f"{RECIPE_VERSIONS_URL}/{created['version']['id']}",
    )

    for url in urls:
        response = await http_client.request(method, url, json={})
        assert response.status_code == 405, f"{method.upper()} {url}: {response.text}"

    assert await catalog_counts(connection) == {
        "growing_recipes": 1,
        "recipe_versions": 1,
        "recipe_stages": 1,
        "target_requirements": 3,
    }
