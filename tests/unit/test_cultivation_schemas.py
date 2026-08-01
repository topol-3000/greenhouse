"""What the grow cycle schemas accept, and what a client may never decide.

The value tables themselves are asserted in ``test_types.py``. What is asserted
here is the wiring: which fields are required, which bounds they carry, that the
creation body accepts no unknown key, and that a materialized band leaves the
API as a JSON number.

Everything the *lifecycle* can get wrong — an invalid transition, a zone in
another facility, a missing control loop, a loop already driven — is a domain
rule and is asserted once, through the endpoints, in
``tests/integration/cultivation``.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.cultivation.models import (
    MAX_GROW_CYCLE_CODE_LENGTH,
    MAX_GROW_CYCLE_NAME_LENGTH,
    GrowCycleStatus,
    GrowCycleZoneRole,
    RuntimeTargetMetric,
)
from ai_greenhouse.cultivation.schemas import GrowCycleCreate, RuntimeTargetRead

SERVER_ASSIGNED_FIELDS: tuple[str, ...] = (
    "status",
    "current_stage_id",
    "started_at",
    "ended_at",
)
"""Everything about a cycle that a client must not be able to state.

Each of them is either derived from the recipe version or written by a lifecycle
transition. A body that could carry one would let a caller claim a cycle is
``active`` without the stage instance and runtime target that make it true.
"""


def grow_cycle(**overrides: Any) -> dict[str, Any]:
    """Build a valid grow cycle creation body.

    Args:
        **overrides: Fields replacing the defaults.

    Returns:
        The cycle body.
    """
    return {
        "code": "basil-demo-cycle",
        "name": "Basil Grow Cycle",
        "facility_id": str(uuid4()),
        "climate_zone_id": str(uuid4()),
        "recipe_version_id": str(uuid4()),
        "planned_start_at": None,
    } | overrides


def test_a_valid_body_is_accepted() -> None:
    payload = GrowCycleCreate.model_validate(grow_cycle())

    assert payload.code == "basil-demo-cycle"
    assert payload.name == "Basil Grow Cycle"
    assert payload.planned_start_at is None


def test_planned_start_at_is_the_one_instant_a_client_may_state() -> None:
    """An intention changes no rule; it is recorded and nothing schedules from it."""
    intended = datetime(2026, 8, 4, 6, 0, tzinfo=UTC)

    payload = GrowCycleCreate.model_validate(grow_cycle(planned_start_at=intended.isoformat()))

    assert payload.planned_start_at == intended


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GrowCycleCreate.model_validate(grow_cycle(control_loop_id=str(uuid4())))


@pytest.mark.parametrize("field", SERVER_ASSIGNED_FIELDS)
def test_a_client_cannot_state_a_server_assigned_field(field: str) -> None:
    """Sending one is a refusal, not a silently dropped key."""
    with pytest.raises(ValidationError):
        GrowCycleCreate.model_validate(grow_cycle(**{field: "active"}))


@pytest.mark.parametrize(
    "field",
    ["code", "name", "facility_id", "climate_zone_id", "recipe_version_id"],
)
def test_every_reference_and_label_is_required(field: str) -> None:
    body = grow_cycle()
    del body[field]

    with pytest.raises(ValidationError):
        GrowCycleCreate.model_validate(body)


@pytest.mark.parametrize("code", ["Basil-Cycle", "basil cycle", "-basil", "", "a" * 81])
def test_the_code_is_wired_to_the_shared_slug_type(code: str) -> None:
    with pytest.raises(ValidationError):
        GrowCycleCreate.model_validate(grow_cycle(code=code))


def test_the_code_and_name_bounds_come_from_the_model() -> None:
    payload = GrowCycleCreate.model_validate(
        grow_cycle(
            code="a" * MAX_GROW_CYCLE_CODE_LENGTH,
            name="n" * MAX_GROW_CYCLE_NAME_LENGTH,
        )
    )

    assert len(payload.code) == MAX_GROW_CYCLE_CODE_LENGTH
    assert len(payload.name) == MAX_GROW_CYCLE_NAME_LENGTH


def test_a_name_longer_than_the_bound_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GrowCycleCreate.model_validate(grow_cycle(name="n" * (MAX_GROW_CYCLE_NAME_LENGTH + 1)))


def test_a_runtime_target_serialises_its_band_as_json_numbers() -> None:
    """A band returned as a string would have to be parsed by every reader."""
    target = RuntimeTargetRead(
        id=uuid4(),
        control_loop_id=uuid4(),
        grow_cycle_id=uuid4(),
        target_requirement_id=uuid4(),
        metric_type=RuntimeTargetMetric.AIR_TEMPERATURE,
        lower_value=Decimal("22"),
        upper_value=Decimal("26"),
        unit="°C",
        effective_from=datetime(2026, 8, 1, tzinfo=UTC),
        effective_to=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    encoded = json.loads(target.model_dump_json())

    assert encoded["lower_value"] == 22.0
    assert encoded["upper_value"] == 26.0
    assert encoded["effective_to"] is None


def test_the_lifecycle_states_are_the_four_m5_defines() -> None:
    assert {member.value for member in GrowCycleStatus} == {
        "planned",
        "active",
        "completed",
        "aborted",
    }


def test_a_zone_assignment_covers_only_climate() -> None:
    assert {member.value for member in GrowCycleZoneRole} == {"climate"}


def test_only_air_temperature_is_materialized() -> None:
    """Humidity and photoperiod stay display-only properties of the recipe."""
    assert {member.value for member in RuntimeTargetMetric} == {"air_temperature"}
