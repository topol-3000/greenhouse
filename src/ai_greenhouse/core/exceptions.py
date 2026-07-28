"""Domain failures, expressed without any dependency on the HTTP layer.

These exceptions must never inherit from ``HTTPException`` and must never
import FastAPI. Translation into a response is done centrally in
``ai_greenhouse.api.errors``.

``code`` and ``http_status`` are class-level defaults. Concrete modules pass a
specific ``code`` per situation, for example ``facility_code_conflict``.
"""

from collections.abc import Mapping
from typing import Any

__all__ = [
    "ConflictError",
    "DomainError",
    "ImmutableFieldError",
    "NotFoundError",
    "ParentArchivedError",
    "ReferenceError",
]


class DomainError(Exception):
    """Base class for failures caused by domain rules rather than by defects.

    Attributes:
        code: Machine-readable error code returned in the API envelope.
        http_status: HTTP status translated by ``ai_greenhouse.api.errors``.
        message: Human-readable description of the failure.
        details: Structured context safe to expose to API clients.
    """

    code: str = "domain_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Create a domain failure.

        Args:
            message: Human-readable description of the failure.
            code: Overrides the class-level ``code`` default, if given.
            details: Structured context merged into the error envelope.
        """
        super().__init__(message)
        self.message: str = message
        self.details: dict[str, Any] = dict(details or {})
        if code is not None:
            self.code = code


class NotFoundError(DomainError):
    """The requested entity does not exist."""

    code = "not_found"
    http_status = 404


class ConflictError(DomainError):
    """The request contradicts the current state of the system."""

    code = "conflict"
    http_status = 409


class ReferenceError(DomainError):
    """A referenced parent entity does not exist.

    This deliberately shadows the builtin ``ReferenceError`` inside this module,
    as named by the story. Import it explicitly rather than relying on the
    builtin name being free.

    Reported as 422 rather than 404: the missing entity is part of the request
    body, so the request itself is unprocessable.
    """

    code = "reference_not_found"
    http_status = 422


class ImmutableFieldError(ConflictError):
    """A field that is fixed at creation time was modified."""

    code = "immutable_field"


class ParentArchivedError(ConflictError):
    """A child entity was created inside a parent that is no longer active.

    Shared rather than declared per module: every entity below a site repeats
    the same rule, and every module reports it with the same ``error.code``.
    """

    code = "parent_archived"
