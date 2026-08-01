"""Grow cycle business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.cultivation.exceptions` and, for a missing referenced
facility, zone or recipe version, the ones owned by the topology and agronomy
modules, which ``ai_greenhouse.api.errors`` turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or rollback
belongs to the ``get_session`` request dependency, and that is also what makes
every operation here atomic. Activation writes three rows and updates a fourth;
a failure at any of them leaves the request rolled back, so a cycle that is
``active`` without its stage instance or without its runtime target is not a
state this module has to handle — it is a state it cannot produce.

Concurrency is the database's job, not the application's. Two guarantees matter
and neither is a Python lock:

- one transition at a time per cycle, through the row lock taken by
  ``lock_by_id``;
- one active runtime target per control loop, through the partial unique index
  on ``runtime_targets``. The pre-check below only improves the error message;
  the index is what makes the rule true across processes.

What activation does *not* do is as fixed as what it does. It reads no
telemetry, evaluates no current state and creates no ``Command``. The runtime
target it writes is persisted and readable and nothing consumes it: the control
loop still decides on its own thresholds.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.exceptions import (
    InvalidRecipeVersionError,
    RecipeVersionNotFoundError,
)
from ai_greenhouse.agronomy.models import (
    Crop,
    GrowingRecipe,
    RecipeStage,
    RecipeVersion,
    RecipeVersionStatus,
    RequirementKind,
    TargetRequirement,
)
from ai_greenhouse.agronomy.service import SUPPORTED_STAGE_CODE
from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.control.models import ControlLoop, ControlPolicyType
from ai_greenhouse.cultivation.exceptions import (
    GrowCycleCodeExistsError,
    GrowCycleLoopUnavailableError,
    GrowCycleNotFoundError,
    GrowCycleResourceArchivedError,
    GrowCycleTargetConflictError,
    GrowStageInstanceNotFoundError,
    InvalidGrowCycleTransitionError,
    InvalidGrowCycleZoneError,
    RuntimeTargetNotFoundError,
)
from ai_greenhouse.cultivation.models import (
    RUNTIME_TARGET_UNIT,
    GrowCycle,
    GrowCycleStatus,
    GrowCycleZoneAssignment,
    GrowCycleZoneRole,
    GrowStageInstance,
    RuntimeTarget,
    RuntimeTargetMetric,
)
from ai_greenhouse.cultivation.repository import GrowCycleRepository, RuntimeTargetRepository
from ai_greenhouse.cultivation.schemas import GrowCycleCreate, GrowCycleRead
from ai_greenhouse.infrastructure.database.base import StatusEnum, utc_now
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.exceptions import ControlZoneNotFoundError, ParentFacilityNotFoundError
from ai_greenhouse.topology.models import ControlZone, Facility, ZoneType

TEMPERATURE_METRIC: str = RuntimeTargetMetric.AIR_TEMPERATURE.value
"""The one metric M5 materializes into a runtime target.

Humidity and photoperiod stay display-only properties of the recipe version:
nothing in the cloud controls either, so a target for them would be a number no
loop could act on.
"""

TERMINAL_SOURCE_STATUSES: dict[GrowCycleStatus, frozenset[GrowCycleStatus]] = {
    GrowCycleStatus.COMPLETED: frozenset({GrowCycleStatus.ACTIVE}),
    GrowCycleStatus.ABORTED: frozenset({GrowCycleStatus.ACTIVE, GrowCycleStatus.PLANNED}),
}
"""The complete lifecycle, as the states each terminal transition accepts.

