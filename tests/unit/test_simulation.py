"""Focused schema and pure-model confidence for both climate model versions.

``simple-climate-v1`` is frozen, so its tests state expected numbers rather than
re-deriving the formula: a test that recomputes what the code computes cannot
notice the formula changing. ``simple-climate-v2`` is tested for the one thing it
adds — which target the temperature moves toward — and for the humidity it
leaves alone.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.simulation.climate import (
    ClimateState,
    effective_target_temperature,
    step_climate,
    step_climate_v2,
)
from ai_greenhouse.simulation.models import MODEL_VERSION_V2
from ai_greenhouse.simulation.schemas import (
    FAN_COOLING_OFFSET,
    HUMIDITY_RESPONSE_RATE,
    TEMPERATURE_RESPONSE_RATE,
    ClimateV2Parameters,
    SimulationParameters,
    SimulationRunCreate,
    SimulationRunRead,
)
from ai_greenhouse.simulation.service import simulation_sample_id

V1_SNAPSHOT: dict[str, float] = {
    "initial_temperature": 20.0,
    "initial_humidity": 65.0,
    "ambient_temperature": 30.0,
    "ambient_humidity": 45.0,
    "temperature_response_rate": TEMPERATURE_RESPONSE_RATE,
    "humidity_response_rate": HUMIDITY_RESPONSE_RATE,
}
"""The six values a persisted ``simple-climate-v1`` run stores, and only those."""

DEMO_AMBIENT_TEMPERATURE: float = 30.0
DEMO_INITIAL_TEMPERATURE: float = 22.0
DEMO_SPEED_MULTIPLIER: float = 600.0
DEMO_UPPER_THRESHOLD: float = 26.0
DEMO_LOWER_THRESHOLD: float = 24.0
"""The documented demonstration defaults the closed cycle has to be reachable at."""


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


def v1_parameters(**overrides: float) -> SimulationParameters:
    """Build a frozen ``simple-climate-v1`` snapshot."""
    return SimulationParameters.model_validate(V1_SNAPSHOT | overrides)


def v2_parameters(**overrides: float) -> ClimateV2Parameters:
    """Build a ``simple-climate-v2`` snapshot with the version's own offset."""
    return ClimateV2Parameters.model_validate(
        V1_SNAPSHOT | {"fan_cooling_offset": FAN_COOLING_OFFSET} | overrides
    )


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

    with pytest.raises(ValidationError):
        SimulationRunCreate.model_validate(run_payload(fan_cooling_offset=1.0))


def test_new_runs_snapshot_the_current_version_including_the_cooling_offset() -> None:
    """A request supplies conditions; the version supplies its coefficients."""
    snapshot = SimulationRunCreate.model_validate(run_payload()).parameter_snapshot()

    assert isinstance(snapshot, ClimateV2Parameters)
    assert snapshot.temperature_response_rate == TEMPERATURE_RESPONSE_RATE
    assert snapshot.humidity_response_rate == HUMIDITY_RESPONSE_RATE
    assert snapshot.fan_cooling_offset == FAN_COOLING_OFFSET


def test_the_two_snapshot_shapes_do_not_validate_as_each_other() -> None:
    """One persisted column serves both versions because neither shape overlaps."""
    v2_snapshot = v2_parameters().model_dump(mode="json")

    with pytest.raises(ValidationError):
        ClimateV2Parameters.model_validate(V1_SNAPSHOT)
    with pytest.raises(ValidationError):
        SimulationParameters.model_validate(v2_snapshot)


@pytest.mark.parametrize(
    ("model_version", "snapshot", "expected_offset"),
    [
        ("simple-climate-v1", V1_SNAPSHOT, None),
        (MODEL_VERSION_V2, V1_SNAPSHOT | {"fan_cooling_offset": 8.0}, 8.0),
    ],
)
def test_read_representation_returns_the_snapshot_of_either_version(
    model_version: str,
    snapshot: dict[str, float],
    expected_offset: float | None,
) -> None:
    """A v1 run stays readable after M4, with the fields it actually stored."""
    body: dict[str, Any] = {
        "id": uuid4(),
        "control_zone_id": uuid4(),
        "status": "created",
        "model_version": model_version,
        "speed_multiplier": 60,
        "parameters": snapshot,
        "virtual_time": None,
        "step_index": 0,
        "started_at": None,
        "stopped_at": None,
        "failure_reason": None,
        "created_at": "2026-07-30T09:00:00Z",
        "updated_at": "2026-07-30T09:00:00Z",
    }

    run = SimulationRunRead.model_validate(body)

    assert run.model_version == model_version
    assert getattr(run.parameters, "fan_cooling_offset", None) == expected_offset


