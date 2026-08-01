"""Data access for the cultivation module.

This layer holds SQLAlchemy statements and nothing else. Which cycle may be
activated, and which loop may carry its target, belong to
:mod:`ai_greenhouse.cultivation.service`.

Two statements here are load-bearing rather than convenient:

- :meth:`GrowCycleRepository.lock_by_id` takes a row lock. It is what serialises
  two concurrent transitions of one cycle, so the second one sees the first
  one's result instead of repeating its writes.
- The batch reads take collections of identifiers, so a page of cycles costs a
  fixed number of statements instead of one per cycle.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.models import (
    Crop,
    GrowingRecipe,
    RecipeStage,
    RecipeVersion,
    TargetRequirement,
)
from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.control.models import ControlLoop
from ai_greenhouse.cultivation.models import (
    GrowCycle,
    GrowCycleStatus,
    GrowCycleZoneAssignment,
    GrowStageInstance,
    RuntimeTarget,
)
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.models import ControlZone, Facility


class GrowCycleRepository:
    """Queries over ``grow_cycles`` and the aggregate written with it.

    The cycle, its zone assignment, its stage instance and its runtime target
    are read and written together and never on their own, so one repository owns
    all four: they are one aggregate, and splitting them across repositories
    would invite a caller to write half of it.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, instance: object) -> None:
        """Stage one row of the aggregate for insertion on the next flush.

        Args:
            instance: The cycle, assignment, stage instance or runtime target to
                persist.
        """
        self._session.add(instance)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate them
        into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, grow_cycle_id: UUID) -> GrowCycle | None:
        """Load one cycle by primary key.

        Args:
            grow_cycle_id: Identifier to look up.

        Returns:
            The matching cycle, or ``None`` when no row exists.
        """
        return await self._session.get(GrowCycle, grow_cycle_id)

    async def get_by_code(self, code: str) -> GrowCycle | None:
        """Load one cycle by its globally unique code.

        Args:
            code: The slug to look up.

        Returns:
            The matching cycle, or ``None`` when the code is free.
        """
        return await self._session.scalar(select(GrowCycle).where(GrowCycle.code == code))

    async def lock_by_id(self, grow_cycle_id: UUID) -> GrowCycle | None:
        """Load one cycle and hold a row lock on it for the transaction.

        ``populate_existing`` is what makes the lock useful: the row is re-read
        once the lock is granted, so a transaction that waited for another
        transition sees the status that transition left behind rather than the
        one it read before blocking.

        Args:
            grow_cycle_id: The cycle a lifecycle transition was requested on.

        Returns:
            The locked cycle, or ``None`` when no row exists.
        """
        return await self._session.scalar(
            select(GrowCycle)
            .where(GrowCycle.id == grow_cycle_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_page(
        self,
        params: PageParams,
        *,
        code: str | None = None,
        facility_id: UUID | None = None,
        status: GrowCycleStatus | None = None,
    ) -> tuple[list[GrowCycle], int]:
        """Return one page of cycles together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the cycle with that exact code.
            facility_id: Restricts the result to the cycles of one facility.
            status: Restricts the result to the cycles in one lifecycle state.

        Returns:
            A tuple of the page's cycles, ordered by ``created_at ASC, id ASC``,
            and the total number matching the filters.
        """
        statement: Select[tuple[GrowCycle]] = select(GrowCycle)
        if code is not None:
            statement = statement.where(GrowCycle.code == code)
        if facility_id is not None:
            statement = statement.where(GrowCycle.facility_id == facility_id)
        if status is not None:
            statement = statement.where(GrowCycle.status == status)
        return await paginate(self._session, statement, GrowCycle, params)

    async def get_assignment(self, grow_cycle_id: UUID) -> GrowCycleZoneAssignment | None:
        """Load the single climate-zone assignment of one cycle.

        Args:
            grow_cycle_id: The cycle whose placement to read.

        Returns:
            The assignment, or ``None`` when the cycle carries none.
        """
        return await self._session.scalar(
            select(GrowCycleZoneAssignment).where(
                GrowCycleZoneAssignment.grow_cycle_id == grow_cycle_id
            )
        )

    async def list_assignments(
        self,
        grow_cycle_ids: Sequence[UUID],
    ) -> list[GrowCycleZoneAssignment]:
        """Load the assignments of several cycles in one statement.

        Args:
            grow_cycle_ids: The cycles whose placements to read. An empty
                sequence short-circuits without touching the database.

        Returns:
            The assignments, in no particular order; the caller keys them by
            cycle.
        """
        if not grow_cycle_ids:
            return []
        return list(
            await self._session.scalars(
                select(GrowCycleZoneAssignment).where(
                    GrowCycleZoneAssignment.grow_cycle_id.in_(grow_cycle_ids)
                )
            )
        )

    async def list_stages(self, recipe_stage_ids: Sequence[UUID]) -> list[RecipeStage]:
        """Load several recipe stages in one statement.

        Args:
            recipe_stage_ids: The stages named by a page of cycles. An empty
                sequence short-circuits without touching the database.

        Returns:
            The stages, in no particular order; the caller keys them by
            identifier.
        """
        if not recipe_stage_ids:
            return []
        return list(
            await self._session.scalars(
                select(RecipeStage).where(RecipeStage.id.in_(recipe_stage_ids))
            )
        )

    async def get_open_stage_instance(self, grow_cycle_id: UUID) -> GrowStageInstance | None:
        """Load the stage instance a running cycle has not closed yet.

        Args:
            grow_cycle_id: The cycle whose open stage to read.

        Returns:
            The open instance, or ``None`` when the cycle has none.
        """
        return await self._session.scalar(
            select(GrowStageInstance).where(
                GrowStageInstance.grow_cycle_id == grow_cycle_id,
                GrowStageInstance.ended_at.is_(None),
            )
        )

    async def get_active_target_for_cycle(self, grow_cycle_id: UUID) -> RuntimeTarget | None:
        """Load the open runtime target of one cycle.

        Args:
            grow_cycle_id: The cycle whose active target to read.

        Returns:
            The target with no ``effective_to``, or ``None`` when the cycle has
            none.
        """
        return await self._session.scalar(
            select(RuntimeTarget).where(
                RuntimeTarget.grow_cycle_id == grow_cycle_id,
                RuntimeTarget.effective_to.is_(None),
            )
        )

    async def list_active_targets(self, grow_cycle_ids: Sequence[UUID]) -> list[RuntimeTarget]:
        """Load the open runtime targets of several cycles in one statement.

        Args:
            grow_cycle_ids: The cycles whose active targets to read. An empty
                sequence short-circuits without touching the database.

        Returns:
            The open targets, in no particular order; the caller keys them by
            cycle.
        """
        if not grow_cycle_ids:
            return []
        return list(
            await self._session.scalars(
                select(RuntimeTarget).where(
                    RuntimeTarget.grow_cycle_id.in_(grow_cycle_ids),
                    RuntimeTarget.effective_to.is_(None),
                )
            )
        )

    async def get_active_target_for_loop(self, control_loop_id: UUID) -> RuntimeTarget | None:
        """Answer the concrete query the partial unique index serves.

        Args:
            control_loop_id: The loop to check.

        Returns:
            The loop's open target, or ``None`` when nothing drives it.
        """
        return await self._session.scalar(
            select(RuntimeTarget).where(
                RuntimeTarget.control_loop_id == control_loop_id,
                RuntimeTarget.effective_to.is_(None),
            )
        )

    async def get_facility(self, facility_id: UUID) -> Facility | None:
        """Load the facility a cycle runs in.

        Args:
            facility_id: The facility named by the request or the cycle.

        Returns:
            The matching facility, or ``None`` when no row exists.
        """
        return await self._session.get(Facility, facility_id)

    async def get_control_zone(self, control_zone_id: UUID) -> ControlZone | None:
        """Load the zone a cycle is assigned to.

        Args:
            control_zone_id: The zone named by the request or the assignment.

        Returns:
            The matching zone, or ``None`` when no row exists.
        """
        return await self._session.get(ControlZone, control_zone_id)

    async def get_recipe_version(self, recipe_version_id: UUID) -> RecipeVersion | None:
        """Load the published version a cycle is grown against.

        Args:
            recipe_version_id: The version named by the request or the cycle.

        Returns:
            The matching version, or ``None`` when no row exists.
        """
        return await self._session.get(RecipeVersion, recipe_version_id)

    async def get_recipe(self, recipe_id: UUID) -> GrowingRecipe | None:
        """Load the recipe identity a version belongs to.

        Args:
            recipe_id: The recipe reached through the version.

        Returns:
            The matching recipe, or ``None`` when no row exists.
        """
        return await self._session.get(GrowingRecipe, recipe_id)

    async def get_crop(self, crop_id: UUID) -> Crop | None:
        """Load the crop a recipe grows.

        Args:
            crop_id: The crop reached through the recipe.

        Returns:
            The matching crop, or ``None`` when no row exists.
        """
        return await self._session.get(Crop, crop_id)

    async def list_version_stages(self, recipe_version_id: UUID) -> list[RecipeStage]:
        """Load every stage of one version, in its own order.

        The whole list is returned rather than one stage, because "the version's
        *only* stage" is a rule the service applies and cannot apply to a query
        that already picked one.

        Args:
            recipe_version_id: The version whose stages to read.

        Returns:
            The stages, ordered by ``sequence_number ASC``.
        """
        return list(
            await self._session.scalars(
                select(RecipeStage)
                .where(RecipeStage.recipe_version_id == recipe_version_id)
                .order_by(RecipeStage.sequence_number.asc())
            )
        )

    async def list_stage_requirements(self, recipe_stage_id: UUID) -> list[TargetRequirement]:
        """Load every requirement of one stage.

        Args:
            recipe_stage_id: The stage whose requirements to read.

        Returns:
            The requirements, ordered by ``metric_type ASC``.
        """
        return list(
            await self._session.scalars(
                select(TargetRequirement)
                .where(TargetRequirement.recipe_stage_id == recipe_stage_id)
                .order_by(TargetRequirement.metric_type.asc())
            )
        )

    async def list_zone_loops(self, control_zone_id: UUID) -> list[ControlLoop]:
        """Load every control loop configured for one zone.

        The whole list is returned for the same reason the stages are: "exactly
        one compatible loop" is a rule, and a query returning a single row would
        answer it before the service could.

        Args:
            control_zone_id: The climate zone whose loops to read.

        Returns:
            The loops, ordered by ``created_at ASC, id ASC``.
        """
        return list(
            await self._session.scalars(
                select(ControlLoop)
                .where(ControlLoop.control_zone_id == control_zone_id)
                .order_by(ControlLoop.created_at.asc(), ControlLoop.id.asc())
            )
        )

    async def get_point(self, point_id: UUID) -> Point | None:
        """Load the point a control loop measures.

        Args:
            point_id: The loop's measurement point.

        Returns:
            The matching point, or ``None`` when no row exists.
        """
        return await self._session.get(Point, point_id)


class RuntimeTargetRepository:
    """Queries over ``runtime_targets``.

    Read-only apart from what the cycle lifecycle writes through
    :class:`GrowCycleRepository`. There is no create, update or delete here,
    because there is no endpoint that would call one: a target is a consequence
    of activating a cycle, never something a client asks for.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    async def get_by_id(self, runtime_target_id: UUID) -> RuntimeTarget | None:
        """Load one target by primary key.

        Args:
            runtime_target_id: Identifier to look up.

        Returns:
            The matching target, or ``None`` when no row exists.
        """
        return await self._session.get(RuntimeTarget, runtime_target_id)

    async def list_history(
        self,
        *,
        control_loop_id: UUID | None,
        active: bool | None,
        limit: int,
    ) -> list[RuntimeTarget]:
        """Read a bounded, newest-first window of runtime targets.

        The complete order and limit are part of the statement. ``id DESC``
        breaks ties between targets written in the same instant, so repeated
        calls return the same order without Python sorting or slicing.

        Args:
            control_loop_id: Restricts the result to one loop when given.
            active: ``True`` returns only open targets, ``False`` only closed
                ones, ``None`` both.
            limit: Maximum number of rows for PostgreSQL to return.

        Returns:
            Matching targets in ``created_at DESC, id DESC`` order.
        """
        statement: Select[tuple[RuntimeTarget]] = select(RuntimeTarget)
        if control_loop_id is not None:
            statement = statement.where(RuntimeTarget.control_loop_id == control_loop_id)
        if active is True:
            statement = statement.where(RuntimeTarget.effective_to.is_(None))
        elif active is False:
            statement = statement.where(RuntimeTarget.effective_to.is_not(None))
        statement = statement.order_by(
            RuntimeTarget.created_at.desc(),
            RuntimeTarget.id.desc(),
        ).limit(limit)
        return list(await self._session.scalars(statement))