Declared as a table rather than as conditionals because it *is* the contract:
completion needs a cycle that actually ran, abort accepts one that never did,
and every other transition — reactivating, completing a planned cycle, pausing —
is absent because it does not exist. Repeating a transition already made is not
in the table either; that case is answered before it is consulted.
"""


def select_temperature_requirement(
    requirements: list[TargetRequirement],
    recipe_stage_id: UUID,
) -> TargetRequirement:
    """Pick the one temperature band a stage can be grown against.

    Every condition is checked rather than trusted to the catalog's own
    constraints, because this is the moment the numbers stop being a description
    and start driving a zone. A stage carrying no usable band is reported as an
    invalid version, which is the code agronomy already uses for a graph nothing
    can be grown against.

    Args:
        requirements: Every requirement of the stage.
        recipe_stage_id: The stage they belong to, reported on failure.

    Returns:
        The single ``air_temperature`` range requirement, in ``°C``, with finite
        ordered bounds.

    Raises:
        InvalidRecipeVersionError: If no such requirement exists, if more than
            one does, or if the one that does is not a usable band.
    """
    candidates: list[TargetRequirement] = [
        requirement for requirement in requirements if requirement.metric_type == TEMPERATURE_METRIC
    ]
    if not candidates:
        raise InvalidRecipeVersionError(
            "missing_temperature_requirement",
            recipe_stage_id=str(recipe_stage_id),
            metric_type=TEMPERATURE_METRIC,
        )
    if len(candidates) > 1:
        raise InvalidRecipeVersionError(
            "ambiguous_temperature_requirement",
            recipe_stage_id=str(recipe_stage_id),
            metric_type=TEMPERATURE_METRIC,
        )

    requirement: TargetRequirement = candidates[0]
    if requirement.requirement_kind is not RequirementKind.RANGE:
        raise InvalidRecipeVersionError(
            "unsupported_requirement_kind",
            metric_type=TEMPERATURE_METRIC,
            requirement_kind=requirement.requirement_kind.value,
            supported_requirement_kind=RequirementKind.RANGE.value,
        )
    if requirement.unit != RUNTIME_TARGET_UNIT:
        raise InvalidRecipeVersionError(
            "unsupported_unit",
            metric_type=TEMPERATURE_METRIC,
            unit=requirement.unit,
            supported_unit=RUNTIME_TARGET_UNIT,
        )

    lower: Decimal | None = requirement.min_value
    upper: Decimal | None = requirement.max_value
    if lower is None or upper is None:
        raise InvalidRecipeVersionError("missing_range_bounds", metric_type=TEMPERATURE_METRIC)
    if not (lower.is_finite() and upper.is_finite()) or lower >= upper:
        raise InvalidRecipeVersionError(
            "invalid_range",
            metric_type=TEMPERATURE_METRIC,
            min_value=str(lower),
            max_value=str(upper),
        )
    return requirement


def snapshot_values(requirement: TargetRequirement) -> tuple[Decimal, Decimal]:
    """Return the two bounds a runtime target copies from a requirement.

    A separate step so the copy is one expression rather than two attribute
    reads at the call site: what makes the snapshot meaningful is that both ends
    come from the same requirement, unchanged and un-rounded.

    Args:
        requirement: The band chosen by :func:`select_temperature_requirement`,
            whose bounds are therefore both present.

    Returns:
        The lower and upper bound, exactly as stored.

    Raises:
        InvalidRecipeVersionError: If a bound is missing, which the selection
            above rules out.
    """
    if requirement.min_value is None or requirement.max_value is None:
        raise InvalidRecipeVersionError("missing_range_bounds", metric_type=TEMPERATURE_METRIC)
    return requirement.min_value, requirement.max_value


def select_compatible_loop(
    loops: list[ControlLoop],
    measurement_points: dict[UUID, Point],
    control_zone_id: UUID,
) -> ControlLoop:
    """Pick the one control loop a cycle's temperature target may drive.

    "Compatible" is not broadened here and ``hysteresis-v1`` is not redesigned:
    a loop already satisfies every rule the control module applied when it was
    configured, and what this adds is only what a *target* needs — that it is
    the single loop of the zone, that it evaluates the policy the band is
    expressed for, and that the point it measures really carries air temperature
    in the unit the band uses.

    The zone's own unique constraint already makes more than one loop
    impossible, so the ambiguity branch is defensive. It is kept because the
    rule it states — a target addresses exactly one loop — must not become
    silently untrue if that constraint is ever widened.

    Args:
        loops: Every loop configured for the zone.
        measurement_points: The loops' measurement points, keyed by identifier.
        control_zone_id: The zone the loops belong to, reported on failure.

    Returns:
        The single compatible loop.

    Raises:
        GrowCycleLoopUnavailableError: If no loop exists, if more than one does,
            or if the one that does cannot carry a temperature target.
    """
    if not loops:
        raise GrowCycleLoopUnavailableError(control_zone_id, "no_control_loop")
    if len(loops) > 1:
        raise GrowCycleLoopUnavailableError(control_zone_id, "ambiguous_control_loop")

    loop: ControlLoop = loops[0]
    if loop.policy_type is not ControlPolicyType.HYSTERESIS_V1:
        raise GrowCycleLoopUnavailableError(control_zone_id, "unsupported_policy_type")

    point: Point | None = measurement_points.get(loop.measurement_point_id)
    if point is None:
        raise GrowCycleLoopUnavailableError(control_zone_id, "measurement_point_missing")
    if point.metric_type != TEMPERATURE_METRIC:
        raise GrowCycleLoopUnavailableError(control_zone_id, "measurement_metric_mismatch")
    if point.unit != RUNTIME_TARGET_UNIT:
        raise GrowCycleLoopUnavailableError(control_zone_id, "measurement_unit_mismatch")
    return loop


class GrowCycleService:
    """Applies the grow cycle invariants over a single request's session.

    Creating a cycle creates its whole placement. A cycle without its climate
    zone is a cycle nothing could ever be activated for, so the assignment is
    written in the same transaction and not by a second call.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repository around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: GrowCycleRepository = GrowCycleRepository(session)

    async def create_cycle(self, payload: GrowCycleCreate) -> GrowCycleRead:
        """Register a planned cycle and place it in exactly one climate zone.

        Nothing operational is decided here. No control loop is looked up or
        reserved, no stage instance is created and no runtime target is
        materialized: which loop will carry the target is a question about the
        topology as it stands *at activation*, and answering it now would
        reserve a loop for a cycle that may never start.

        Args:
            payload: The validated request body.

        Returns:
            The complete created cycle, with no active runtime target.

        Raises:
            ParentFacilityNotFoundError: If the facility does not exist.
            ControlZoneNotFoundError: If the zone does not exist.
            RecipeVersionNotFoundError: If the version does not exist, or
                carries no stage.
            InvalidGrowCycleZoneError: If the zone is not a climate zone of the
                supplied facility.
            InvalidRecipeVersionError: If the version is not one a cycle can be
                grown against.
            GrowCycleCodeExistsError: If the code is already taken.
        """
        facility: Facility | None = await self._repository.get_facility(payload.facility_id)
        if facility is None:
            raise ParentFacilityNotFoundError(payload.facility_id)

        zone: ControlZone | None = await self._repository.get_control_zone(payload.climate_zone_id)
        if zone is None:
            raise ControlZoneNotFoundError(payload.climate_zone_id)
        self._check_zone_placement(zone, payload.facility_id)

        version: RecipeVersion | None = await self._repository.get_recipe_version(
            payload.recipe_version_id
        )
        if version is None:
            raise RecipeVersionNotFoundError(payload.recipe_version_id)
        stage: RecipeStage = await self._resolve_stage(version)

        if await self._repository.get_by_code(payload.code) is not None:
            raise GrowCycleCodeExistsError(payload.code)

        cycle = GrowCycle(
            code=payload.code,
            name=payload.name,
            facility_id=payload.facility_id,
            recipe_version_id=version.id,
            current_stage_id=stage.id,
            status=GrowCycleStatus.PLANNED,
            planned_start_at=payload.planned_start_at,
        )
        self._repository.add(cycle)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise GrowCycleCodeExistsError(payload.code) from error

        self._repository.add(
            GrowCycleZoneAssignment(
                grow_cycle_id=cycle.id,
                control_zone_id=zone.id,
                role=GrowCycleZoneRole.CLIMATE,
            )
        )
        await self._repository.flush()

        return GrowCycleRead.from_parts(cycle, zone.id, stage, None)

    async def get_cycle(self, grow_cycle_id: UUID) -> GrowCycleRead:
        """Read one cycle with its zone, its current stage and its active target.

        Args:
            grow_cycle_id: Identifier of the cycle to read.

        Returns:
            The complete current representation.

        Raises:
            GrowCycleNotFoundError: If no cycle has that identifier.
        """
        cycle: GrowCycle | None = await self._repository.get_by_id(grow_cycle_id)
        if cycle is None:
            raise GrowCycleNotFoundError(grow_cycle_id)
        return await self._describe(cycle)

    async def list_cycles(
        self,
        params: PageParams,
        *,
        code: str | None = None,
        facility_id: UUID | None = None,
        status: GrowCycleStatus | None = None,
    ) -> tuple[list[GrowCycleRead], int]:
        """Return one page of complete cycle representations with the unpaged total.

        The page costs three further statements whatever its size: the
        assignments, the stages and the active targets. An unknown filter value
        is not an error — a filter matching nothing yields an empty page.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the cycle with that exact code.
            facility_id: Restricts the result to the cycles of one facility.
            status: Restricts the result to the cycles in one lifecycle state.

        Returns:
            A tuple of the page's cycles and the total matching the filters.
        """
        cycles, total = await self._repository.list_page(
            params,
            code=code,
            facility_id=facility_id,
            status=status,
        )
        return await self._describe_many(cycles), total

    async def activate_cycle(self, grow_cycle_id: UUID) -> GrowCycleRead:
        """Start a planned cycle, its stage instance and its runtime target at once.

        The three rows share one server-assigned instant, so "when did this
        cycle start" has a single answer whichever of them is read. Repeating
        the call on an already-active cycle answers with the current
        representation and writes nothing, which is what makes a retried request
        safe.

        Args:
            grow_cycle_id: The cycle to activate.

        Returns:
            The complete representation of the now-active cycle.

        Raises:
            GrowCycleNotFoundError: If no cycle has that identifier.
            InvalidGrowCycleTransitionError: If the cycle is terminal.
            GrowCycleResourceArchivedError: If the crop, recipe, facility or
                zone has been retired.
            InvalidRecipeVersionError: If the stored recipe graph cannot be
                grown against.
            InvalidGrowCycleZoneError: If the cycle's zone is not a climate zone
                of its facility.
            GrowCycleLoopUnavailableError: If no single compatible control loop
                can be resolved.
            GrowCycleTargetConflictError: If another cycle already drives that
                loop.
        """
        cycle: GrowCycle | None = await self._repository.lock_by_id(grow_cycle_id)
        if cycle is None:
            raise GrowCycleNotFoundError(grow_cycle_id)

        zone: ControlZone = await self._load_assigned_zone(cycle)
        if cycle.status is GrowCycleStatus.ACTIVE:
            return await self._describe(cycle, control_zone=zone)
        if cycle.status is not GrowCycleStatus.PLANNED:
            raise InvalidGrowCycleTransitionError(cycle.id, cycle.status.value, "activate")

        stage, requirement = await self._load_agronomy(cycle)

        facility: Facility | None = await self._repository.get_facility(cycle.facility_id)
        if facility is None:
            raise ParentFacilityNotFoundError(cycle.facility_id)
        if facility.status is not StatusEnum.ACTIVE:
            raise GrowCycleResourceArchivedError("facility", facility.id)
        if zone.status is not StatusEnum.ACTIVE:
            raise GrowCycleResourceArchivedError("control_zone", zone.id)
        self._check_zone_placement(zone, cycle.facility_id)

        loop: ControlLoop = await self._resolve_loop(zone)
        control_loop_id: UUID = loop.id
        existing: RuntimeTarget | None = await self._repository.get_active_target_for_loop(
            control_loop_id
        )
        if existing is not None:
            raise GrowCycleTargetConflictError(control_loop_id)

        lower_value, upper_value = snapshot_values(requirement)
        activated_at: datetime = utc_now()
        cycle.status = GrowCycleStatus.ACTIVE
        cycle.started_at = activated_at
        self._repository.add(
            GrowStageInstance(
                grow_cycle_id=cycle.id,
                recipe_stage_id=stage.id,
                started_at=activated_at,
            )
        )
        target = RuntimeTarget(
            control_loop_id=control_loop_id,
            grow_cycle_id=cycle.id,
            target_requirement_id=requirement.id,
            metric_type=RuntimeTargetMetric.AIR_TEMPERATURE,
            lower_value=lower_value,
            upper_value=upper_value,
            unit=RUNTIME_TARGET_UNIT,
            effective_from=activated_at,
            created_at=activated_at,
        )
        self._repository.add(target)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            # Every identifier the failure reports is bound above, before the
            # flush. A failed flush expires the session's instances, so reading
            # ``loop.id`` here would try to reload it through a connection that
            # is no longer usable and raise ``PendingRollbackError`` instead of
            # the domain conflict the caller has to see.
            raise GrowCycleTargetConflictError(control_loop_id) from error

        return GrowCycleRead.from_parts(cycle, zone.id, stage, target)

    async def complete_cycle(self, grow_cycle_id: UUID) -> GrowCycleRead:
        """Finish an active cycle, closing its stage and its target with it.

        Args:
            grow_cycle_id: The cycle to complete.

        Returns:
            The complete representation of the finished cycle, whose active
            target is now ``null``.

        Raises:
            GrowCycleNotFoundError: If no cycle has that identifier.
            InvalidGrowCycleTransitionError: If the cycle is planned or aborted.
            GrowStageInstanceNotFoundError: If an active cycle carries no open
                stage instance, which activation rules out.
            RuntimeTargetNotFoundError: If an active cycle carries no active
                target, which activation rules out.
        """
        return await self._terminate(
            grow_cycle_id,
            transition="complete",
            terminal_status=GrowCycleStatus.COMPLETED,
        )

    async def abort_cycle(self, grow_cycle_id: UUID) -> GrowCycleRead:
        """Give up a cycle, whether it was ever started or not.

        A cycle aborted straight from ``planned`` gets no stage instance and no
        runtime target: it never ran, and manufacturing a closed stage merely to
        close it would put a stage in the history of a cycle that had none.

        Args:
            grow_cycle_id: The cycle to abort.

        Returns:
            The complete representation of the aborted cycle.

        Raises:
            GrowCycleNotFoundError: If no cycle has that identifier.
            InvalidGrowCycleTransitionError: If the cycle is completed.
            GrowStageInstanceNotFoundError: If an active cycle carries no open
                stage instance, which activation rules out.
            RuntimeTargetNotFoundError: If an active cycle carries no active
                target, which activation rules out.
        """
        return await self._terminate(
            grow_cycle_id,
            transition="abort",
            terminal_status=GrowCycleStatus.ABORTED,
        )

    async def _terminate(
        self,
        grow_cycle_id: UUID,
        *,
        transition: str,
        terminal_status: GrowCycleStatus,
    ) -> GrowCycleRead:
        """Move a cycle to a terminal state, closing whatever it opened.

        The cycle, its stage instance and its target are read before anything is
        written, so a cycle whose children are missing is refused with the
        transaction untouched rather than half-closed.

        Args:
            grow_cycle_id: The cycle to finish.
            transition: The requested transition, reported when it is refused.
            terminal_status: The state the cycle ends in.

        Returns:
            The complete representation of the terminal cycle.

        Raises:
            GrowCycleNotFoundError: If no cycle has that identifier.
            InvalidGrowCycleTransitionError: If the cycle is in a state this
                transition has no meaning for.
            GrowStageInstanceNotFoundError: If an active cycle carries no open
                stage instance.
            RuntimeTargetNotFoundError: If an active cycle carries no active
                target.
        """
        cycle: GrowCycle | None = await self._repository.lock_by_id(grow_cycle_id)
        if cycle is None:
            raise GrowCycleNotFoundError(grow_cycle_id)

        if cycle.status is terminal_status:
            return await self._describe(cycle)
        if cycle.status not in TERMINAL_SOURCE_STATUSES[terminal_status]:
            raise InvalidGrowCycleTransitionError(cycle.id, cycle.status.value, transition)

        stage_instance: GrowStageInstance | None = None
        target: RuntimeTarget | None = None
        if cycle.status is GrowCycleStatus.ACTIVE:
            stage_instance = await self._repository.get_open_stage_instance(cycle.id)
            if stage_instance is None:
                raise GrowStageInstanceNotFoundError(cycle.id)
            target = await self._repository.get_active_target_for_cycle(cycle.id)
            if target is None:
                raise RuntimeTargetNotFoundError(grow_cycle_id=str(cycle.id))

        ended_at: datetime = utc_now()
        cycle.status = terminal_status
        cycle.ended_at = ended_at
        if stage_instance is not None:
            stage_instance.ended_at = ended_at
        if target is not None:
            target.effective_to = ended_at
        await self._repository.flush()

        return await self._describe(cycle)

    async def _resolve_loop(self, zone: ControlZone) -> ControlLoop:
        """Find the single compatible control loop of a cycle's climate zone.

        Args:
            zone: The zone the cycle is assigned to.

        Returns:
            The loop the runtime target will address.

        Raises:
            GrowCycleLoopUnavailableError: If no single compatible loop exists.
        """
        loops: list[ControlLoop] = await self._repository.list_zone_loops(zone.id)
        points: dict[UUID, Point] = {}
        for loop in loops:
            point: Point | None = await self._repository.get_point(loop.measurement_point_id)
            if point is not None:
                points[point.id] = point
        return select_compatible_loop(loops, points, zone.id)

    async def _resolve_stage(self, version: RecipeVersion) -> RecipeStage:
        """Return the single stage a cycle may be grown against.

        Args:
            version: The published version being applied.

        Returns:
            Its only stage.

        Raises:
            InvalidRecipeVersionError: If the version is not published, carries
                more than one stage, or its stage is not the supported one.
            RecipeVersionNotFoundError: If the version carries no stage at all,
                which the atomic catalog creation path rules out.
        """
        if version.status is not RecipeVersionStatus.PUBLISHED:
            raise InvalidRecipeVersionError(
                "version_not_published",
                recipe_version_id=str(version.id),
                status=version.status.value,
            )

        stages: list[RecipeStage] = await self._repository.list_version_stages(version.id)
        if not stages:
            raise RecipeVersionNotFoundError(version.id)
        if len(stages) > 1:
            raise InvalidRecipeVersionError(
                "ambiguous_stage",
                recipe_version_id=str(version.id),
                stage_count=len(stages),
            )

        stage: RecipeStage = stages[0]
        if stage.code != SUPPORTED_STAGE_CODE:
            raise InvalidRecipeVersionError(
                "unsupported_stage_code",
                stage_code=stage.code,
                supported_stage_code=SUPPORTED_STAGE_CODE,
            )
        return stage

    async def _load_agronomy(
        self,
        cycle: GrowCycle,
    ) -> tuple[RecipeStage, TargetRequirement]:
        """Re-read and re-validate everything the recipe side of activation needs.

        The catalog is checked again at activation rather than trusted from
        creation: a crop or a recipe can be archived in between, and a cycle
        must not start growing against something that has since been retired.

        Args:
            cycle: The cycle being activated.

        Returns:
            Its version's only stage, and the temperature band the runtime
            target will snapshot.

        Raises:
            RecipeVersionNotFoundError: If the version or its stage is missing.
            GrowCycleResourceArchivedError: If the crop or the recipe is
                archived.
            InvalidRecipeVersionError: If the stored graph cannot be grown
                against, or if ``current_stage_id`` is not the version's stage.
        """
        version: RecipeVersion | None = await self._repository.get_recipe_version(
            cycle.recipe_version_id
        )
        if version is None:
            raise RecipeVersionNotFoundError(cycle.recipe_version_id)

        recipe: GrowingRecipe | None = await self._repository.get_recipe(version.recipe_id)
        if recipe is None:
            raise RecipeVersionNotFoundError(version.id)
        if recipe.status is not StatusEnum.ACTIVE:
            raise GrowCycleResourceArchivedError("growing_recipe", recipe.id)

        crop: Crop | None = await self._repository.get_crop(recipe.crop_id)
        if crop is None:
            raise RecipeVersionNotFoundError(version.id)
        if crop.status is not StatusEnum.ACTIVE:
            raise GrowCycleResourceArchivedError("crop", crop.id)

        stage: RecipeStage = await self._resolve_stage(version)
        if stage.id != cycle.current_stage_id:
            raise InvalidRecipeVersionError(
                "stage_not_in_version",
                recipe_version_id=str(version.id),
                current_stage_id=str(cycle.current_stage_id),
            )

        requirement: TargetRequirement = select_temperature_requirement(
            await self._repository.list_stage_requirements(stage.id),
            stage.id,
        )
        return stage, requirement

    async def _load_assigned_zone(self, cycle: GrowCycle) -> ControlZone:
        """Re-read the zone of a cycle's single climate assignment.

        Args:
            cycle: The cycle whose placement to resolve.

        Returns:
            The assigned zone.

        Raises:
            InvalidGrowCycleZoneError: If the cycle carries no assignment, which
                the atomic creation path rules out.
            ControlZoneNotFoundError: If the assignment names a zone that no
                longer exists, which ``ON DELETE RESTRICT`` rules out.
        """
        assignment: GrowCycleZoneAssignment | None = await self._repository.get_assignment(cycle.id)
        if assignment is None:
            raise InvalidGrowCycleZoneError(cycle.id, cycle.facility_id, "missing_zone_assignment")
        zone: ControlZone | None = await self._repository.get_control_zone(
            assignment.control_zone_id
        )
        if zone is None:
            raise ControlZoneNotFoundError(assignment.control_zone_id)
        return zone

    @staticmethod
    def _check_zone_placement(zone: ControlZone, facility_id: UUID) -> None:
        """Check that a zone can carry a cycle's climate assignment.

        Ownership is read through the existing topology chain — a zone belongs
        to a facility through ``control_zones.facility_id`` and to a site
        through that facility — rather than through a second hierarchy invented
        for cycles.

        Args:
            zone: The zone named by the request or the assignment.
            facility_id: The facility the cycle runs in.

        Raises:
            InvalidGrowCycleZoneError: If the zone belongs to another facility
                or is not a climate zone.
        """
        if zone.facility_id != facility_id:
            raise InvalidGrowCycleZoneError(zone.id, facility_id, "zone_not_in_facility")
        if zone.zone_type is not ZoneType.CLIMATE:
            raise InvalidGrowCycleZoneError(zone.id, facility_id, "zone_not_climate")

    async def _describe(
        self,
        cycle: GrowCycle,
        *,
        control_zone: ControlZone | None = None,
    ) -> GrowCycleRead:
        """Assemble the complete representation of one cycle.

        Args:
            cycle: The cycle to describe.
            control_zone: Its zone, when the caller already loaded it.

        Returns:
            The cycle with its zone, current stage and active target.

        Raises:
            InvalidGrowCycleZoneError: If the cycle carries no assignment.
            RecipeVersionNotFoundError: If ``current_stage_id`` names no stage.
        """
        zone: ControlZone = control_zone or await self._load_assigned_zone(cycle)
        stages: list[RecipeStage] = await self._repository.list_stages([cycle.current_stage_id])
        if not stages:
            raise RecipeVersionNotFoundError(cycle.recipe_version_id)
        target: RuntimeTarget | None = await self._repository.get_active_target_for_cycle(cycle.id)
        return GrowCycleRead.from_parts(cycle, zone.id, stages[0], target)

    async def _describe_many(self, cycles: list[GrowCycle]) -> list[GrowCycleRead]:
        """Assemble a page of cycles in a fixed number of statements.

        Args:
            cycles: The cycles to describe, in the order they are to be
                returned.

        Returns:
            One complete representation per cycle, in the same order.

        Raises:
            InvalidGrowCycleZoneError: If a cycle carries no assignment, which
                the atomic creation path rules out.
            RecipeVersionNotFoundError: If a cycle's stage is missing, which
                ``ON DELETE RESTRICT`` rules out.
        """
        if not cycles:
            return []

        identifiers: list[UUID] = [cycle.id for cycle in cycles]
        zone_by_cycle: dict[UUID, UUID] = {
            assignment.grow_cycle_id: assignment.control_zone_id
            for assignment in await self._repository.list_assignments(identifiers)
        }
        stage_by_id: dict[UUID, RecipeStage] = {
            stage.id: stage
            for stage in await self._repository.list_stages(
                [cycle.current_stage_id for cycle in cycles]
            )
        }
        target_by_cycle: dict[UUID, RuntimeTarget] = {
            target.grow_cycle_id: target
            for target in await self._repository.list_active_targets(identifiers)
        }

        described: list[GrowCycleRead] = []
        for cycle in cycles:
            control_zone_id: UUID | None = zone_by_cycle.get(cycle.id)
            if control_zone_id is None:
                raise InvalidGrowCycleZoneError(
                    cycle.id,
                    cycle.facility_id,
                    "missing_zone_assignment",
                )
            stage: RecipeStage | None = stage_by_id.get(cycle.current_stage_id)
            if stage is None:
                raise RecipeVersionNotFoundError(cycle.recipe_version_id)
            described.append(
                GrowCycleRead.from_parts(
                    cycle,
                    control_zone_id,
                    stage,
                    target_by_cycle.get(cycle.id),
                )
            )
        return described


