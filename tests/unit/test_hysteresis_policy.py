"""The ``hysteresis-v1`` decision, at the only layer that can get it wrong.

Everything the policy does is decided by four numbers, so it is checked here
and asserted through the database exactly once — in the end-to-end scenario
that has to be true anyway.
"""

import pytest

from ai_greenhouse.control.policy import FanAction, evaluate_hysteresis

LOWER: float = 24.0
UPPER: float = 26.0


def decide(temperature: float, *, fan_is_on: bool) -> FanAction | None:
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
        pytest.param(27.0, False, FanAction.TURN_ON, id="above the band with the fan off"),
        pytest.param(27.0, True, None, id="above the band with the fan already on"),
        pytest.param(23.0, True, FanAction.TURN_OFF, id="below the band with the fan on"),
        pytest.param(23.0, False, None, id="below the band with the fan already off"),
        pytest.param(25.0, True, None, id="inside the band keeps the fan on"),
        pytest.param(25.0, False, None, id="inside the band keeps the fan off"),
    ],
)
def test_only_leaving_the_band_changes_the_fan(
    temperature: float,
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
    assert decide(27.0, fan_is_on=False) is FanAction.TURN_ON
