"""Domain failures owned by gateway configuration."""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError


class GatewayNotFoundError(NotFoundError):
    """The requested gateway identity is unknown."""

    code = "gateway_not_found"

    def __init__(self, gateway_id: UUID) -> None:
        """Report the unknown identity."""
        super().__init__("Gateway not found", details={"gateway_id": str(gateway_id)})


class GatewayInactiveError(ConflictError):
    """The requested gateway has been archived."""

    code = "gateway_inactive"

    def __init__(self, gateway_id: UUID) -> None:
        """Report the inactive identity."""
        super().__init__("Gateway is inactive", details={"gateway_id": str(gateway_id)})


class GatewayPointConflictError(ConflictError):
    """A point cannot be assigned to the requested gateway configuration."""

    code = "gateway_point_conflict"

    def __init__(self, point_id: UUID, reason: str) -> None:
        """Report the rejected point without physical addressing detail."""
        super().__init__(
            "Point cannot be authorized to the gateway",
            details={"point_id": str(point_id), "reason": reason},
        )
