"""The ``hysteresis-v1`` decision, at the only layer that can get it wrong.

Everything the policy does is decided by four numbers, so it is checked here
and asserted through the database exactly once — in the end-to-end scenario
that has to be true anyway.
"""

from decimal import Decimal

import pytest

from ai_greenhouse.control.policy import FanAction, evaluate_hysteresis

LOWER: Decimal = Decimal("24")
UPPER: Decimal = Decimal("26")


def decide(temperature: Decimal, *, fan_is_on: bool) -> FanAction | None:
    """Evaluate one temperature against the documented demo band.

    Args:
        temperature: The measured value in ``°C``.
        fan_is_on: Effective state of the control point.

    Returns:
        The decision the policy reaches.
    """
    return evaluate_hysteresis(
        temperature=temperature,
        lower_threshold=LOWER,
        upper_threshold=UPPER,
        fan_is_on=fan_is_on,
    )


@pytest.mark.parametrize(
    ("temperature", "fan_is_on", "expected"),
    [
        pytest.param(Decimal("27"), False, FanAction.TURN_ON, id="above with fan off"),
        pytest.param(Decimal("27"), True, None, id="above with fan already on"),
        pytest.param(Decimal("23"), True, FanAction.TURN_OFF, id="below with fan on"),
        pytest.param(Decimal("23"), False, None, id="below with fan already off"),
        pytest.param(Decimal("25"), True, None, id="inside keeps fan on"),
        pytest.param(Decimal("25"), False, None, id="inside keeps fan off"),
    ],
)
def test_only_leaving_the_band_changes_the_fan(
    temperature: Decimal,
    fan_is_on: bool,
    expected: FanAction | None,
) -> None:
    """The band is what the fan keeps its state inside; leaving it is what switches."""
    assert decide(temperature, fan_is_on=fan_is_on) is expected


@pytest.mark.parametrize(
    ("temperature", "fan_is_on"),
    [
        pytest.param(UPPER, False, id="exactly the upper end does not switch on"),
        pytest.param(LOWER, True, id="exactly the lower end does not switch off"),
    ],
)
def test_a_temperature_resting_on_a_threshold_changes_nothing(
    temperature: float,
    fan_is_on: bool,
) -> None:
    """Both comparisons are strict, so a threshold belongs to the band it bounds."""
    assert decide(temperature, fan_is_on=fan_is_on) is None


def test_a_fan_that_was_never_written_is_treated_as_off() -> None:
    """A first hot measurement has to switch a fan that has no recorded state."""
    assert decide(Decimal("27"), fan_is_on=False) is FanAction.TURN_ON


def test_decimal_boundaries_are_compared_without_float_rounding() -> None:
    """A value exactly equal to a precise decimal boundary remains a no-op."""
    boundary = Decimal("22.0000000000000000000000000001")

    assert (
        evaluate_hysteresis(
            temperature=boundary,
            lower_threshold=Decimal("20"),
            upper_threshold=boundary,
            fan_is_on=False,
        )
        is None
    )
