"""Domain failures owned by gateway configuration."""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError


class GatewayNotFoundError(NotFoundError):
    """The requested gateway identity is unknown."""

    code = "gateway_not_found"

    def __init__(self, gateway_id: UUID) -> None:
        """Report the unknown identity."""
        super().__init__("Gateway not found", details={"gateway_id": str(gateway_id)})


class GatewayCodeNotFoundError(NotFoundError):
    """No gateway uses the requested stable provisioning code."""

    code = "gateway_not_found"

    def __init__(self, code: str) -> None:
        """Report the unresolved stable code."""
        super().__init__("Gateway not found", details={"code": code})


class GatewayInactiveError(ConflictError):
    """The requested gateway has been archived."""

    code = "gateway_inactive"

    def __init__(self, gateway_id: UUID) -> None:
        """Report the inactive identity."""
        super().__init__("Gateway is inactive", details={"gateway_id": str(gateway_id)})


class GatewayConfigurationConflictError(ConflictError):
    """A stable code is already bound to incompatible functional configuration."""

    code = "gateway_configuration_conflict"

    def __init__(
        self,
        *,
        gateway_id: UUID,
        code: str,
        conflicts: list[dict[str, str]],
    ) -> None:
        """Report every field that differs from the requested configuration."""
        super().__init__(
            "Gateway code already exists with incompatible configuration",
            details={
                "gateway_id": str(gateway_id),
                "code": code,
                "conflicts": conflicts,
            },
        )


class GatewaySiteInactiveError(ConflictError):
    """A gateway cannot be provisioned or extended in an archived site."""

    code = "gateway_site_inactive"

    def __init__(self, site_id: UUID) -> None:
        """Report the inactive owning site."""
        super().__init__(
            "Gateway site is inactive",
            details={"site_id": str(site_id)},
        )


class GatewayPointConflictError(ConflictError):
    """A point cannot be assigned to the requested gateway configuration."""

    code = "gateway_point_conflict"

    def __init__(
        self,
        point_id: UUID,
        reason: str,
        *,
        gateway_id: UUID | None = None,
        conflicting_gateway_id: UUID | None = None,
    ) -> None:
        """Report the rejected point without physical addressing detail."""
        details = {"point_id": str(point_id), "reason": reason}
        if gateway_id is not None:
            details["gateway_id"] = str(gateway_id)
        if conflicting_gateway_id is not None:
            details["conflicting_gateway_id"] = str(conflicting_gateway_id)
        super().__init__(
            "Point cannot be authorized to the gateway",
            details=details,
        )
