import json
from collections.abc import Callable
from io import StringIO

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from ai_greenhouse.api.dependencies import (
    DatabaseHealthProbe,
    get_database_health_probe,
)
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings
from ai_greenhouse.core.logging import configure_logging


def integration_settings() -> Settings:
    try:
        return Settings(app_env="test")
    except ValidationError:
        pytest.skip("DATABASE_URL is required to run PostgreSQL integration tests")


@pytest.mark.asyncio
async def test_health_reports_postgresql_18_and_applied_baseline() -> None:
    app = create_app(integration_settings())

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

        async with app.state.database_engine.connect() as connection:
            server_version = await connection.scalar(text("SHOW server_version"))
            migration_version = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-greenhouse-api",
        "database": "ok",
    }
    assert str(server_version).startswith("18.")
    assert migration_version == "20260727_0001"


@pytest.mark.asyncio
async def test_health_failure_is_safe_and_logged_as_json(
    build_settings: Callable[..., Settings],
) -> None:
    database_url = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"
    settings = build_settings(database_url, app_env="test")
    app = create_app(settings)
    log_output = StringIO()
    configure_logging(settings, stream=log_output)

    async def failing_probe() -> None:
        raise RuntimeError(database_url)

    async def override_probe() -> DatabaseHealthProbe:
        return failing_probe

    app.dependency_overrides[get_database_health_probe] = override_probe

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

    rendered_logs = log_output.getvalue()
    log_records = [json.loads(line) for line in rendered_logs.splitlines() if line.strip()]

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "service": "ai-greenhouse-api",
        "database": "unavailable",
    }
    assert database_url not in response.text
    assert "top-secret" not in response.text
    assert database_url not in rendered_logs
    assert "top-secret" not in rendered_logs

    failure_record = next(
        record for record in log_records if record["event"] == "database_health_check_failed"
    )
    request_record = next(record for record in log_records if record["event"] == "http_request")

    for record in log_records:
        assert {"timestamp", "level", "event", "service", "environment"} <= record.keys()

    assert "exception" in failure_record
    assert "[REDACTED]" in failure_record["exception"]
    assert request_record["method"] == "GET"
    assert request_record["path"] == "/health"
    assert request_record["status_code"] == 503
    assert isinstance(request_record["duration_ms"], float)
