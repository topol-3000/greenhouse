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
    "ReferencedEntityNotFoundError",
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


class ReferencedEntityNotFoundError(NotFoundError):
    """An entity named by the request body or query does not exist.

    Reported as 404, exactly like an entity missing from the path: where the
    identifier was written is a detail of the request, and a client that sent a
    stale identifier needs the same answer either way. Kept as its own class so
    the two origins stay distinguishable in the code that raises them.

    The name avoids the builtin ``ReferenceError``. Nothing in this project may
    shadow a builtin exception: a bare ``except ReferenceError`` elsewhere would
    then catch a case it never meant to.
    """

    code = "reference_not_found"


class ImmutableFieldError(ConflictError):
    """A field that is fixed at creation time was modified.

    Every such refusal in the API reports this one code and names the fields in
    ``details["fields"]``. A caller that wants to know which field was refused
    reads the details; it never has to know a per-entity code.
    """

    code = "immutable_field"


class ParentArchivedError(ConflictError):
    """A child entity was created inside a parent that is no longer active.

    Shared rather than declared per module: every entity below a site repeats
    the same rule, and every module reports it with the same ``error.code``.
    """

    code = "parent_archived"
