"""Schema-level rules of the assignment body and the role/kind mapping.

The mapping is unit-tested rather than only exercised through the API because
it is the single place the role rules are written down. A role dropped from it
would not fail loudly — it would quietly become unassignable.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.points.models import PointKind
from ai_greenhouse.topology.models import ZonePointRole
from ai_greenhouse.topology.schemas import ZonePointAssignmentCreate
from ai_greenhouse.topology.service import ROLE_ALLOWED_POINT_KINDS

ROLES: list[str] = [
    "primary_measurement",
    "secondary_measurement",
    "control_output",
    "status_feedback",
    "safety_interlock",
    "derived_indicator",
]


def test_create_parses_the_role() -> None:
    payload = ZonePointAssignmentCreate(point_id=uuid4(), role="primary_measurement")

    assert payload.role is ZonePointRole.PRIMARY_MEASUREMENT


@pytest.mark.parametrize("role", ROLES)
def test_create_accepts_every_documented_role(role: str) -> None:
    payload = ZonePointAssignmentCreate(point_id=uuid4(), role=role)

    assert payload.role == role


@pytest.mark.parametrize("role", ["vibes", "Primary_measurement", "", "primary measurement"])
def test_create_rejects_an_unknown_role(role: str) -> None:
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(point_id=uuid4(), role=role)


def test_create_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(
            point_id=uuid4(),
            role="control_output",
            control_zone_id=uuid4(),
        )


def test_create_requires_a_point() -> None:
    with pytest.raises(ValidationError):
        ZonePointAssignmentCreate(role="control_output")


def test_every_role_is_mapped() -> None:
    assert set(ROLE_ALLOWED_POINT_KINDS) == set(ZonePointRole)


def test_every_role_accepts_at_least_one_kind() -> None:
    unusable: list[ZonePointRole] = [
        role for role, kinds in ROLE_ALLOWED_POINT_KINDS.items() if not kinds
    ]

    assert unusable == []


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
