import ast
import json
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from ai_greenhouse.api.errors import (
    INTERNAL_ERROR_CODE,
    INTERNAL_ERROR_MESSAGE,
    VALIDATION_ERROR_CODE,
)
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings
from ai_greenhouse.core.exceptions import (
    ConflictError,
    DomainError,
    ImmutableFieldError,
    NotFoundError,
    ReferenceError,
)
from ai_greenhouse.core.logging import configure_logging
from ai_greenhouse.core.types import CodeStr

FAKE_DATABASE_URL = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"
SITE_ID = UUID("11111111-1111-1111-1111-111111111111")

DOMAIN_ERRORS: dict[str, DomainError] = {
    "not_found": NotFoundError(
        "Site does not exist",
        code="site_not_found",
        details={"site_id": SITE_ID},
    ),
    "conflict": ConflictError(
        "Facility code already exists within the site",
        code="facility_code_conflict",
        details={"site_id": SITE_ID, "code": "basil-growbox"},
    ),
    "reference": ReferenceError(
        "Referenced site does not exist",
        code="site_not_found",
        details={"site_id": SITE_ID},
    ),
    "immutable": ImmutableFieldError("Point code cannot be changed", details={"field": "code"}),
    "bare": DomainError("Something the domain refuses"),
}


class ProbePayload(BaseModel):
    code: CodeStr


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)

    @application.get("/probe/domain-error/{kind}")
    async def raise_domain_error(kind: str) -> None:
        raise DOMAIN_ERRORS[kind]

    @application.post("/probe/validate")
    async def validate(payload: ProbePayload) -> ProbePayload:
        return payload

    @application.get("/probe/unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError(FAKE_DATABASE_URL)

    return application


def client(app: FastAPI, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_code"),
    [
        ("not_found", 404, "site_not_found"),
        ("conflict", 409, "facility_code_conflict"),
        ("reference", 422, "site_not_found"),
        ("immutable", 409, "immutable_field"),
        ("bare", 500, "domain_error"),
    ],
)
async def test_domain_errors_use_the_declared_status_and_envelope(
    app: FastAPI,
    kind: str,
    expected_status: int,
    expected_code: str,
) -> None:
    async with client(app) as http_client:
        response = await http_client.get(f"/probe/domain-error/{kind}")

    error = DOMAIN_ERRORS[kind]
    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": error.message,
            "details": json.loads(json.dumps(error.details, default=str)),
        }
    }


async def test_domain_error_details_are_json_encoded(app: FastAPI) -> None:
    async with client(app) as http_client:
        response = await http_client.get("/probe/domain-error/conflict")

    assert response.json()["error"]["details"] == {
        "site_id": str(SITE_ID),
        "code": "basil-growbox",
    }


async def test_domain_error_without_details_reports_an_empty_object(app: FastAPI) -> None:
    async with client(app) as http_client:
        response = await http_client.get("/probe/domain-error/bare")

    assert response.json()["error"]["details"] == {}


def test_domain_exceptions_do_not_depend_on_fastapi() -> None:
    import ai_greenhouse.core.exceptions as exceptions_module

    source = exceptions_module.__file__
    assert source is not None
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    top_level = {name.split(".", maxsplit=1)[0] for name in imported}
    assert not top_level & {"fastapi", "starlette"}

    for error_class in (NotFoundError, ConflictError, ReferenceError, ImmutableFieldError):
        assert issubclass(error_class, DomainError)
        assert issubclass(error_class, Exception)


async def test_validation_failure_uses_the_same_envelope(app: FastAPI) -> None:
    async with client(app) as http_client:
        response = await http_client.post("/probe/validate", json={"code": "Basil_Growbox"})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == VALIDATION_ERROR_CODE
    assert body["error"]["message"]
    assert body["error"]["details"]["errors"][0]["location"] == ["body", "code"]
    assert "type" in body["error"]["details"]["errors"][0]


async def test_validation_failure_does_not_echo_the_submitted_value(app: FastAPI) -> None:
    async with client(app) as http_client:
        response = await http_client.post("/probe/validate", json={"code": "Top-Secret-Value"})

    assert response.status_code == 422
    assert "Top-Secret-Value" not in response.text


async def test_valid_payload_is_accepted(app: FastAPI) -> None:
    async with client(app) as http_client:
        response = await http_client.post("/probe/validate", json={"code": "basil-growbox"})

    assert response.status_code == 200
    assert response.json() == {"code": "basil-growbox"}


async def test_unhandled_exception_is_generic_and_fully_logged(
    app: FastAPI,
    settings: Settings,
) -> None:
    log_output = StringIO()
    configure_logging(settings, stream=log_output)

    async with client(app, raise_app_exceptions=False) as http_client:
        response = await http_client.get("/probe/unhandled")

    rendered_logs = log_output.getvalue()
    log_records = [json.loads(line) for line in rendered_logs.splitlines() if line.strip()]

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": INTERNAL_ERROR_CODE,
            "message": INTERNAL_ERROR_MESSAGE,
            "details": {},
        }
    }
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    assert FAKE_DATABASE_URL not in response.text
    assert "top-secret" not in response.text
    assert "test-db" not in response.text

    failure_record = next(
        record for record in log_records if record["event"] == "unhandled_exception"
    )
    assert failure_record["exception_type"] == "RuntimeError"
    assert failure_record["path"] == "/probe/unhandled"
    assert "Traceback" in failure_record["exception"]
    assert "RuntimeError" in failure_record["exception"]
    assert "[REDACTED]" in failure_record["exception"]
    assert FAKE_DATABASE_URL not in rendered_logs
    assert "top-secret" not in rendered_logs
