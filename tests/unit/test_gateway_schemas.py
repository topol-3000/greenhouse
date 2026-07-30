"""Schema and pure compatibility rules for gateway management."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.schemas import (
    GatewayCreate,
    GatewayPointAuthorizationRequest,
)
from ai_greenhouse.gateways.service import configuration_conflicts
from ai_greenhouse.infrastructure.database.base import StatusEnum

GATEWAY_ID = UUID("11111111-1111-1111-1111-111111111111")
SITE_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_SITE_ID = UUID("33333333-3333-3333-3333-333333333333")
POINT_ID = UUID("44444444-4444-4444-4444-444444444444")


def test_gateway_create_uses_the_shared_code_type_and_forbids_extra_fields() -> None:
    """The management request accepts only one stable code and functional site."""
    assert GatewayCreate(code="north-gateway", site_id=SITE_ID).model_dump() == {
        "code": "north-gateway",
        "site_id": SITE_ID,
    }
    with pytest.raises(ValidationError):
        GatewayCreate.model_validate(
            {"code": "North Gateway", "site_id": str(SITE_ID), "name": "North"}
        )


def test_authorization_request_accepts_duplicates_for_deterministic_normalization() -> None:
    """Transport validation leaves duplicate normalization to the use case."""
    payload = GatewayPointAuthorizationRequest(point_ids=[POINT_ID, POINT_ID])

    assert payload.point_ids == [POINT_ID, POINT_ID]


def test_configuration_conflicts_are_normalized_and_field_level() -> None:
    """Compatibility describes each expected and actual functional value."""
    gateway = Gateway(
        id=GATEWAY_ID,
        code="north-gateway",
        site_id=SITE_ID,
        status=StatusEnum.ARCHIVED,
    )

    assert configuration_conflicts(gateway, expected_site_id=OTHER_SITE_ID) == [
        {
            "field": "site_id",
            "expected": str(OTHER_SITE_ID),
            "actual": str(SITE_ID),
        },
        {
            "field": "status",
            "expected": "active",
            "actual": "archived",
        },
    ]
