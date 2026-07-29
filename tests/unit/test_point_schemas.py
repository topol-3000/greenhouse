"""Schema-level rules of the point request bodies.

A point carries more of its meaning in its schema than the topology entities do:
the range is decimal, several fields are nullable rather than merely optional,
and the fields that define what the point *is* must reach the service
unvalidated so it can answer ``409 immutable_field`` rather than ``422``.

The slug pattern and the name bounds are exercised case by case in
``test_types.py``; this module asserts the wiring and the point-specific rules.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import PointDataType, PointKind
from ai_greenhouse.points.schemas import PointCreate, PointUpdate


def build_create(**overrides: object) -> PointCreate:
    """Build a valid creation body with the given fields replaced.

    Args:
        **overrides: Fields replacing the defaults.

    Returns:
        The validated creation body.
    """
    payload: dict[str, object] = {
        "site_id": uuid4(),
        "code": "air_temperature",
        "name": "Air temperature",
        "point_kind": "measurement",
        "metric_type": "air_temperature",
        "data_type": "float",
        "unit": "°C",
    } | overrides
    return PointCreate.model_validate(payload)


def test_the_point_kinds_are_the_documented_ones() -> None:
    """The set is a published contract; adding or dropping one is an API change."""
    assert {member.value for member in PointKind} == {
        "measurement",
        "control",
        "status",
        "derived",
    }


def test_the_data_types_are_the_documented_ones() -> None:
    assert {member.value for member in PointDataType} == {
        "float",
        "integer",
        "boolean",
        "string",
    }


def test_create_parses_the_enumerations() -> None:
    payload = build_create()

    assert payload.point_kind is PointKind.MEASUREMENT
    assert payload.data_type is PointDataType.FLOAT


@pytest.mark.parametrize("field", ["point_kind", "data_type"])
def test_create_rejects_an_unknown_enum_member(field: str) -> None:
    with pytest.raises(ValidationError):
        build_create(**{field: "vibes"})


def test_create_allows_a_point_without_a_facility() -> None:
    """A point may belong to the site as a whole, such as an outdoor sensor."""
    assert build_create().facility_id is None


def test_create_requires_a_site() -> None:
    with pytest.raises(ValidationError):
        PointCreate.model_validate(
            {
                "code": "air_temperature",
                "name": "Air temperature",
                "point_kind": "measurement",
                "metric_type": "air_temperature",
                "data_type": "float",
                "unit": "°C",
            }
        )


def test_create_strips_the_name() -> None:
    assert build_create(name="  Air temperature  ").name == "Air temperature"


@pytest.mark.parametrize("field", ["code", "metric_type"])
def test_create_rejects_a_non_slug_identifier(field: str) -> None:
    """``metric_type`` is a slug too — it is what groups points across sites."""
    with pytest.raises(ValidationError):
        build_create(**{field: "Not A Slug"})


def test_create_keeps_the_range_as_decimals() -> None:
    """A ``numeric`` column deserves a decimal, not a float that has already rounded."""
    payload = build_create(min_value=-20, max_value=60)

    assert payload.min_value == Decimal("-20")
    assert payload.max_value == Decimal("60")


def test_serialising_the_range_produces_json_numbers() -> None:
    payload = build_create(min_value=-20, max_value=60.5)

    dumped = payload.model_dump(mode="json")

    assert dumped["min_value"] == -20.0
    assert dumped["max_value"] == 60.5


@pytest.mark.parametrize(
    "bounds",
    [
        pytest.param({"min_value": 5, "max_value": 5}, id="equal-ends"),
        pytest.param({"min_value": 0}, id="lower-bound-alone"),
    ],
)
def test_the_range_accepts_its_boundary_cases(bounds: dict[str, int]) -> None:
    """``min_value <= max_value`` — the ends may meet, and either may stand alone."""
    assert build_create(**bounds).min_value == Decimal(bounds["min_value"])


def test_create_rejects_an_inverted_range() -> None:
    with pytest.raises(ValidationError):
        build_create(min_value=10, max_value=5)


@pytest.mark.parametrize("forbidden", ["value", "gpio"])
def test_create_rejects_a_value_or_a_physical_address(forbidden: str) -> None:
    """Neither a current value nor a hardware address is part of a point."""
    with pytest.raises(ValidationError):
        build_create(**{forbidden: "1"})


def test_update_records_only_the_submitted_fields() -> None:
    payload = PointUpdate.model_validate({"name": "Air temperature v2"})

    assert payload.model_fields_set == {"name"}
    assert payload.unit is None
    assert payload.status is None


def test_update_accepts_an_empty_body() -> None:
    assert PointUpdate.model_validate({}).model_fields_set == set()


def test_update_distinguishes_a_cleared_field_from_an_omitted_one() -> None:
    """``unit``, ``min_value`` and ``max_value`` are nullable, so null clears them."""
    cleared = PointUpdate.model_validate({"unit": None, "min_value": None})

    assert cleared.model_fields_set == {"unit", "min_value"}
    assert "max_value" not in cleared.model_fields_set


@pytest.mark.parametrize(
    "field",
    ["code", "site_id", "facility_id", "point_kind", "metric_type", "data_type"],
)
def test_update_accepts_immutable_fields_so_the_service_can_refuse_them(field: str) -> None:
    """A 422 here would hide the ``409 immutable_field`` the contract promises."""
    value: object = str(uuid4()) if field.endswith("_id") else "changed"

    payload = PointUpdate.model_validate({field: value})

    assert field in payload.model_fields_set


@pytest.mark.parametrize("field", ["point_kind", "metric_type", "data_type"])
def test_update_does_not_validate_the_immutable_enumerations(field: str) -> None:
    """A value that will be refused anyway must not be pre-empted by a 422."""
    payload = PointUpdate.model_validate({field: "vibes"})

    assert field in payload.model_fields_set


def test_update_parses_the_status() -> None:
    assert PointUpdate.model_validate({"status": "archived"}).status is StatusEnum.ARCHIVED


def test_update_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        PointUpdate.model_validate({"status": "retired"})


def test_update_rejects_a_value() -> None:
    """No request body writes a point's value."""
    with pytest.raises(ValidationError):
        PointUpdate.model_validate({"value": 21.5})


def test_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PointUpdate.model_validate({"gpio": 17})
