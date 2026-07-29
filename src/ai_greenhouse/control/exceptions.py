"""Domain failures raised by the control module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.

Failures about a *referenced* control zone or point are not redeclared here.
The missing entity belongs to the topology or points module, so
``ai_greenhouse.topology.exceptions.ControlZoneNotFoundError`` and
``ai_greenhouse.points.exceptions.ReferencedPointNotFoundError`` are reused.
"""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError

__all__ = [
    "ControlLoopExistsError",
    "ControlLoopNotFoundError",
    "InvalidControlLoopPointError",
    "InvalidControlLoopZoneError",
]


class ControlLoopNotFoundError(NotFoundError):
    """No control loop exists with the requested identifier."""

    code = "control_loop_not_found"

    def __init__(self, control_loop_id: UUID) -> None:
        """Report the missing loop.

        Args:
            control_loop_id: The identifier that could not be resolved.
        """
        super().__init__(
            "Control loop not found",
            details={"control_loop_id": str(control_loop_id)},
        )


class ControlLoopExistsError(ConflictError):
    """The control zone already has a loop.

    M3 allows one loop per zone. Replacing a loop would silently change the
    configuration a command already applied was decided under, so a second one
    is refused rather than accepted alongside the first.
    """

    code = "control_loop_exists"

    def __init__(self, control_zone_id: UUID) -> None:
        """Report the zone that is already automated.

        Args:
            control_zone_id: The zone that already has a loop.
        """
        super().__init__(
            "Control zone already has a control loop",
            details={"control_zone_id": str(control_zone_id)},
        )


class InvalidControlLoopZoneError(ConflictError):
    """The selected zone cannot host a hysteresis loop.

    Reported as 409 rather than 422: the identifier resolves, and what the
    request contradicts is the state of the zone it names.
    """

    code = "invalid_control_loop_zone"

    def __init__(self, control_zone_id: UUID, reason: str) -> None:
        """Report the unusable zone.

        Args:
            control_zone_id: The zone named by the request.
            reason: Stable machine-readable cause, safe to expose.
        """
        super().__init__(
            "Control zone is not valid for a hysteresis control loop",
            details={"control_zone_id": str(control_zone_id), "reason": reason},
        )


class InvalidControlLoopPointError(ConflictError):
    """A point named by the request cannot play the role it was given.

    One failure covers every point rule — archived, assigned to another zone,
    wrong kind, wrong data type, wrong metric or wrong unit — because a client
    needs the same thing in all of them: which of the three roles was refused,
    and why. Both reach the caller in the details.
    """

    code = "invalid_control_loop_point"

    def __init__(self, role: str, point_id: UUID, reason: str) -> None:
        """Report the rejected point.

        Args:
            role: Which loop role the point was submitted for, for example
                ``measurement_point_id``.
            point_id: The rejected point.
            reason: Stable machine-readable cause, safe to expose.
        """
        super().__init__(
            "Point is not valid for this control loop role",
            details={"role": role, "point_id": str(point_id), "reason": reason},
        )
