"""Schema-level rules of the assignment body and the role/kind mapping.

The mapping is unit-tested rather than only exercised through the API because
it is the single place the role rules are written down. A role dropped from it
would not fail loudly — it would quietly become unassignable. Between them, the
two parametrised tests below name every role and the exact kinds it admits, so
the API tests only need to prove that the service consults the mapping.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.points.models import PointKind
from ai_greenhouse.topology.models import ZonePointRole
from ai_greenhouse.topology.schemas import ZonePointAssignmentCreate
from ai_greenhouse.topology.service import ROLE_ALLOWED_POINT_KINDS


def test_the_roles_are_the_documented_ones() -> None:
    """The set is a published contract; adding or dropping one is an API change."""
    assert {member.value for member in ZonePointRole} == {
        "primary_measurement",
        "secondary_measurement",
        "control_output",
        "status_feedback",
        "safety_interlock",
        "derived_indicator",
    }


def test_create_parses_the_role() -> None:
    payload = ZonePointAssignmentCreate(point_id=uuid4(), role="primary_measurement")

    assert payload.role is ZonePointRole.PRIMARY_MEASUREMENT


def test_create_rejects_an_unknown_role() -> None:
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(point_id=uuid4(), role="vibes")


def test_create_requires_a_point() -> None:
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(role="control_output")


def test_create_rejects_an_unknown_field() -> None:
    """The zone comes from the path, so the body must not carry one of its own."""
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(
            point_id=uuid4(),
            role="control_output",
            control_zone_id=uuid4(),
        )


def test_every_role_is_mapped() -> None:
    """An unmapped role would be silently unassignable rather than an error."""
    assert set(ROLE_ALLOWED_POINT_KINDS) == set(ZonePointRole)


@pytest.mark.parametrize(
    ("role", "expected_kinds"),
    [
        (
            ZonePointRole.PRIMARY_MEASUREMENT,
            {PointKind.MEASUREMENT, PointKind.DERIVED},
        ),
        (
            ZonePointRole.SECONDARY_MEASUREMENT,
            {PointKind.MEASUREMENT, PointKind.DERIVED},
        ),
        (ZonePointRole.CONTROL_OUTPUT, {PointKind.CONTROL}),
        (ZonePointRole.STATUS_FEEDBACK, {PointKind.STATUS}),
    ],
)
def test_the_constrained_roles_accept_exactly_the_documented_kinds(
    role: ZonePointRole,
    expected_kinds: set[PointKind],
) -> None:
    assert ROLE_ALLOWED_POINT_KINDS[role] == expected_kinds


@pytest.mark.parametrize(
    "role",
    [ZonePointRole.SAFETY_INTERLOCK, ZonePointRole.DERIVED_INDICATOR],
)
def test_the_unconstrained_roles_accept_every_kind(role: ZonePointRole) -> None:
    """These two are open on purpose; narrowing them is a decision M1 has not made."""
    assert ROLE_ALLOWED_POINT_KINDS[role] == set(PointKind)
