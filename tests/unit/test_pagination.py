from typing import Any

import pytest
from sqlalchemy import MetaData, Select, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase

from ai_greenhouse.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    Page,
    PageParams,
    paginate,
)
from ai_greenhouse.infrastructure.database.base import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from ai_greenhouse.infrastructure.database.metadata import NAMING_CONVENTION


class SampleBase(DeclarativeBase):
    """Isolated metadata so the test table never reaches the shared schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Sample(SampleBase, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sample"


class RecordingSession:
    """Captures the statements ``paginate`` builds without touching a database."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.statements: list[Select[Any]] = []

    async def scalar(self, statement: Select[Any]) -> int:
        self.statements.append(statement)
        return self.total

    async def scalars(self, statement: Select[Any]) -> list[Any]:
        self.statements.append(statement)
        return []


def test_default_window() -> None:
    params = PageParams()

    assert params.limit == DEFAULT_LIMIT
    assert params.offset == 0


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (MAX_LIMIT, MAX_LIMIT),
        (MAX_LIMIT + 1, MAX_LIMIT),
        (10_000, MAX_LIMIT),
        (MIN_LIMIT, MIN_LIMIT),
        (0, MIN_LIMIT),
        (-5, MIN_LIMIT),
    ],
)
def test_limit_is_clamped(requested: int, expected: int) -> None:
    assert PageParams(limit=requested).limit == expected


def test_negative_offset_is_rejected() -> None:
    with pytest.raises(ValueError, match="offset must not be negative"):
        PageParams(offset=-1)


def test_offset_is_preserved() -> None:
    assert PageParams(offset=120).offset == 120


def test_page_serialises_as_the_documented_envelope() -> None:
    page = Page[int](items=[1, 2], total=7, limit=50, offset=0)

    assert page.model_dump() == {"items": [1, 2], "total": 7, "limit": 50, "offset": 0}
    assert page.model_dump_json() == '{"items":[1,2],"total":7,"limit":50,"offset":0}'


def test_empty_page_serialises_with_an_empty_item_list() -> None:
    assert Page[int](items=[], total=0, limit=50, offset=0).model_dump() == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }


async def test_paginate_applies_deterministic_order_and_window() -> None:
    session = RecordingSession(total=7)
    params = PageParams(limit=10, offset=20)

    items, total = await paginate(session, select(Sample), Sample, params)  # type: ignore[arg-type]

    assert items == []
    assert total == 7

    count_statement, window_statement = session.statements
    compiled_count = str(count_statement.compile(dialect=postgresql.dialect()))
    compiled_window = str(window_statement.compile(dialect=postgresql.dialect()))

    assert "count(*)" in compiled_count
    assert "ORDER BY" not in compiled_count
    assert "ORDER BY sample.created_at ASC, sample.id ASC" in compiled_window
    assert "LIMIT" in compiled_window
    assert "OFFSET" in compiled_window
    assert window_statement.compile(dialect=postgresql.dialect()).params == {
        "param_1": 10,
        "param_2": 20,
    }
