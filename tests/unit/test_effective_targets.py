"""Effective target resolution at the layer that owns source integrity."""

from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.control.models import ControlLoop, ControlPolicyType
from ai_greenhouse.control.targets import (
    EffectiveBoundsSource,
    EffectiveTemperatureBoundsResolver,
)
from ai_greenhouse.cultivation.models import RuntimeTarget, RuntimeTargetMetric


def loop() -> ControlLoop:
    """Build legacy immutable configuration without a database."""
    return ControlLoop(
        id=uuid4(),
        control_zone_id=uuid4(),
        measurement_point_id=uuid4(),
        control_point_id=uuid4(),
        status_point_id=uuid4(),
        policy_type=ControlPolicyType.HYSTERESIS_V1,
        lower_threshold=Decimal("20"),
        upper_threshold=Decimal("30"),
    )


def target(**overrides: object) -> RuntimeTarget:
    """Build an active temperature snapshot without a database."""
    values: dict[str, object] = {
        "id": uuid4(),
        "control_loop_id": uuid4(),
        "grow_cycle_id": uuid4(),
        "target_requirement_id": uuid4(),
        "metric_type": RuntimeTargetMetric.AIR_TEMPERATURE,
        "lower_value": Decimal("22"),
        "upper_value": Decimal("26"),
        "unit": "°C",
    }
    return RuntimeTarget(**(values | overrides))


def test_a_valid_runtime_target_keeps_bounds_and_provenance_together() -> None:
    snapshot = target()

    resolved = EffectiveTemperatureBoundsResolver._from_runtime_target(snapshot)

    assert resolved.lower == Decimal("22")
    assert resolved.upper == Decimal("26")
    assert resolved.source is EffectiveBoundsSource.RUNTIME_TARGET
    assert resolved.runtime_target_id == snapshot.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"metric_type": "air_humidity"},
        {"unit": "%"},
        {"lower_value": Decimal("NaN")},
        {"upper_value": Decimal("Infinity")},
        {"lower_value": Decimal("26"), "upper_value": Decimal("26")},
        {"lower_value": Decimal("27"), "upper_value": Decimal("26")},
    ],
)
def test_an_invalid_active_target_is_not_treated_as_absent(overrides: dict[str, object]) -> None:
    """Corrupt target data fails resolution instead of executing legacy bounds."""
    with pytest.raises(RuntimeError, match="violates temperature invariants"):
        EffectiveTemperatureBoundsResolver._from_runtime_target(target(**overrides))


async def test_no_active_target_returns_legacy_bounds_with_null_provenance() -> None:
    """The compatibility source is explicit rather than inferred downstream."""

    class NoTargets:
        async def get_active_for_evaluation(self, control_loop_id: object) -> None:
            return None

    resolver = EffectiveTemperatureBoundsResolver(cast(AsyncSession, object()))
    resolver._targets = cast(object, NoTargets())  # type: ignore[assignment]

    resolved = await resolver.resolve(loop())

    assert resolved.lower == Decimal("20")
    assert resolved.upper == Decimal("30")
    assert resolved.source is EffectiveBoundsSource.CONTROL_LOOP
    assert resolved.runtime_target_id is None
