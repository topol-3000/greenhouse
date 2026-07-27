from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, MetaData, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateTable

from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusEnum,
    StatusMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)
from ai_greenhouse.infrastructure.database.metadata import NAMING_CONVENTION, metadata


class SampleBase(DeclarativeBase):
    """Isolated metadata so the test table never reaches the shared schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Sample(SampleBase, UUIDPrimaryKeyMixin, TimestampMixin, StatusMixin):
    __tablename__ = "sample"


def compiled_ddl() -> str:
    return str(CreateTable(Sample.__table__).compile(dialect=postgresql.dialect()))


def test_base_is_bound_to_the_shared_metadata() -> None:
    assert Base.metadata is metadata


def test_domain_tables_register_themselves_on_the_shared_metadata() -> None:
    import ai_greenhouse.topology.models  # noqa: F401

    assert "sites" in Base.metadata.tables, (
        "migrations autogenerate against this metadata; an unregistered table is invisible"
    )


def test_primary_key_is_a_uuid_generated_by_the_application() -> None:
    column = Sample.__table__.c.id

    assert column.primary_key is True
    assert isinstance(column.type, Uuid)
    assert column.default is not None
    assert column.default.is_callable is True
    assert isinstance(column.default.arg(None), UUID), "identifiers come from uuid4"
    assert column.server_default is None, "the database must not generate identifiers"


def test_timestamps_are_non_null_utc_timestamptz() -> None:
    created_at = Sample.__table__.c.created_at
    updated_at = Sample.__table__.c.updated_at

    for column in (created_at, updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.default is not None
        assert column.default.arg(None).tzinfo is not None
        assert column.server_default is None, "instants come from the application"

    assert created_at.onupdate is None
    assert updated_at.onupdate is not None, "updated_at must be refreshed on update"
    assert updated_at.onupdate.arg(None).tzinfo is not None


def test_utc_now_returns_an_aware_utc_instant() -> None:
    value = utc_now()

    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value.utcoffset() == UTC.utcoffset(None)


def test_status_enum_values() -> None:
    assert [member.value for member in StatusEnum] == ["active", "archived"]


def test_status_column_defaults_to_active() -> None:
    column = Sample.__table__.c.status

    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg is StatusEnum.ACTIVE


def test_enums_are_stored_as_varchar_with_a_check_constraint() -> None:
    column = Sample.__table__.c.status
    assert isinstance(column.type, Enum)
    assert column.type.native_enum is False

    ddl = compiled_ddl()
    assert "status VARCHAR" in ddl
    assert "CONSTRAINT ck_sample_status CHECK (status IN ('active', 'archived'))" in ddl
    assert "CREATE TYPE" not in ddl, "native PostgreSQL enums are not the chosen convention"


def test_constraints_follow_the_milestone_0_naming_convention() -> None:
    ddl = compiled_ddl()

    assert "CONSTRAINT pk_sample PRIMARY KEY (id)" in ddl
    assert "CONSTRAINT ck_sample_status" in ddl


def test_status_is_indexed_for_collection_filtering() -> None:
    indexed = {column.name for index in Sample.__table__.indexes for column in index.columns}

    assert "status" in indexed
