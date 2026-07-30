"""Safe failures defined by the public Edge contract."""


class EdgeContractError(Exception):
    """Base failure carrying an exact contract code and HTTP status."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        """Create a safe public failure."""
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class EdgeValidationError(EdgeContractError):
    """Semantic validation that needs database context."""

    def __init__(self, message: str = "The request is invalid.") -> None:
        """Create a validation failure."""
        super().__init__("validation_error", message, 422)


def gateway_not_found() -> EdgeContractError:
    """Return the non-enumerating missing gateway failure."""
    return EdgeContractError("gateway_not_found", "Gateway not found.", 404)


def gateway_inactive() -> EdgeContractError:
    """Return the inactive gateway failure."""
    return EdgeContractError("gateway_inactive", "Gateway is inactive.", 409)


def point_not_found() -> EdgeContractError:
    """Return the missing point failure."""
    return EdgeContractError("point_not_found", "Point not found.", 404)


def point_inactive() -> EdgeContractError:
    """Return the inactive point failure."""
    return EdgeContractError("point_inactive", "Point is inactive.", 409)


def gateway_point_forbidden() -> EdgeContractError:
    """Return the generic authorization/topology failure."""
    return EdgeContractError(
        "gateway_point_forbidden",
        "The logical point is not authorized to this gateway.",
        403,
    )


def telemetry_message_conflict() -> EdgeContractError:
    """Return producer idempotency-key conflict."""
    return EdgeContractError(
        "telemetry_message_conflict",
        "The telemetry message identity was reused with different content.",
        409,
    )


def command_not_found() -> EdgeContractError:
    """Return a command miss without revealing cross-gateway existence."""
    return EdgeContractError("command_not_found", "Command not found.", 404)


def command_not_pending() -> EdgeContractError:
    """Return a command that cannot accept its first terminal result."""
    return EdgeContractError(
        "command_not_pending",
        "Command is not pending.",
        409,
    )


def command_acknowledgement_conflict() -> EdgeContractError:
    """Return a terminal representation conflict."""
    return EdgeContractError(
        "command_acknowledgement_conflict",
        "The stored command acknowledgement differs from this request.",
        409,
    )
