"""Schema contracts of the control module.

The value types themselves are covered by ``unit/test_types.py``. What is
asserted here is what these schemas add: the band ordering, the fields a
creation body accepts, and the fields it refuses to take from a client.

For the manual command boundary the refusals are the contract. Boolean on/off is
the whole of what this unit implements, so a numeric level, a free-form object
and an unknown field are each rejected here rather than at the service — a
schema that let one through would advertise control the backend does not have.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.control.models import CommandSource, CommandState, ControlPolicyType
from ai_greenhouse.control.schemas import (
    CommandRead,
    CommandRejectionReason,
    ControlLoopCreate,
    ManualCommandCreate,
)


def valid_body(**overrides: object) -> dict[str, object]:
    """Build a control-loop creation body.

    Args:
        **overrides: Fields replacing the defaults.

    Returns:
        A mapping accepted by :class:`ControlLoopCreate` unless overridden.
    """
    return {
        "control_zone_id": uuid4(),
        "measurement_point_id": uuid4(),
        "control_point_id": uuid4(),
        "status_point_id": uuid4(),
        "lower_threshold": Decimal("24.0"),
        "upper_threshold": Decimal("26.0"),
    } | overrides


def test_a_band_is_accepted_when_its_lower_end_is_below_its_upper_end() -> None:
    """The documented demo band is valid and reaches the service unchanged."""
    payload = ControlLoopCreate.model_validate(valid_body())

    assert payload.lower_threshold == Decimal("24.0")
    assert payload.upper_threshold == Decimal("26.0")


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        pytest.param(Decimal("26.0"), Decimal("26.0"), id="equal ends leave no hysteresis"),
        pytest.param(Decimal("27.0"), Decimal("26.0"), id="inverted ends"),
    ],
)
def test_a_band_whose_lower_end_is_not_below_its_upper_end_is_refused(
    lower: Decimal,
    upper: Decimal,
) -> None:
    """Only a strictly ordered band can switch a fan without oscillating."""
    with pytest.raises(ValidationError, match="lower_threshold must be less than upper_threshold"):
        ControlLoopCreate.model_validate(valid_body(lower_threshold=lower, upper_threshold=upper))


def test_a_policy_type_submitted_by_a_client_is_refused() -> None:
    """The one policy of M3 is chosen by the service, never by the request."""
    with pytest.raises(ValidationError):
        ControlLoopCreate.model_validate(
            valid_body(policy_type=ControlPolicyType.HYSTERESIS_V1.value)
        )


def test_a_threshold_that_is_not_finite_is_refused() -> None:
    """A band bounded by infinity would never switch the fan back."""
    with pytest.raises(ValidationError):
        ControlLoopCreate.model_validate(valid_body(upper_threshold=float("inf")))


# --- The manual command boundary ------------------------------------------


def manual_body(**overrides: object) -> dict[str, object]:
    """Build a manual command request body.

    Args:
        **overrides: Fields replacing the defaults.

    Returns:
        A mapping accepted by :class:`ManualCommandCreate` unless overridden.
    """
    return {
        "control_zone_id": uuid4(),
        "target_point_id": uuid4(),
        "desired_value": True,
    } | overrides


def test_a_manual_request_carries_the_zone_the_target_and_a_boolean() -> None:
    """The complete accepted body, and nothing beside it."""
    payload = ManualCommandCreate.model_validate(manual_body(desired_value=False))

    assert set(ManualCommandCreate.model_fields) == {
        "control_zone_id",
        "target_point_id",
        "desired_value",
    }
    assert payload.desired_value is False


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("on", id="a string"),
        pytest.param(75, id="a percentage"),
        pytest.param({"speed": 3}, id="an object"),
        pytest.param(None, id="null"),
    ],
)
def test_a_desired_value_that_is_not_boolean_is_refused(value: object) -> None:
    """This unit is on/off control, and the schema is where that stops being negotiable.

    A number would be a dimming contract, an object would be generic JSON
    command submission, and a string would be either of them spelled out. None
    of the three reaches the service.
    """
    with pytest.raises(ValidationError):
        ManualCommandCreate.model_validate(manual_body(desired_value=value))


def test_a_manual_request_refuses_an_unknown_field() -> None:
    """There is no way to smuggle a second instruction past the boundary."""
    with pytest.raises(ValidationError):
        ManualCommandCreate.model_validate(manual_body(payload={"speed": 3}))


def test_the_idempotency_key_is_not_a_body_field() -> None:
    """It travels in the ``Idempotency-Key`` header, so the body cannot carry it.

    A body that also accepted the key would give one request two places to state
    it, and two places that can disagree.
    """
    with pytest.raises(ValidationError):
        ManualCommandCreate.model_validate(manual_body(idempotency_key=str(uuid4())))


def test_the_command_representation_admits_a_command_without_a_loop() -> None:
    """The defect this unit was reported for: every command looked automatic.

    A manual command has no control loop and no trigger sample, and the read
    model has to be able to say so rather than requiring a value that would
    describe a decision nobody made.
    """
    command = CommandRead.model_validate(
        {
            "id": uuid4(),
            "source": CommandSource.MANUAL,
            "idempotency_key": str(uuid4()),
            "control_zone_id": uuid4(),
            "control_loop_id": None,
            "trigger_sample_id": None,
            "target_point_id": uuid4(),
            "reported_point_id": uuid4(),
            "gateway_id": uuid4(),
            "desired_value": True,
            "state": CommandState.PENDING,
            "result_control_sample_id": None,
            "result_status_sample_id": None,
            "issued_at": datetime(2026, 8, 3, tzinfo=UTC),
            "executed_at": None,
            "acknowledged_at": None,
            "rejection_reason": None,
            "created_at": datetime(2026, 8, 3, tzinfo=UTC),
        }
    )

    assert command.source is CommandSource.MANUAL
    assert command.control_loop_id is None
    assert command.trigger_sample_id is None


def test_a_rejection_reason_is_typed_rather_than_an_open_object() -> None:
    """A client branches on ``code``, so ``code`` has a shape it can rely on."""
    reason = CommandRejectionReason.model_validate(
        {"code": "actuator_unavailable", "message": "The relay did not respond."}
    )

    assert reason.code == "actuator_unavailable"
    with pytest.raises(ValidationError):
        CommandRejectionReason.model_validate({"code": "Not A Code", "message": "x"})
    with pytest.raises(ValidationError):
        CommandRejectionReason.model_validate({"code": "ok", "message": "x", "extra": 1})


def test_the_two_command_sources_are_the_documented_pair() -> None:
    """An added or removed source fails here rather than in a client's switch."""
    assert {member.value for member in CommandSource} == {"control_loop", "manual"}


def test_the_lifecycle_states_are_the_documented_three() -> None:
    """``pending`` is the only non-terminal one, and no speculative state joined it."""
    assert {member.value for member in CommandState} == {"pending", "applied", "rejected"}
