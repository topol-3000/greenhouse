import logging
import sys
from time import perf_counter
from typing import TextIO

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.types import EventDict, Processor

from ai_greenhouse.core.config import Settings


def _static_context(settings: Settings) -> Processor:
    def add_static_context(
        _: object,
        __: str,
        event_dict: EventDict,
    ) -> EventDict:
        event_dict.setdefault("service", settings.app_name)
        event_dict.setdefault("environment", settings.app_env)
        return event_dict

    return add_static_context


def _secret_scrubber(settings: Settings) -> Processor:
    database_url = settings.database_url_value()
    secrets = {database_url}
    if "://" in database_url and "@" in database_url:
        credentials = database_url.split("://", maxsplit=1)[1].split("@", maxsplit=1)[0]
        if ":" in credentials:
            secrets.add(credentials.split(":", maxsplit=1)[1])
    secrets.discard("")

    def scrub(value: object) -> object:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return tuple(scrub(item) for item in value)
        return value

    def redact_secrets(
        _: object,
        __: str,
        event_dict: EventDict,
    ) -> EventDict:
        return scrub(event_dict)  # type: ignore[return-value]

    return redact_secrets


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    """Configure structlog and standard-library loggers as JSON."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp")
    static_context = _static_context(settings)
    secret_scrubber = _secret_scrubber(settings)
    renderer = structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            timestamper,
            static_context,
            secret_scrubber,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "alembic"):
        configured_logger = logging.getLogger(logger_name)
        configured_logger.handlers.clear()
        configured_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            static_context,
            secret_scrubber,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started_at = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            structlog.get_logger("http").info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
            )
