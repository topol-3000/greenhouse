import json
import logging
from collections.abc import Callable
from io import StringIO

from ai_greenhouse.core.config import Settings
from ai_greenhouse.core.logging import configure_logging

DATABASE_URL = "postgresql+asyncpg://user:top-secret@postgres:5432/greenhouse"


def test_third_party_exceptions_are_rendered_and_redacted(
    build_settings: Callable[..., Settings],
) -> None:
    """Libraries log through the standard library, so that chain must scrub too."""
    settings = build_settings(DATABASE_URL, app_env="test")
    log_output = StringIO()
    configure_logging(settings, stream=log_output)

    try:
        raise RuntimeError(f"connection to {DATABASE_URL} failed")
    except RuntimeError:
        logging.getLogger("sqlalchemy.pool").exception("third_party_failure")

    rendered_logs = log_output.getvalue()
    record = json.loads(rendered_logs)

    assert DATABASE_URL not in rendered_logs
    assert "top-secret" not in rendered_logs
    assert record["event"] == "third_party_failure"
    assert record["exception"].startswith("Traceback (most recent call last):")
    assert "[REDACTED]" in record["exception"]
