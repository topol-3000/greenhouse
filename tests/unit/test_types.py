import pytest
from pydantic import BaseModel, ValidationError

from ai_greenhouse.core.types import (
    MAX_CODE_LENGTH,
    MAX_TIMEZONE_LENGTH,
    CodeStr,
    NameStr,
    TimezoneStr,
)


class CodeModel(BaseModel):
    code: CodeStr


class NameModel(BaseModel):
    name: NameStr


class TimezoneModel(BaseModel):
    timezone: TimezoneStr


@pytest.mark.parametrize(
    "value",
    [
        "home",
        "basil-growbox",
        "air_temperature",
        "a",
        "0",
        "a" * MAX_CODE_LENGTH,
    ],
)
def test_code_accepts_slugs(value: str) -> None:
    assert CodeModel(code=value).code == value


@pytest.mark.parametrize(
    "value",
    [
        "Basil_Growbox",
        "-abc",
        "abc-",
        "",
        "a" * (MAX_CODE_LENGTH + 1),
        "_abc",
        "abc_",
        "basil growbox",
        "basil.growbox",
        "grüne-box",
    ],
)
def test_code_rejects_non_slugs(value: str) -> None:
    with pytest.raises(ValidationError):
        CodeModel(code=value)


def test_name_strips_surrounding_whitespace() -> None:
    assert NameModel(name="  Basil Growbox  ").name == "Basil Growbox"


@pytest.mark.parametrize("value", ["", "   ", "a" * 201])
def test_name_rejects_values_outside_bounds(value: str) -> None:
    with pytest.raises(ValidationError):
        NameModel(name=value)


@pytest.mark.parametrize("value", ["a", "a" * 200])
def test_name_accepts_values_on_the_bounds(value: str) -> None:
    assert NameModel(name=value).name == value


@pytest.mark.parametrize("value", ["UTC", "Europe/Kiev", "America/New_York", "Etc/GMT+3"])
def test_timezone_accepts_known_iana_names(value: str) -> None:
    assert TimezoneModel(timezone=value).timezone == value


@pytest.mark.parametrize(
    "value",
    [
        "Not/AZone",
        "Europe/Nowhere",
        "europe/kiev",
        "UTC+3",
        "",
        "   ",
        "../etc/passwd",
        "/etc/localtime",
        "a" * (MAX_TIMEZONE_LENGTH + 1),
    ],
)
def test_timezone_rejects_anything_tzdata_does_not_know(value: str) -> None:
    with pytest.raises(ValidationError):
        TimezoneModel(timezone=value)


def test_timezone_strips_surrounding_whitespace() -> None:
    assert TimezoneModel(timezone="  Europe/Kiev  ").timezone == "Europe/Kiev"
