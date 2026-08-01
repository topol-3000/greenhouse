"""What the agronomy schemas accept, and what they hand on unvalidated.

The value tables themselves are asserted in ``test_types.py``. What is asserted
here is the wiring: which fields are required, which bounds they carry, that no
creation body accepts an unknown key, and that a decimal reaches the service as
a ``Decimal`` and leaves the API as a JSON number.

Everything a submitted *graph* can get wrong — the version number, the stage,
the set of requirements and the combinations inside them — is a domain rule and
is asserted once, through the endpoint, in
``tests/integration/agronomy``.
"""

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.agronomy.models import (
    MAX_CROP_CODE_LENGTH,
    MAX_RECIPE_CODE_LENGTH,
    MAX_STAGE_CODE_LENGTH,
)
from ai_greenhouse.agronomy.schemas import (
    CropCreate,
    GrowingRecipeCreate,
    RecipeStageCreate,
    RecipeVersionCreate,
    TargetRequirementCreate,
)


def requirement(**overrides: Any) -> dict[str, Any]:
    """Build a valid requirement body.

    Args:
        **overrides: Fields replacing the defaults.

    Returns:
        The requirement body.
    """
    return {
        "metric_type": "air_temperature",
        "requirement_kind": "range",
        "min_value": 22,
        "max_value": 26,
        "unit": "°C",
    } | overrides


def recipe(**overrides: Any) -> dict[str, Any]:
    """Build a valid recipe creation body.

    Args:
        **overrides: Fields replacing the defaults at the top level.

    Returns:
        The recipe body.
    """
    return {
        "crop_id": str(uuid4()),
        "code": "basil-default",
        "name": "Default basil recipe",
        "version": {
            "version_number": 1,
            "stage": {
                "code": "vegetative",
                "name": "Vegetative",
                "requirements": [requirement()],
            },
        },
    } | overrides


def test_a_crop_needs_only_a_code_and_a_display_name() -> None:
    """``scientific_name`` is the one optional field, and defaults to ``None``."""
    crop = CropCreate(code="basil", display_name="Basil")

    assert crop.scientific_name is None


def test_a_crop_name_is_stripped() -> None:
    assert CropCreate(code="basil", display_name="  Basil  ").display_name == "Basil"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "Basil"),
        ("code", ""),
        ("code", "a" * (MAX_CROP_CODE_LENGTH + 1)),
        ("display_name", "   "),
        ("display_name", "a" * 121),
        ("scientific_name", "a" * 161),
    ],
)
def test_a_crop_field_outside_its_bounds_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        CropCreate(**{"code": "basil", "display_name": "Basil", field: value})


def test_a_crop_code_may_reach_its_own_longer_bound() -> None:
    """A crop code is longer than the shared 63-character slug, and still a slug."""
    code = "a" * MAX_CROP_CODE_LENGTH

    assert CropCreate(code=code, display_name="Basil").code == code


def test_a_recipe_code_may_reach_its_own_longer_bound() -> None:
    code = "a" * MAX_RECIPE_CODE_LENGTH

    assert GrowingRecipeCreate(**recipe(code=code)).code == code


@pytest.mark.parametrize("value", ["a" * (MAX_RECIPE_CODE_LENGTH + 1), "Basil", "basil default"])
def test_a_recipe_code_that_is_not_a_slug_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        GrowingRecipeCreate(**recipe(code=value))


@pytest.mark.parametrize("value", ["a" * (MAX_STAGE_CODE_LENGTH + 1), "Vegetative", ""])
def test_a_stage_code_that_is_not_a_slug_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        RecipeStageCreate(code=value, name="Vegetative", requirements=[])


def test_a_version_and_a_stage_number_default_to_one() -> None:
    """The representative body omits both, so both have to mean position one."""
    version = RecipeVersionCreate(
        stage=RecipeStageCreate(code="vegetative", name="Vegetative", requirements=[])
    )

    assert version.version_number == 1
    assert version.stage.sequence_number == 1


@pytest.mark.parametrize(
    "body",
    [
        {"code": "basil", "display_name": "Basil", "status": "active"},
        {"code": "basil", "display_name": "Basil", "id": str(uuid4())},
    ],
)
def test_a_crop_body_refuses_an_unknown_field(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CropCreate(**body)


def test_a_recipe_body_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        GrowingRecipeCreate(**recipe(status="active"))


def test_a_version_body_refuses_an_unknown_field() -> None:
    """``status`` and ``published_at`` are the catalog's, not a client's."""
    with pytest.raises(ValidationError):
        RecipeVersionCreate(
            status="published",
            stage=RecipeStageCreate(code="vegetative", name="Vegetative", requirements=[]),
        )


def test_a_stage_body_refuses_an_unknown_field() -> None:
    """A stage carries no duration: submitting one has to fail, not be dropped."""
    with pytest.raises(ValidationError):
        RecipeStageCreate(
            code="vegetative",
            name="Vegetative",
            duration_days=21,
            requirements=[],
        )


def test_a_requirement_body_refuses_an_unknown_field() -> None:
    """A requirement never names a point, a zone or a device."""
    with pytest.raises(ValidationError):
        TargetRequirementCreate(**requirement(point_id=str(uuid4())))


def test_a_requirement_holds_its_values_as_decimals() -> None:
    """A ``numeric`` column has to receive what the client sent, not a float."""
    submitted = TargetRequirementCreate(**requirement(min_value=22.5))

    assert submitted.min_value == Decimal("22.5")
    assert isinstance(submitted.min_value, Decimal)


@pytest.mark.parametrize("field", ["min_value", "max_value", "target_value"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_requirement_refuses_a_non_finite_value(field: str, value: float) -> None:
    """``Infinity`` and ``NaN`` survive a ``numeric`` column; they stop here."""
    with pytest.raises(ValidationError):
        TargetRequirementCreate(**requirement(**{field: value}))


def test_requirement_values_serialise_as_json_numbers() -> None:
    """A client reads ``22.0``, not ``"22.0"``."""
    submitted = TargetRequirementCreate(**requirement())

    rendered = json.loads(submitted.model_dump_json())

    assert rendered["min_value"] == 22
    assert rendered["max_value"] == 26
    assert rendered["target_value"] is None
