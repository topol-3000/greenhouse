"""Schema-level rules of the facility request bodies."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import FacilityType
from ai_greenhouse.topology.schemas import FacilityCreate, FacilityUpdate


def test_create_parses_the_facility_type() -> None:
    payload = FacilityCreate(
        site_id=uuid4(),
        name="Basil Growbox",
        code="basil-growbox",
        facility_type="growbox",
    )

    assert payload.facility_type is FacilityType.GROWBOX


@pytest.mark.parametrize(
    "facility_type",
    ["growbox", "greenhouse", "rack", "seedling_room", "utility"],
)
def test_create_accepts_every_documented_type(facility_type: str) -> None:
    payload = FacilityCreate(
        site_id=uuid4(),
        name="Object",
        code="object",
        facility_type=facility_type,
    )

    assert payload.facility_type == facility_type


@pytest.mark.parametrize("facility_type", ["spaceship", "Growbox", "", "seedling room"])
def test_create_rejects_an_unknown_type(facility_type: str) -> None:
    with pytest.raises(ValidationError):
        FacilityCreate(
            site_id=uuid4(),
            name="Object",
            code="object",
            facility_type=facility_type,
        )


def test_create_strips_the_name() -> None:
    payload = FacilityCreate(
        site_id=uuid4(),
        name="  Basil Growbox  ",
        code="basil-growbox",
        facility_type="growbox",
    )

    assert payload.name == "Basil Growbox"


@pytest.mark.parametrize("code", ["Growbox", "-growbox", "growbox_", "my growbox", ""])
def test_create_rejects_non_slug_codes(code: str) -> None:
    with pytest.raises(ValidationError):
        FacilityCreate(
            site_id=uuid4(),
            name="Basil Growbox",
            code=code,
            facility_type="growbox",
        )


def test_create_requires_a_site() -> None:
    with pytest.raises(ValidationError):
        FacilityCreate.model_validate(
            {"name": "Basil Growbox", "code": "basil-growbox", "facility_type": "growbox"}
        )


def test_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FacilityCreate.model_validate(
            {
                "site_id": str(uuid4()),
                "name": "Basil Growbox",
                "code": "basil-growbox",
                "facility_type": "growbox",
                "usable_area": 1.2,
            }
        )


def test_update_records_only_the_submitted_fields() -> None:
    payload = FacilityUpdate.model_validate({"name": "Basil Growbox v2"})

    assert payload.model_fields_set == {"name"}
    assert payload.facility_type is None
    assert payload.status is None


def test_update_accepts_an_empty_body() -> None:
    assert FacilityUpdate.model_validate({}).model_fields_set == set()


def test_update_accepts_code_so_the_service_can_refuse_it() -> None:
    payload = FacilityUpdate.model_validate({"code": "moved"})

    assert "code" in payload.model_fields_set, (
        "code must reach the service, which answers 409 immutable_field"
    )


def test_update_accepts_site_id_so_the_service_can_refuse_it() -> None:
    payload = FacilityUpdate.model_validate({"site_id": str(uuid4())})

    assert "site_id" in payload.model_fields_set, (
        "site_id must reach the service, which answers 409 immutable_field"
    )


def test_update_parses_the_status() -> None:
    assert FacilityUpdate.model_validate({"status": "archived"}).status is StatusEnum.ARCHIVED


def test_update_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        FacilityUpdate.model_validate({"status": "retired"})


def test_update_rejects_an_unknown_facility_type() -> None:
    with pytest.raises(ValidationError):
        FacilityUpdate.model_validate({"facility_type": "spaceship"})


def test_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FacilityUpdate.model_validate({"usable_area": 1.2})
