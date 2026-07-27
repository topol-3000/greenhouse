import pytest
from pydantic import BaseModel, ValidationError

from ai_greenhouse.core.types import MAX_CODE_LENGTH, CodeStr, NameStr


class CodeModel(BaseModel):
    code: CodeStr


class NameModel(BaseModel):
    name: NameStr


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
