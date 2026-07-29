"""Focused schema and pure-model confidence for ``simple-climate-v1``."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.simulation.climate import ClimateState, step_climate
from ai_greenhouse.simulation.schemas import SimulationRunCreate
from ai_greenhouse.simulation.service import simulation_sample_id


def run_payload(**overrides: object) -> dict[str, object]:
    """Build a valid simulation request for schema tests."""
    return {
        "control_zone_id": uuid4(),
        "speed_multiplier": 60,
        "initial_temperature": 20.0,
        "initial_humidity": 65.0,
        "ambient_temperature": 30.0,
        "ambient_humidity": 45.0,
    } | overrides


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speed_multiplier", 0),
        ("speed_multiplier", 3601),
        ("initial_humidity", -0.1),
        ("ambient_humidity", 100.1),
        ("initial_temperature", float("inf")),
    ],
)
def test_request_boundaries_are_refused(field: str, value: object) -> None:
    """Boundary validation belongs to the request schema."""
    with pytest.raises(ValidationError):
        SimulationRunCreate.model_validate(run_payload(**{field: value}))


def test_request_cannot_choose_model_version_or_snapshot_constants() -> None:
    """The model version owns its response constants, not the caller."""
    with pytest.raises(ValidationError):
        SimulationRunCreate.model_validate(
            run_payload(model_version="future", temperature_response_rate=1.0)
        )


def test_identical_climate_inputs_produce_identical_results() -> None:
    payload = SimulationRunCreate.model_validate(run_payload())
    state = ClimateState(temperature=20.0, humidity=65.0)

    first = step_climate(state, payload.parameter_snapshot(), 60.0)
    second = step_climate(state, payload.parameter_snapshot(), 60.0)

    assert first == second


def test_repeated_steps_converge_monotonically_without_overshoot() -> None:
    parameters = SimulationRunCreate.model_validate(run_payload()).parameter_snapshot()
    states = [ClimateState(temperature=20.0, humidity=65.0)]

    for _ in range(120):
        states.append(step_climate(states[-1], parameters, 60.0))

    temperatures = [state.temperature for state in states]
    humidities = [state.humidity for state in states]
    assert temperatures == sorted(temperatures)
    assert humidities == sorted(humidities, reverse=True)
    assert all(temperature <= parameters.ambient_temperature for temperature in temperatures)
    assert all(parameters.ambient_humidity <= humidity <= 100 for humidity in humidities)
    assert temperatures[-1] > temperatures[0]
    assert humidities[-1] < humidities[0]


def test_humidity_is_always_clamped_to_physical_bounds() -> None:
    parameters = SimulationRunCreate.model_validate(
        run_payload(initial_humidity=100.0, ambient_humidity=0.0)
    ).parameter_snapshot()

    result = step_climate(ClimateState(temperature=20.0, humidity=150.0), parameters, 3600.0)

    assert 0 <= result.humidity <= 100


def test_negative_virtual_delta_is_refused() -> None:
    parameters = SimulationRunCreate.model_validate(run_payload()).parameter_snapshot()

    with pytest.raises(ValueError):
        step_climate(ClimateState(temperature=20.0, humidity=65.0), parameters, -1.0)


def test_simulation_sample_id_is_stable_and_scoped_to_the_step_and_point() -> None:
    run_id = UUID("11111111-1111-1111-1111-111111111111")
    point_id = UUID("22222222-2222-2222-2222-222222222222")

    first = simulation_sample_id(run_id, 7, point_id)

    assert first == simulation_sample_id(run_id, 7, point_id)
    assert first != simulation_sample_id(run_id, 8, point_id)
    assert first != simulation_sample_id(run_id, 7, uuid4())
