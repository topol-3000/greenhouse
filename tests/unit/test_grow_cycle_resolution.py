"""The two choices activation makes before it writes anything.

Both are pure functions over rows the service has already loaded, so they are
asserted here rather than through the endpoint: every branch is reachable
without a database, and one of them — more than one loop for a zone — cannot be
reached *with* one, because ``uq_control_loops_control_zone_id`` refuses the
second loop. That refusal is asserted where it lives, in
``tests/integration/cultivation``; the behaviour of the rule if the constraint
were ever widened is asserted here.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ai_greenhouse.agronomy.exceptions import InvalidRecipeVersionError
from ai_greenhouse.agronomy.models import RequirementKind, TargetRequirement
from ai_greenhouse.control.models import ControlLoop, ControlPolicyType
from ai_greenhouse.cultivation.exceptions import GrowCycleLoopUnavailableError
from ai_greenhouse.cultivation.service import (
    select_compatible_loop,
    select_temperature_requirement,
    snapshot_values,
)
from ai_greenhouse.points.models import Point, PointDataType, PointKind

ZONE_ID: UUID = UUID("33333333-3333-4333-8333-333333333333")


def requirement(**overrides: object) -> TargetRequirement:
    """Build a stored temperature requirement.

    Args:
        **overrides: Column values replacing the defaults.

    Returns:
        An unattached ``TargetRequirement`` row.
    """
    values: dict[str, object] = {
        "id": uuid4(),
        "recipe_stage_id": uuid4(),
        "metric_type": "air_temperature",
        "requirement_kind": RequirementKind.RANGE,
        "unit": "°C",
        "min_value": Decimal("22"),
        "max_value": Decimal("26"),
        "target_value": None,
    } | overrides
    return TargetRequirement(**values)


def measurement_point(**overrides: object) -> Point:
    """Build a stored measurement point.

    Args:
        **overrides: Column values replacing the defaults.

    Returns:
        An unattached ``Point`` row.
    """
    values: dict[str, object] = {
        "id": uuid4(),
        "site_id": uuid4(),
        "code": "air_temperature",
        "name": "Air Temperature",
        "point_kind": PointKind.MEASUREMENT,
        "metric_type": "air_temperature",
        "data_type": PointDataType.FLOAT,
        "unit": "°C",
    } | overrides
    return Point(**values)


def loop(point: Point, **overrides: object) -> ControlLoop:
    """Build a stored control loop measuring one point.

    Args:
        point: The loop's measurement point.
        **overrides: Column values replacing the defaults.

    Returns:
        An unattached ``ControlLoop`` row.
    """
    values: dict[str, object] = {
        "id": uuid4(),
        "control_zone_id": ZONE_ID,
        "measurement_point_id": point.id,
        "control_point_id": uuid4(),
        "status_point_id": uuid4(),
        "policy_type": ControlPolicyType.HYSTERESIS_V1,
        "lower_threshold": Decimal("24"),
        "upper_threshold": Decimal("26"),
    } | overrides
    return ControlLoop(**values)


def test_the_single_hysteresis_loop_of_the_zone_is_chosen() -> None:
    point = measurement_point()
    only = loop(point)

    assert select_compatible_loop([only], {point.id: point}, ZONE_ID) is only


def test_a_zone_without_a_loop_cannot_be_activated() -> None:
    with pytest.raises(GrowCycleLoopUnavailableError) as failure:
        select_compatible_loop([], {}, ZONE_ID)

    assert failure.value.details["reason"] == "no_control_loop"


def test_more_than_one_loop_leaves_the_target_without_an_address() -> None:
    """A band drives exactly one loop, so two candidates is a refusal, not a pick."""
    point = measurement_point()

    with pytest.raises(GrowCycleLoopUnavailableError) as failure:
        select_compatible_loop([loop(point), loop(point)], {point.id: point}, ZONE_ID)

    assert failure.value.details["reason"] == "ambiguous_control_loop"


def test_a_loop_measuring_another_metric_is_refused() -> None:
    point = measurement_point(metric_type="air_humidity", unit="%")

    with pytest.raises(GrowCycleLoopUnavailableError) as failure:
        select_compatible_loop([loop(point)], {point.id: point}, ZONE_ID)

    assert failure.value.details["reason"] == "measurement_metric_mismatch"


def test_a_loop_measuring_in_another_unit_is_refused() -> None:
    """Nothing in the flow converts units, so a °F point cannot carry a °C band."""
    point = measurement_point(unit="°F")

    with pytest.raises(GrowCycleLoopUnavailableError) as failure:
        select_compatible_loop([loop(point)], {point.id: point}, ZONE_ID)

    assert failure.value.details["reason"] == "measurement_unit_mismatch"


def test_a_loop_whose_measurement_point_is_gone_is_refused() -> None:
    point = measurement_point()

    with pytest.raises(GrowCycleLoopUnavailableError) as failure:
        select_compatible_loop([loop(point)], {}, ZONE_ID)

    assert failure.value.details["reason"] == "measurement_point_missing"


def test_the_temperature_band_of_a_stage_is_chosen() -> None:
    temperature = requirement()
    humidity = requirement(metric_type="air_humidity", unit="%")

    chosen = select_temperature_requirement([humidity, temperature], temperature.recipe_stage_id)

    assert chosen is temperature
    assert snapshot_values(chosen) == (Decimal("22"), Decimal("26"))


def test_a_stage_without_a_temperature_band_cannot_be_grown() -> None:
    humidity = requirement(metric_type="air_humidity", unit="%")

    with pytest.raises(InvalidRecipeVersionError) as failure:
        select_temperature_requirement([humidity], uuid4())

    assert failure.value.details["reason"] == "missing_temperature_requirement"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"requirement_kind": RequirementKind.DURATION_PER_DAY}, "unsupported_requirement_kind"),
        ({"unit": "°F"}, "unsupported_unit"),
        ({"min_value": None}, "missing_range_bounds"),
        ({"min_value": Decimal("26"), "max_value": Decimal("22")}, "invalid_range"),
        ({"max_value": Decimal("NaN")}, "invalid_range"),
    ],
)
def test_a_band_nothing_can_be_grown_against_is_refused(
    overrides: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(InvalidRecipeVersionError) as failure:
        select_temperature_requirement([requirement(**overrides)], uuid4())

    assert failure.value.details["reason"] == reason
