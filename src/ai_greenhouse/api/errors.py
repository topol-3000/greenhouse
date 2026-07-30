"""Translation of failures into the single error envelope used by the API.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": {}}}

Responses never carry SQL, stack traces, driver messages, credentials or the
database URL. Technical context is written to the structured log instead.
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_greenhouse.core.exceptions import DomainError
from ai_greenhouse.edge.exceptions import EdgeContractError
from ai_greenhouse.edge.schemas import CONTRACT_VERSION

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

VALIDATION_ERROR_CODE: str = "validation_error"
VALIDATION_ERROR_MESSAGE: str = "Request validation failed"
INTERNAL_ERROR_CODE: str = "internal_error"
INTERNAL_ERROR_MESSAGE: str = "Internal server error"


def error_body(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the error envelope.

    Args:
        code: Machine-readable error code.
        message: Human-readable description of the failure.
        details: Structured context safe to expose to API clients.

    Returns:
        The ``{"error": {...}}`` envelope shared by every error response.
    """
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    """Report a domain failure with the status declared by the exception.

    Args:
        request: The request being handled, used for log context.
        exc: The raised domain failure.

    Returns:
        A ``JSONResponse`` carrying the error envelope and ``exc.http_status``.
    """
    logger.info(
        "domain_error",
        error_code=exc.code,
        http_status=exc.http_status,
        method=request.method,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=error_body(exc.code, exc.message, jsonable_encoder(exc.details)),
    )


async def handle_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Report a request that Pydantic rejected.

    Only the location, message and type of each failure are echoed. The
    submitted input is left out so a malformed body cannot be reflected back.

    Args:
        request: The request being handled, used for log context.
        exc: The validation failure raised by FastAPI/Pydantic.

    Returns:
        A ``JSONResponse`` with HTTP 422 and ``error.code = "validation_error"``.
    """
    if request.url.path.startswith("/api/v1/edge/"):
        failures: list[dict[str, Any]] = [
            {
                "location": [
                    part for part in failure["loc"] if part not in {"body", "path", "query"}
                ],
                "message": failure["msg"][:500],
            }
            for failure in exc.errors()
        ]
        for failure in failures:
            if not failure["location"]:
                failure["location"] = ["request"]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "contract_version": CONTRACT_VERSION,
                "error": {
                    "code": VALIDATION_ERROR_CODE,
                    "message": "The request is invalid.",
                    "details": failures,
                },
            },
        )

    failures = [
        {
            "location": [str(part) for part in failure["loc"]],
            "message": failure["msg"],
            "type": failure["type"],
        }
        for failure in exc.errors()
    ]
    logger.info(
        "request_validation_error",
        method=request.method,
        path=request.url.path,
        failure_count=len(failures),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body(
            VALIDATION_ERROR_CODE,
            VALIDATION_ERROR_MESSAGE,
            {"errors": failures},
        ),
    )


async def handle_edge_contract_error(
    request: Request,
    exc: EdgeContractError,
) -> JSONResponse:
    """Return the closed v1 Edge error representation."""
    logger.info(
        "edge_contract_error",
        error_code=exc.code,
        http_status=exc.http_status,
        method=request.method,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "contract_version": CONTRACT_VERSION,
            "error": {"code": exc.code, "message": exc.message},
        },
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Report a defect as a generic 500 and log the exception in full.

    Args:
        request: The request being handled, used for log context.
        exc: The unhandled exception. Logged in full server-side; never
            reflected into the response body.

    Returns:
        A ``JSONResponse`` with HTTP 500 and a generic message.
    """
    logger.exception(
        "unhandled_exception",
        method=request.method,
        path=request.url.path,
        exception_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_body(INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE),
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Install the handlers that produce the error envelope.

    Args:
        application: The FastAPI app to register handlers on.
    """
    application.add_exception_handler(DomainError, handle_domain_error)
    application.add_exception_handler(EdgeContractError, handle_edge_contract_error)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_exception_handler(Exception, handle_unexpected_error)
