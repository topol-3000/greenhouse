"""Schema contracts of the control module.

The value types themselves are covered by ``unit/test_types.py``. What is
asserted here is what these schemas add: the band ordering, the fields a
creation body accepts, and the fields it refuses to take from a client.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.control.models import ControlPolicyType
from ai_greenhouse.control.schemas import ControlLoopCreate


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
