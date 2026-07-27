import pytest
from pydantic import ValidationError

from ai_greenhouse.core.config import Settings


def test_settings_use_documented_defaults() -> None:
    settings = Settings.for_test("postgresql+asyncpg://user:password@postgres:5432/greenhouse")

    assert settings.app_name == "ai-greenhouse-api"
    assert settings.app_env == "local"
    assert settings.app_host == "0.0.0.0"
    assert settings.app_port == 8000
    assert settings.log_level == "INFO"


def test_settings_support_explicit_overrides() -> None:
    settings = Settings.for_test(
        "postgresql+asyncpg://user:password@postgres:5432/greenhouse",
        app_name="test-greenhouse",
        app_env="test",
        app_host="127.0.0.1",
        app_port=9000,
        log_level="debug",
    )

    assert settings.app_name == "test-greenhouse"
    assert settings.app_env == "test"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 9000
    assert settings.log_level == "DEBUG"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_database_url_is_secret_safe() -> None:
    database_url = "postgresql+asyncpg://user:top-secret@postgres:5432/greenhouse"
    settings = Settings.for_test(database_url)

    assert database_url not in repr(settings)
    assert "top-secret" not in repr(settings)
    assert settings.database_url_value() == database_url
