"""Schema-level rules of the control zone request bodies."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import ZoneType
from ai_greenhouse.topology.schemas import ControlZoneCreate, ControlZoneUpdate


def test_create_parses_the_zone_type() -> None:
    payload = ControlZoneCreate(
        facility_id=uuid4(),
        name="Main Climate",
        code="main-climate",
        zone_type="climate",
    )

    assert payload.zone_type is ZoneType.CLIMATE


@pytest.mark.parametrize(
    "zone_type",
    ["climate", "irrigation", "lighting", "measurement", "nutrient_solution", "safety"],
)
def test_create_accepts_every_documented_type(zone_type: str) -> None:
    payload = ControlZoneCreate(
        facility_id=uuid4(),
        name="Zone",
        code="zone",
        zone_type=zone_type,
    )

    assert payload.zone_type == zone_type


@pytest.mark.parametrize("zone_type", ["vibes", "Climate", "", "nutrient solution"])
def test_create_rejects_an_unknown_type(zone_type: str) -> None:
    with pytest.raises(ValidationError):
        ControlZoneCreate(
            facility_id=uuid4(),
            name="Zone",
            code="zone",
            zone_type=zone_type,
        )


def test_create_strips_the_name() -> None:
    payload = ControlZoneCreate(
        facility_id=uuid4(),
        name="  Main Climate  ",
        code="main-climate",
        zone_type="climate",
    )

    assert payload.name == "Main Climate"


@pytest.mark.parametrize("code", ["Climate", "-climate", "climate_", "my climate", ""])
def test_create_rejects_non_slug_codes(code: str) -> None:
    with pytest.raises(ValidationError):
        ControlZoneCreate(
            facility_id=uuid4(),
            name="Main Climate",
            code=code,
            zone_type="climate",
        )


def test_create_requires_a_facility() -> None:
    with pytest.raises(ValidationError):
        ControlZoneCreate.model_validate(
            {"name": "Main Climate", "code": "main-climate", "zone_type": "climate"}
        )


def test_create_rejects_a_site_id() -> None:
    """A zone reaches its site through its facility and declares none of its own."""
    with pytest.raises(ValidationError):
        ControlZoneCreate.model_validate(
            {
                "facility_id": str(uuid4()),
                "site_id": str(uuid4()),
                "name": "Main Climate",
                "code": "main-climate",
                "zone_type": "climate",
            }
        )


def test_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ControlZoneCreate.model_validate(
            {
                "facility_id": str(uuid4()),
                "name": "Main Climate",
                "code": "main-climate",
                "zone_type": "climate",
                "priority": 3,
            }
        )


def test_update_records_only_the_submitted_fields() -> None:
    payload = ControlZoneUpdate.model_validate({"name": "Main Climate v2"})

    assert payload.model_fields_set == {"name"}
    assert payload.zone_type is None
    assert payload.status is None


def test_update_accepts_an_empty_body() -> None:
    assert ControlZoneUpdate.model_validate({}).model_fields_set == set()


def test_update_accepts_code_so_the_service_can_refuse_it() -> None:
    payload = ControlZoneUpdate.model_validate({"code": "moved"})

    assert "code" in payload.model_fields_set, (
        "code must reach the service, which answers 409 immutable_field"
    )


def test_update_accepts_facility_id_so_the_service_can_refuse_it() -> None:
    payload = ControlZoneUpdate.model_validate({"facility_id": str(uuid4())})

    assert "facility_id" in payload.model_fields_set, (
        "facility_id must reach the service, which answers 409 zone_facility_immutable"
    )


def test_update_parses_the_status() -> None:
    assert ControlZoneUpdate.model_validate({"status": "archived"}).status is StatusEnum.ARCHIVED


def test_update_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ControlZoneUpdate.model_validate({"status": "retired"})


def test_update_rejects_an_unknown_zone_type() -> None:
    with pytest.raises(ValidationError):
        ControlZoneUpdate.model_validate({"zone_type": "vibes"})


def test_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ControlZoneUpdate.model_validate({"priority": 3})
