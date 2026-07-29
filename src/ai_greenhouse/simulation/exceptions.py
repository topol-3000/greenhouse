"""Domain failures raised by the simulation module."""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError


class SimulationRunNotFoundError(NotFoundError):
    """No simulation run exists with the requested identifier."""

    code = "simulation_run_not_found"

    def __init__(self, run_id: UUID) -> None:
        super().__init__("Simulation run not found", details={"run_id": str(run_id)})


class SimulationZoneNotFoundError(NotFoundError):
    """No control zone exists with the identifier submitted for a run."""

    code = "control_zone_not_found"

    def __init__(self, control_zone_id: UUID) -> None:
        super().__init__(
            "Control zone not found",
            details={"control_zone_id": str(control_zone_id)},
        )


class InvalidSimulationZoneError(ConflictError):
    """The selected zone cannot host the climate model."""

    code = "invalid_simulation_zone"

    def __init__(self, control_zone_id: UUID, reason: str) -> None:
        super().__init__(
            "Control zone is not valid for climate simulation",
            details={"control_zone_id": str(control_zone_id), "reason": reason},
        )


class SimulationAlreadyRunningError(ConflictError):
    """A zone already has a running simulation."""

    code = "simulation_already_running"

    def __init__(self, control_zone_id: UUID) -> None:
        super().__init__(
            "Control zone already has a running simulation",
            details={"control_zone_id": str(control_zone_id)},
        )


class InvalidSimulationTransitionError(ConflictError):
    """A requested lifecycle transition is not valid for the current state."""

    code = "invalid_simulation_transition"

    def __init__(self, run_id: UUID, status: str, transition: str) -> None:
        super().__init__(
            "Simulation lifecycle transition is not allowed",
            details={
                "run_id": str(run_id),
                "status": status,
                "transition": transition,
            },
        )


class InvalidSimulationProgressError(ConflictError):
    """A step attempted to leave virtual time unchanged or move it backwards."""

    code = "invalid_simulation_progress"

    def __init__(self, run_id: UUID) -> None:
        super().__init__(
            "Simulation virtual time must move forwards",
            details={"run_id": str(run_id)},
        )
