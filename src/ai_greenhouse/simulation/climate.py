"""Pure deterministic implementation of ``simple-climate-v1``."""

from dataclasses import dataclass
from math import exp

from ai_greenhouse.simulation.schemas import SimulationParameters


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
