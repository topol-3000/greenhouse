import logging
from collections.abc import Callable, Iterator

import pytest

from ai_greenhouse.core.config import Settings

SETTINGS_ENV_VARS = (
    "APP_NAME",
    "APP_ENV",
    "APP_HOST",
    "APP_PORT",
    "LOG_LEVEL",
    "DATABASE_URL",
)


@pytest.fixture
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove settings variables so assertions cannot read ambient configuration."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def build_settings(isolated_environment: None) -> Callable[..., Settings]:
    """Build settings from explicit values only, ignoring the environment and .env."""

    def factory(database_url: str, **overrides: object) -> Settings:
        return Settings(database_url=database_url, _env_file=None, **overrides)

    return factory


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    """Undo the global logging state that configure_logging installs."""
    root_logger = logging.getLogger()
    handlers = root_logger.handlers[:]
    level = root_logger.level
    try:
        yield
    finally:
        root_logger.handlers[:] = handlers
        root_logger.setLevel(level)
