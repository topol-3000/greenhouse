"""Data access for the agronomy module.

This layer holds SQLAlchemy statements and nothing else. Invariants, error
translation and the decision to commit belong to
:mod:`ai_greenhouse.agronomy.service`.

The graph reads take collections of identifiers rather than one, so reading a
page of recipes costs one statement per level instead of one per recipe. That
is a documented contract and not an optimisation: a catalog of a hundred
recipes must cost the same number of queries as one of four.
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


class CropRepository:
    """Queries over the ``crops`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, crop: Crop) -> None:
        """Stage a new crop for insertion on the next flush.

        Args:
            crop: The instance to persist.
        """
        self._session.add(crop)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, crop_id: UUID) -> Crop | None:
        """Load a crop by primary key.

        Args:
            crop_id: Identifier to look up.

        Returns:
            The matching crop, or ``None`` when no row exists.
        """
        return await self._session.get(Crop, crop_id)

    async def get_by_code(self, code: str) -> Crop | None:
        """Load a crop by its globally unique code.

        Args:
            code: The slug to look up.

        Returns:
            The matching crop, or ``None`` when the code is free.
        """
        return await self._session.scalar(select(Crop).where(Crop.code == code))

    async def list_page(
        self,
        params: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[Crop], int]:
        """Return one page of crops together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the crop with that exact code.

        Returns:
            A tuple of the page's crops, ordered by ``created_at ASC, id ASC``,
            and the total number of crops matching the filter.
        """
        statement: Select[tuple[Crop]] = select(Crop)
        if code is not None:
            statement = statement.where(Crop.code == code)
        return await paginate(self._session, statement, Crop, params)


class GrowingRecipeRepository:
    """Queries over the ``growing_recipes`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, recipe: GrowingRecipe) -> None:
        """Stage a new recipe identity for insertion on the next flush.

        Args:
            recipe: The instance to persist.
        """
        self._session.add(recipe)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, recipe_id: UUID) -> GrowingRecipe | None:
        """Load a recipe identity by primary key.

        Args:
            recipe_id: Identifier to look up.

        Returns:
            The matching recipe, or ``None`` when no row exists.
        """
        return await self._session.get(GrowingRecipe, recipe_id)

    async def get_by_code(self, code: str) -> GrowingRecipe | None:
        """Load a recipe identity by its globally unique code.

        Args:
            code: The slug to look up.

        Returns:
            The matching recipe, or ``None`` when the code is free.
        """
        return await self._session.scalar(select(GrowingRecipe).where(GrowingRecipe.code == code))

    async def list_page(
        self,
        params: PageParams,
        *,
        code: str | None = None,
        crop_id: UUID | None = None,
    ) -> tuple[list[GrowingRecipe], int]:
        """Return one page of recipe identities with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the recipe with that exact code.
            crop_id: Restricts the result to the recipes of one crop.

        Returns:
            A tuple of the page's recipes, ordered by ``created_at ASC,
            id ASC``, and the total number matching the filters.
        """
        statement: Select[tuple[GrowingRecipe]] = select(GrowingRecipe)
        if code is not None:
            statement = statement.where(GrowingRecipe.code == code)
        if crop_id is not None:
            statement = statement.where(GrowingRecipe.crop_id == crop_id)
        return await paginate(self._session, statement, GrowingRecipe, params)


class RecipeGraphRepository:
    """Queries over the three immutable levels below a recipe identity.

    Versions, stages and requirements are read together and never on their own,
    so one repository owns all three. Each read takes a collection of parent
    identifiers, which is what keeps a page of recipes at three statements.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add_version(self, version: RecipeVersion) -> None:
        """Stage a new version for insertion on the next flush.

        Args:
            version: The instance to persist.
        """
        self._session.add(version)

    def add_stage(self, stage: RecipeStage) -> None:
        """Stage a new recipe stage for insertion on the next flush.

        Args:
            stage: The instance to persist.
        """
        self._session.add(stage)

    def add_requirements(self, requirements: Sequence[TargetRequirement]) -> None:
        """Stage new requirements for insertion on the next flush.

        Args:
            requirements: The instances to persist.
        """
        self._session.add_all(requirements)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The whole aggregate is flushed at once, so a constraint broken by any
        of its rows is raised before any of them is committed.
        """
        await self._session.flush()

    async def get_version(self, recipe_version_id: UUID) -> RecipeVersion | None:
        """Load one version by primary key.

        Args:
            recipe_version_id: Identifier to look up.

        Returns:
            The matching version, or ``None`` when no row exists.
        """
        return await self._session.get(RecipeVersion, recipe_version_id)

    async def list_versions(self, recipe_ids: Sequence[UUID]) -> list[RecipeVersion]:
        """Load every version of several recipes in one statement.

        Args:
            recipe_ids: The recipes whose versions to read. An empty sequence
                short-circuits without touching the database.

        Returns:
            The versions, ordered by ``recipe_id`` and then by
            ``version_number ASC``.
        """
        if not recipe_ids:
            return []
        statement: Select[tuple[RecipeVersion]] = (
            select(RecipeVersion)
            .where(RecipeVersion.recipe_id.in_(recipe_ids))
            .order_by(RecipeVersion.recipe_id.asc(), RecipeVersion.version_number.asc())
        )
        return list(await self._session.scalars(statement))

    async def list_stages(self, recipe_version_ids: Sequence[UUID]) -> list[RecipeStage]:
        """Load every stage of several versions in one statement.

        Args:
            recipe_version_ids: The versions whose stages to read. An empty
                sequence short-circuits without touching the database.

        Returns:
            The stages, ordered by ``recipe_version_id`` and then by
            ``sequence_number ASC``.
        """
        if not recipe_version_ids:
            return []
        statement: Select[tuple[RecipeStage]] = (
            select(RecipeStage)
            .where(RecipeStage.recipe_version_id.in_(recipe_version_ids))
            .order_by(RecipeStage.recipe_version_id.asc(), RecipeStage.sequence_number.asc())
        )
        return list(await self._session.scalars(statement))

    async def list_requirements(
        self,
        recipe_stage_ids: Sequence[UUID],
    ) -> list[TargetRequirement]:
        """Load every requirement of several stages in one statement.

        Args:
            recipe_stage_ids: The stages whose requirements to read. An empty
                sequence short-circuits without touching the database.

        Returns:
            The requirements, ordered by ``recipe_stage_id`` and then by
            ``metric_type ASC``, so a version always reports its requirements
            in the same order.
        """
        if not recipe_stage_ids:
            return []
        statement: Select[tuple[TargetRequirement]] = (
            select(TargetRequirement)
            .where(TargetRequirement.recipe_stage_id.in_(recipe_stage_ids))
            .order_by(
                TargetRequirement.recipe_stage_id.asc(),
                TargetRequirement.metric_type.asc(),
            )
        )
        return list(await self._session.scalars(statement))
