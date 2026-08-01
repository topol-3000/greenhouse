"""The ``hysteresis-v1`` decision, as a function of four numbers.

Nothing here reads a database, a clock or a session. The decision is the one
part of automation that has to be obvious on inspection and cheap to check at
every boundary, so it is kept apart from everything that persists its result.
"""

from decimal import Decimal
from enum import StrEnum


class FanAction(StrEnum):
    """The two things a decision can ask for. "Nothing" is ``None``, not a member.

    A no-op is deliberately absent from the enumeration. M3 keeps no history of
    evaluations that changed nothing, and a member for it would invite one.
    """

    TURN_ON = "on"
    TURN_OFF = "off"


def evaluate_hysteresis(
    *,
    temperature: Decimal,
    lower_threshold: Decimal,
    upper_threshold: Decimal,
    fan_is_on: bool,
) -> FanAction | None:
    """Decide what a temperature should do to a fan that is in a known state.

    The band is what stops a fan from chattering around one setpoint: the
    temperature has to leave it on the warm side to switch on, and on the cool
    side to switch off. Between the two ends nothing happens, and the state
    already reached is kept.

    Both comparisons are strict. A measurement sitting exactly on a threshold is
    the boundary of the band and not outside it, so it changes nothing — which
    is also what keeps a sensor resting on a round number from switching the fan
    on and off with its last digit.

    Args:
        temperature: The measured value, in the same unit as the thresholds.
        lower_threshold: Lower end of the band.
        upper_threshold: Upper end of the band, above the lower one.
        fan_is_on: Effective state of the control point. A point that has never
            been written is off, so a first measurement above the band switches
            it on.

    Returns:
        The action to apply, or ``None`` when the state already matches what the
        temperature calls for.
    """
    if temperature > upper_threshold and not fan_is_on:
        return FanAction.TURN_ON
    if temperature < lower_threshold and fan_is_on:
        return FanAction.TURN_OFF
    return None