def test_frozen_v1_step_returns_its_documented_values() -> None:
    """Pinned outputs: a change to the v1 formula has to fail here."""
    result = step_climate(ClimateState(temperature=20.0, humidity=65.0), v1_parameters(), 60.0)

    assert result.temperature == pytest.approx(20.165285461783824, rel=1e-12)
    assert result.humidity == pytest.approx(64.34432200964012, rel=1e-12)


def test_identical_climate_inputs_produce_identical_results() -> None:
    state = ClimateState(temperature=20.0, humidity=65.0)

    first = step_climate(state, v1_parameters(), 60.0)
    second = step_climate(state, v1_parameters(), 60.0)

    assert first == second


def test_repeated_steps_converge_monotonically_without_overshoot() -> None:
    parameters = v1_parameters()
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
    parameters = v1_parameters(initial_humidity=100.0, ambient_humidity=0.0)

    result = step_climate(ClimateState(temperature=20.0, humidity=150.0), parameters, 3600.0)

    assert 0 <= result.humidity <= 100


def test_negative_virtual_delta_is_refused() -> None:
    with pytest.raises(ValueError):
        step_climate(ClimateState(temperature=20.0, humidity=65.0), v1_parameters(), -1.0)

    with pytest.raises(ValueError):
        step_climate_v2(
            ClimateState(temperature=20.0, humidity=65.0),
            v2_parameters(),
            -1.0,
            fan_is_on=True,
        )


@pytest.mark.parametrize("fan_is_on", [False, True])
def test_v2_moves_toward_the_target_its_fan_state_selects(fan_is_on: bool) -> None:
    """OFF aims at ambient; ON aims at ambient minus the run's offset."""
    parameters = v2_parameters()
    start = ClimateState(temperature=27.0, humidity=65.0)
    expected_target = 30.0 - (FAN_COOLING_OFFSET if fan_is_on else 0.0)

    target = effective_target_temperature(parameters, fan_is_on=fan_is_on)
    stepped = step_climate_v2(start, parameters, 600.0, fan_is_on=fan_is_on)

    assert target == expected_target
    assert stepped.temperature == pytest.approx(
        start.temperature + (expected_target - start.temperature) * 0.15351827510938587,
        rel=1e-12,
    )
    assert (stepped.temperature > start.temperature) is not fan_is_on


def test_v2_with_the_fan_off_reproduces_the_v1_temperature() -> None:
    """No fan state means no difference: the offset only applies while ON."""
    start = ClimateState(temperature=22.0, humidity=65.0)

    unfanned = step_climate_v2(start, v2_parameters(), 600.0, fan_is_on=False)

    assert unfanned == step_climate(start, v1_parameters(), 600.0)


@pytest.mark.parametrize("fan_is_on", [False, True])
def test_v2_humidity_is_the_v1_response_and_ignores_the_fan(fan_is_on: bool) -> None:
    """The fan is a temperature input only; it never reaches humidity."""
    start = ClimateState(temperature=27.0, humidity=65.0)

    stepped = step_climate_v2(start, v2_parameters(), 600.0, fan_is_on=fan_is_on)

    assert stepped.humidity == step_climate(start, v1_parameters(), 600.0).humidity


def test_demo_defaults_cross_the_upper_then_the_lower_threshold() -> None:
    """The documented coefficients have to make the closed cycle reachable."""
    parameters = v2_parameters(
        initial_temperature=DEMO_INITIAL_TEMPERATURE,
        ambient_temperature=DEMO_AMBIENT_TEMPERATURE,
    )
    state = ClimateState(temperature=DEMO_INITIAL_TEMPERATURE, humidity=65.0)

    warming_steps = 0
    while state.temperature <= DEMO_UPPER_THRESHOLD and warming_steps < 100:
        state = step_climate_v2(state, parameters, DEMO_SPEED_MULTIPLIER, fan_is_on=False)
        warming_steps += 1

    cooling_steps = 0
    while state.temperature >= DEMO_LOWER_THRESHOLD and cooling_steps < 100:
        state = step_climate_v2(state, parameters, DEMO_SPEED_MULTIPLIER, fan_is_on=True)
        cooling_steps += 1

    assert warming_steps == 5
    assert cooling_steps == 5
    assert state.temperature < DEMO_LOWER_THRESHOLD


def test_simulation_sample_id_is_stable_and_scoped_to_the_step_and_point() -> None:
    run_id = UUID("11111111-1111-1111-1111-111111111111")
    point_id = UUID("22222222-2222-2222-2222-222222222222")

    first = simulation_sample_id(run_id, 7, point_id)

    assert first == simulation_sample_id(run_id, 7, point_id)
    assert first != simulation_sample_id(run_id, 8, point_id)
    assert first != simulation_sample_id(run_id, 7, uuid4())
