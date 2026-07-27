"""Domain failures raised by the topology module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.
"""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError

__all__ = ["SiteCodeConflictError", "SiteNotFoundError"]


class SiteNotFoundError(NotFoundError):
    """No site exists with the requested identifier."""

    code = "site_not_found"

    def __init__(self, site_id: UUID) -> None:
        """Report the missing site.

        Args:
            site_id: The identifier that could not be resolved.
        """
        super().__init__("Site not found", details={"site_id": str(site_id)})


class SiteCodeConflictError(ConflictError):
    """Another site already uses the requested code."""

    code = "site_code_conflict"

    def __init__(self, site_code: str) -> None:
        """Report the duplicate code.

        Args:
            site_code: The code that is already taken.
        """
        super().__init__("Site code already exists", details={"code": site_code})
