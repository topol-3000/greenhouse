"""Pure deterministic implementations of the demonstration climate models.

``simple-climate-v1`` is frozen: :func:`step_climate` is the formula persisted v1
runs were created under, and nothing may change what it returns.
``simple-climate-v2`` differs from it in one respect — which temperature the
model moves toward — so it is written as that one difference over v1 rather than
as a second model, and it reuses v1 for the humidity it does not change.

Neither function reads a database, a clock or a session. Neither models physics:
the cooling offset is the coefficient that makes a closed control cycle visible
in a demonstration, not a calibrated fan.
"""

from dataclasses import dataclass
from math import exp

from ai_greenhouse.simulation.schemas import ClimateV2Parameters, SimulationParameters


@dataclass(frozen=True, slots=True)
class ClimateState:
    """Temperature and relative humidity produced by one model step."""

    temperature: float
    humidity: float


def _response(response_rate: float, delta_virtual_time: float) -> float:
    """Return the bounded first-order response for a virtual-time delta."""
    if delta_virtual_time < 0:
        raise ValueError("delta_virtual_time must not be negative")
    return 1.0 - exp(-response_rate * delta_virtual_time)


def step_climate(
    current: ClimateState,
    parameters: SimulationParameters,
    delta_virtual_time: float,
) -> ClimateState:
    """Advance temperature and humidity monotonically toward ambient values."""
    temperature_response = _response(
        parameters.temperature_response_rate,
        delta_virtual_time,
    )
    humidity_response = _response(
        parameters.humidity_response_rate,
        delta_virtual_time,
    )
    temperature = (
        current.temperature
        + (parameters.ambient_temperature - current.temperature) * temperature_response
    )
    humidity = (
        current.humidity + (parameters.ambient_humidity - current.humidity) * humidity_response
    )
    return ClimateState(
        temperature=temperature,
        humidity=min(100.0, max(0.0, humidity)),
    )


def effective_target_temperature(
    parameters: ClimateV2Parameters,
    *,
    fan_is_on: bool,
) -> float:
    """Return the temperature ``simple-climate-v2`` moves toward.

    Args:
        parameters: The run's immutable v2 snapshot.
        fan_is_on: Effective state of the zone's logical ``fan_power`` point. A
            point that carries no value is off here, exactly as it is for the
            control policy.

    Returns:
        Ambient temperature while the fan is off, and ambient minus the run's
        cooling offset while it is on.
    """
    if fan_is_on:
        return parameters.ambient_temperature - parameters.fan_cooling_offset
    return parameters.ambient_temperature


def step_climate_v2(
    current: ClimateState,
    parameters: ClimateV2Parameters,
    delta_virtual_time: float,
    *,
    fan_is_on: bool,
) -> ClimateState:
    """Advance one ``simple-climate-v2`` step toward the fan's effective target.

    Humidity is taken from :func:`step_climate` rather than recomputed, so "v2
    humidity is v1 humidity" holds by construction and cannot drift into a copy
    that disagrees. The fan is not a humidity input.

    Args:
        current: Temperature and humidity the step starts from.
        parameters: The run's immutable v2 snapshot.
        delta_virtual_time: Virtual seconds this step covers.
        fan_is_on: Effective state of the zone's logical ``fan_power`` point.

    Returns:
        The state after the step.

    Raises:
        ValueError: If ``delta_virtual_time`` is negative.
    """
    unfanned = step_climate(current, parameters, delta_virtual_time)
    temperature_response = _response(
        parameters.temperature_response_rate,
        delta_virtual_time,
    )
    target = effective_target_temperature(parameters, fan_is_on=fan_is_on)
    return ClimateState(
        temperature=current.temperature + (target - current.temperature) * temperature_response,
        humidity=unfanned.humidity,
    )
