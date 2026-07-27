"""Schema-level rules of the site request bodies."""

import pytest
from pydantic import ValidationError

from ai_greenhouse.core.types import DEFAULT_TIMEZONE
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.topology.schemas import SiteCreate, SiteUpdate


def test_create_defaults_the_timezone() -> None:
    assert SiteCreate(name="Home", code="home").timezone == DEFAULT_TIMEZONE


def test_create_strips_the_name() -> None:
    assert SiteCreate(name="  Home  ", code="home").name == "Home"


@pytest.mark.parametrize("code", ["Home", "-home", "home_", "my home", ""])
def test_create_rejects_non_slug_codes(code: str) -> None:
    with pytest.raises(ValidationError):
        SiteCreate(name="Home", code=code)


def test_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SiteCreate(name="Home", code="home", owner="someone")


def test_update_records_only_the_submitted_fields() -> None:
    payload = SiteUpdate.model_validate({"name": "Home lab"})

    assert payload.model_fields_set == {"name"}
    assert payload.timezone is None
    assert payload.status is None


def test_update_accepts_an_empty_body() -> None:
    assert SiteUpdate.model_validate({}).model_fields_set == set()


def test_update_accepts_code_so_the_service_can_refuse_it() -> None:
    payload = SiteUpdate.model_validate({"code": "moved"})

    assert "code" in payload.model_fields_set, (
        "code must reach the service, which answers 409 immutable_field"
    )


def test_update_parses_the_status() -> None:
    assert SiteUpdate.model_validate({"status": "archived"}).status is StatusEnum.ARCHIVED


def test_update_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        SiteUpdate.model_validate({"status": "retired"})


def test_update_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SiteUpdate.model_validate({"owner": "someone"})
