"""Schema-level rules of the site, facility and control zone request bodies.

The three topology entities share a request shape — a name, a slug code, an
optional parent identifier and a status — so the rules they have in common are
asserted once here, parametrised over the three schemas. A rule that applies to
only one of them gets its own test at the bottom of the module.

The shared value types themselves (``CodeStr``, ``NameStr``, ``TimezoneStr``)
are exercised case by case in ``test_types.py``. What this module adds is that
each schema is wired to them and that partial updates behave as the API
contract says.
"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from ai_greenhouse.core.types import DEFAULT_TIMEZONE
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.models import FacilityType, ZoneType
from ai_greenhouse.topology.schemas import (
    ControlZoneCreate,
    ControlZoneUpdate,
    FacilityCreate,
    FacilityUpdate,
    SiteCreate,
    SiteUpdate,
)

SITE_BODY: dict[str, Any] = {"name": "Home", "code": "home"}
FACILITY_BODY: dict[str, Any] = {
    "site_id": str(uuid4()),
    "name": "Basil Growbox",
    "code": "basil-growbox",
    "facility_type": "growbox",
}
CONTROL_ZONE_BODY: dict[str, Any] = {
    "facility_id": str(uuid4()),
    "name": "Main Climate",
    "code": "main-climate",
    "zone_type": "climate",
}

CREATE_SCHEMAS: list[Any] = [
    pytest.param(SiteCreate, SITE_BODY, id="site"),
    pytest.param(FacilityCreate, FACILITY_BODY, id="facility"),
    pytest.param(ControlZoneCreate, CONTROL_ZONE_BODY, id="control-zone"),
]
"""Each creation schema with a body it accepts."""

UPDATE_SCHEMAS: list[Any] = [
    pytest.param(SiteUpdate, id="site"),
    pytest.param(FacilityUpdate, id="facility"),
    pytest.param(ControlZoneUpdate, id="control-zone"),
]

IMMUTABLE_FIELDS: list[Any] = [
    pytest.param(SiteUpdate, "code", id="site-code"),
    pytest.param(FacilityUpdate, "code", id="facility-code"),
    pytest.param(FacilityUpdate, "site_id", id="facility-site"),
    pytest.param(ControlZoneUpdate, "code", id="control-zone-code"),
    pytest.param(ControlZoneUpdate, "facility_id", id="control-zone-facility"),
]
"""Fields the service answers ``409 immutable_field`` for, and so must receive."""


@pytest.mark.parametrize(("schema", "body"), CREATE_SCHEMAS)
def test_create_strips_the_name(schema: type[BaseModel], body: dict[str, Any]) -> None:
    payload = schema.model_validate(body | {"name": "  Padded name  "})

    assert payload.name == "Padded name"


@pytest.mark.parametrize(("schema", "body"), CREATE_SCHEMAS)
def test_create_rejects_a_non_slug_code(schema: type[BaseModel], body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(body | {"code": "Not A Slug"})


@pytest.mark.parametrize(("schema", "body"), CREATE_SCHEMAS)
def test_create_rejects_unknown_fields(schema: type[BaseModel], body: dict[str, Any]) -> None:
    """A field the API does not know is a client mistake, not something to ignore."""
    with pytest.raises(ValidationError):
        schema.model_validate(body | {"usable_area": 1.2})


@pytest.mark.parametrize(
    ("schema", "body", "parent"),
    [
        pytest.param(FacilityCreate, FACILITY_BODY, "site_id", id="facility"),
        pytest.param(ControlZoneCreate, CONTROL_ZONE_BODY, "facility_id", id="control-zone"),
    ],
)
def test_create_requires_its_parent(
    schema: type[BaseModel],
    body: dict[str, Any],
    parent: str,
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({key: value for key, value in body.items() if key != parent})


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_update_records_only_the_submitted_fields(schema: type[BaseModel]) -> None:
    """The service tells an omitted field from one explicitly set to null."""
    payload = schema.model_validate({"name": "A new name"})

    assert payload.model_fields_set == {"name"}
    assert payload.status is None


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_update_accepts_an_empty_body(schema: type[BaseModel]) -> None:
    assert schema.model_validate({}).model_fields_set == set()


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_update_rejects_an_unknown_status(schema: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({"status": "retired"})


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_update_rejects_unknown_fields(schema: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({"usable_area": 1.2})


@pytest.mark.parametrize(("schema", "field"), IMMUTABLE_FIELDS)
def test_update_accepts_immutable_fields_so_the_service_can_refuse_them(
    schema: type[BaseModel],
    field: str,
) -> None:
    """A 422 here would hide the ``409 immutable_field`` the contract promises."""
    value: str = str(uuid4()) if field.endswith("_id") else "moved"

    payload = schema.model_validate({field: value})

    assert field in payload.model_fields_set


@pytest.mark.parametrize(
    ("create_schema", "update_schema", "body", "field"),
    [
        pytest.param(
            FacilityCreate,
            FacilityUpdate,
            FACILITY_BODY,
            "facility_type",
            id="facility-type",
        ),
        pytest.param(
            ControlZoneCreate,
            ControlZoneUpdate,
            CONTROL_ZONE_BODY,
            "zone_type",
            id="zone-type",
        ),
    ],
)
def test_an_unknown_enum_member_is_refused_on_create_and_on_update(
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    body: dict[str, Any],
    field: str,
) -> None:
    """The type is a closed set, not free text, in both directions."""
    with pytest.raises(ValidationError):
        create_schema.model_validate(body | {field: "spaceship"})
    with pytest.raises(ValidationError):
        update_schema.model_validate({field: "spaceship"})


def test_the_facility_types_are_the_documented_ones() -> None:
    """The set is a published contract; adding or dropping one is an API change."""
    assert {member.value for member in FacilityType} == {
        "growbox",
        "greenhouse",
        "rack",
        "seedling_room",
        "utility",
    }


def test_the_zone_types_are_the_documented_ones() -> None:
    assert {member.value for member in ZoneType} == {
        "climate",
        "irrigation",
        "lighting",
        "measurement",
        "nutrient_solution",
        "safety",
    }


def test_site_create_defaults_the_timezone() -> None:
    assert SiteCreate.model_validate(SITE_BODY).timezone == DEFAULT_TIMEZONE


def test_a_zone_declares_no_site_of_its_own() -> None:
    """A zone reaches its site through its facility; ``site_id`` is not an input."""
    with pytest.raises(ValidationError):
        ControlZoneCreate.model_validate(CONTROL_ZONE_BODY | {"site_id": str(uuid4())})


def test_update_parses_the_status() -> None:
    """One case for the shared ``StatusEnum``; archiving is how entities retire."""
    assert SiteUpdate.model_validate({"status": "archived"}).status is StatusEnum.ARCHIVED