class RuntimeTargetService:
    """Reads the complete runtime-target history.

    Read-only on purpose. Nothing creates, updates or deletes a target through
    this service, and there is no endpoint that would: a target is a consequence
    of activating a grow cycle, never something a client asks for.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._targets: RuntimeTargetRepository = RuntimeTargetRepository(session)

    async def get_target(self, runtime_target_id: UUID) -> RuntimeTarget:
        """Return one target or report it missing.

        Args:
            runtime_target_id: The target requested by the path.

        Returns:
            The stored target.

        Raises:
            RuntimeTargetNotFoundError: If the path names no target.
        """
        target: RuntimeTarget | None = await self._targets.get_by_id(runtime_target_id)
        if target is None:
            raise RuntimeTargetNotFoundError(runtime_target_id)
        return target

    async def list_targets(
        self,
        *,
        control_loop_id: UUID | None,
        active: bool | None,
        limit: int,
    ) -> list[RuntimeTarget]:
        """Return a bounded, newest-first window of targets.

        Args:
            control_loop_id: Restricts the result to one loop when given.
            active: ``True`` returns only the open target of each loop,
                ``False`` only closed history, ``None`` both.
            limit: Maximum number of targets to return.

        Returns:
            Matching targets, newest first.
        """
        return await self._targets.list_history(
            control_loop_id=control_loop_id,
            active=active,
            limit=limit,
        )
