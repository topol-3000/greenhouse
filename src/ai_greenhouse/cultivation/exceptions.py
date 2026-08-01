"""Domain failures raised by the cultivation module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.

Failures about a *referenced* facility, zone, recipe version or control loop are
not redeclared here. The missing entity belongs to the topology, agronomy or
control module, so ``ParentFacilityNotFoundError``,
``ControlZoneNotFoundError``, ``RecipeVersionNotFoundError`` and
``ControlLoopNotFoundError`` are reused: a stale identifier needs the same
answer whichever endpoint it was sent to.

Two conventions from elsewhere in the project are followed rather than
re-invented:

- a resource archived underneath an operation is reported with the shared
  ``parent_archived`` code, exactly as an archived site or facility is;
- a stored recipe graph that contradicts itself is reported with agronomy's
  ``invalid_recipe_version`` and a ``details.reason``, which is the code that
  already covers "this is not a version anything can be grown against".
"""

from uuid import UUID

from ai_greenhouse.core.exceptions import ConflictError, NotFoundError, ParentArchivedError

__all__ = [
    "GrowCycleCodeExistsError",
    "GrowCycleLoopUnavailableError",
    "GrowCycleNotFoundError",
    "GrowCycleResourceArchivedError",
    "GrowCycleTargetConflictError",
    "GrowStageInstanceNotFoundError",
    "InvalidGrowCycleTransitionError",
    "InvalidGrowCycleZoneError",
    "RuntimeTargetNotFoundError",
]


class GrowCycleResourceArchivedError(ParentArchivedError):
    """A resource the cycle depends on is no longer active.

    Reported with the shared ``parent_archived`` code rather than a code per
    resource: the reason for the refusal is the same one in every case — the
    crop, the recipe, the facility or the zone was retired — and the details say
    which of them it was.

    Only activation raises this. A planned cycle may be created beside a
    resource that is archived afterwards; what may not happen is starting to
    grow against one.
    """

    def __init__(self, resource: str, resource_id: UUID) -> None:
        """Report the archived resource.

        Args:
            resource: Which resource was retired, for example ``crop``.
            resource_id: Its identifier.
        """
        super().__init__(
            "A resource this grow cycle depends on is archived",
            details={"resource": resource, "resource_id": str(resource_id)},
        )


class GrowCycleNotFoundError(NotFoundError):
    """No grow cycle exists with the requested identifier."""

    code = "grow_cycle_not_found"

    def __init__(self, grow_cycle_id: UUID) -> None:
        """Report the missing cycle.

        Args:
            grow_cycle_id: The identifier that could not be resolved.
        """
        super().__init__("Grow cycle not found", details={"grow_cycle_id": str(grow_cycle_id)})


class GrowCycleCodeExistsError(ConflictError):
    """Another grow cycle already uses the requested code."""

    code = "grow_cycle_code_exists"

    def __init__(self, grow_cycle_code: str) -> None:
        """Report the duplicate code.

        Args:
            grow_cycle_code: The code that is already taken.
        """
        super().__init__("Grow cycle code already exists", details={"code": grow_cycle_code})


class InvalidGrowCycleZoneError(ConflictError):
    """The selected zone cannot carry the cycle's climate assignment.

    Reported as 409 rather than 422: the identifier resolves, and what the
    request contradicts is where that zone sits in the topology.
    """

    code = "invalid_grow_cycle_zone"

    def __init__(self, control_zone_id: UUID, facility_id: UUID, reason: str) -> None:
        """Report the unusable zone.

        Args:
            control_zone_id: The zone named by the request.
            facility_id: The facility the cycle runs in.
            reason: Stable machine-readable cause, safe to expose.
        """
        super().__init__(
            "Control zone is not valid for this grow cycle",
            details={
                "control_zone_id": str(control_zone_id),
                "facility_id": str(facility_id),
                "reason": reason,
            },
        )


class GrowCycleLoopUnavailableError(ConflictError):
    """No single compatible control loop could be resolved for the cycle's zone.

    One failure covers every reason the resolution can fail — no loop at all,
    more than one, a loop that measures something other than air temperature,
    or one measuring in another unit — because a client needs the same thing in
    all of them: the zone, and why activation could not proceed. Both reach the
    caller in the details.
    """

    code = "grow_cycle_loop_unavailable"

    def __init__(self, control_zone_id: UUID, reason: str) -> None:
        """Report the unusable loop resolution.

        Args:
            control_zone_id: The climate zone whose loop was looked for.
            reason: Stable machine-readable cause, safe to expose.
        """
        super().__init__(
            "No compatible control loop is available for this grow cycle",
            details={"control_zone_id": str(control_zone_id), "reason": reason},
        )


class GrowCycleTargetConflictError(ConflictError):
    """Another cycle already drives the control loop this activation resolved to.

    Raised both by the pre-check and by the partial unique index behind it. The
    index is the authority: two concurrent activations resolving to one loop
    both pass any application check, and only one of them can insert an active
    target.
    """

    code = "grow_cycle_target_conflict"

    def __init__(self, control_loop_id: UUID) -> None:
        """Report the loop that is already driven.

        Args:
            control_loop_id: The loop that already has an active runtime target.
        """
        super().__init__(
            "The control loop already has an active runtime target",
            details={"control_loop_id": str(control_loop_id)},
        )


class InvalidGrowCycleTransitionError(ConflictError):
    """The requested lifecycle transition is not one the cycle allows.

    Repeating a transition a cycle has already made is *not* reported here: it
    is idempotent and answers with the current representation. This is the
    answer to a transition that has no meaning at all, such as completing a
    cycle that was never started or aborting one that finished.
    """

    code = "invalid_grow_cycle_transition"

    def __init__(self, grow_cycle_id: UUID, status: str, transition: str) -> None:
        """Report the refused transition.

        Args:
            grow_cycle_id: The cycle the transition was requested on.
            status: The status it is actually in.
            transition: The requested transition, such as ``complete``.
        """
        super().__init__(
            "The grow cycle cannot make this lifecycle transition",
            details={
                "grow_cycle_id": str(grow_cycle_id),
                "status": status,
                "transition": transition,
            },
        )


class GrowStageInstanceNotFoundError(NotFoundError):
    """An active cycle carries no open stage instance.

    Not reachable through the API: activation creates the instance in the same
    transaction that makes the cycle active. It is reported rather than assumed
    away so a graph broken by a direct database edit surfaces as a precise
    failure instead of a crash, exactly as a lost recipe version does.
    """

    code = "grow_stage_instance_not_found"

    def __init__(self, grow_cycle_id: UUID) -> None:
        """Report the missing stage instance.

        Args:
            grow_cycle_id: The cycle that should have carried one.
        """
        super().__init__(
            "Grow stage instance not found",
            details={"grow_cycle_id": str(grow_cycle_id)},
        )


class RuntimeTargetNotFoundError(NotFoundError):
    """No runtime target exists with the requested identifier.

    Also raised when an active cycle carries no active target, which the atomic
    activation path rules out, for the same reason as
    :class:`GrowStageInstanceNotFoundError`.
    """

    code = "runtime_target_not_found"

    def __init__(self, runtime_target_id: UUID | None = None, **details: str) -> None:
        """Report the missing target.

        Args:
            runtime_target_id: The identifier that could not be resolved, when
                the caller supplied one.
            **details: Further safe context, such as the cycle that should have
                carried an active target.
        """
        super().__init__(
            "Runtime target not found",
            details={"runtime_target_id": str(runtime_target_id), **details}
            if runtime_target_id is not None
            else details,
        )
