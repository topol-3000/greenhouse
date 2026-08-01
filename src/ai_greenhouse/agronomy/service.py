"""Agronomy business rules.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.agronomy.exceptions`, which ``ai_greenhouse.api.errors``
turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or
rollback belongs to the ``get_session`` request dependency, which is also what
makes the recipe graph atomic — a failure at any level leaves the whole request
rolled back, so a recipe without its version, or a version without its three
requirements, is never reachable.

What a version may say is declared once, in :data:`SUPPORTED_REQUIREMENTS`.
Spreading the same knowledge across conditionals is how the metric, its kind and
its unit drift apart.
"""

from collections import Counter
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.exceptions import (
    CropCodeExistsError,
    CropNotFoundError,
    InvalidRecipeVersionError,
    RecipeCodeExistsError,
    RecipeNotFoundError,
    RecipeVersionNotFoundError,
    ReferencedCropNotFoundError,
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
from ai_greenhouse.agronomy.repository import (
    CropRepository,
    GrowingRecipeRepository,
    RecipeGraphRepository,
)
from ai_greenhouse.agronomy.schemas import (
    CropCreate,
    GrowingRecipeCreate,
    GrowingRecipeRead,
    RecipeStageCreate,
    RecipeStageRead,
    RecipeVersionCreate,
    RecipeVersionRead,
    TargetRequirementCreate,
)
from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.infrastructure.database.base import utc_now

SUPPORTED_VERSION_NUMBER: int = 1
"""The only version number this milestone publishes.

Version 2 is not a bigger number: it is a second immutable statement, a rule for
which of the two a consumer should read, and a way of superseding the first.
None of that exists yet, so the number that would announce it is refused.
"""

SUPPORTED_STAGE_CODE: str = "vegetative"
"""The only stage a version may carry while a version carries exactly one."""

SUPPORTED_STAGE_SEQUENCE_NUMBER: int = 1
"""Position of that single stage."""

MIN_DURATION_HOURS: Decimal = Decimal(0)
MAX_DURATION_HOURS: Decimal = Decimal(24)
"""Exclusive lower and inclusive upper bound of a daily duration.

A photoperiod of zero is darkness expressed as a requirement, and one above 24
asks for more light than a day has.
"""

SUPPORTED_REQUIREMENTS: dict[str, tuple[RequirementKind, str]] = {
    "air_humidity": (RequirementKind.RANGE, "%"),
    "air_temperature": (RequirementKind.RANGE, "°C"),
    "photoperiod": (RequirementKind.DURATION_PER_DAY, "h/day"),
}
"""The metric, kind and unit of every requirement a version may carry.

All three are fixed together on purpose. ``air_temperature`` expressed as a
``duration_per_day``, or a photoperiod in ``°C``, would both be well-formed rows
and neither would mean anything, so the combination is what is validated rather
than each field on its own.

The mapping is also the *required* set: a version carries one requirement for
each of these metrics, no more and no fewer. A recipe missing its humidity band
is not a smaller recipe; it is a recipe nobody can grow against.
"""


def validate_requirement(payload: TargetRequirementCreate) -> None:
    """Check one requirement against the combination its metric allows.

    Args:
        payload: The submitted requirement.

    Raises:
        InvalidRecipeVersionError: If the metric is unsupported, if its kind or
            unit is not the one that metric is expressed in, or if the values
            do not match the kind.
    """
    supported: tuple[RequirementKind, str] | None = SUPPORTED_REQUIREMENTS.get(payload.metric_type)
    if supported is None:
        raise InvalidRecipeVersionError(
            "unsupported_metric_type",
            metric_type=payload.metric_type,
            supported_metric_types=sorted(SUPPORTED_REQUIREMENTS),
        )

    requirement_kind, unit = supported
    if payload.requirement_kind is not requirement_kind:
        raise InvalidRecipeVersionError(
            "unsupported_requirement_kind",
            metric_type=payload.metric_type,
            requirement_kind=payload.requirement_kind.value,
            supported_requirement_kind=requirement_kind.value,
        )
    if payload.unit != unit:
        raise InvalidRecipeVersionError(
            "unsupported_unit",
            metric_type=payload.metric_type,
            unit=payload.unit,
            supported_unit=unit,
        )

    if requirement_kind is RequirementKind.RANGE:
        validate_range_values(payload)
    else:
        validate_duration_values(payload)


def validate_range_values(payload: TargetRequirementCreate) -> None:
    """Check the values of a ``range`` requirement.

    Args:
        payload: The submitted requirement, already known to be a range.

    Raises:
        InvalidRecipeVersionError: If a bound is missing, if a ``target_value``
            was submitted as well, or if the bounds are not ordered.
    """
    if payload.target_value is not None:
        raise InvalidRecipeVersionError(
            "target_value_not_allowed",
            metric_type=payload.metric_type,
            requirement_kind=RequirementKind.RANGE.value,
        )
    if payload.min_value is None or payload.max_value is None:
        raise InvalidRecipeVersionError(
            "missing_range_bounds",
            metric_type=payload.metric_type,
        )
    if payload.min_value >= payload.max_value:
        raise InvalidRecipeVersionError(
            "invalid_range",
            metric_type=payload.metric_type,
            min_value=str(payload.min_value),
            max_value=str(payload.max_value),
        )


def validate_duration_values(payload: TargetRequirementCreate) -> None:
    """Check the values of a ``duration_per_day`` requirement.

    Args:
        payload: The submitted requirement, already known to be a duration.

    Raises:
        InvalidRecipeVersionError: If the target is missing, if a bound was
            submitted as well, or if the target is not a duration a day can
            hold.
    """
    if payload.min_value is not None or payload.max_value is not None:
        raise InvalidRecipeVersionError(
            "range_bounds_not_allowed",
            metric_type=payload.metric_type,
            requirement_kind=RequirementKind.DURATION_PER_DAY.value,
        )
    if payload.target_value is None:
        raise InvalidRecipeVersionError(
            "missing_target_value",
            metric_type=payload.metric_type,
        )
    if not MIN_DURATION_HOURS < payload.target_value <= MAX_DURATION_HOURS:
        raise InvalidRecipeVersionError(
            "invalid_duration",
            metric_type=payload.metric_type,
            target_value=str(payload.target_value),
        )


def validate_requirement_set(payload: RecipeStageCreate) -> None:
    """Check that a stage carries exactly one requirement per required metric.

    Args:
        payload: The submitted stage.

    Raises:
        InvalidRecipeVersionError: If a metric is repeated, unsupported or
            absent.
    """
    submitted: list[str] = [requirement.metric_type for requirement in payload.requirements]
    repeated: list[str] = sorted(
        {metric for metric, count in Counter(submitted).items() if count > 1}
    )
    if repeated:
        raise InvalidRecipeVersionError("duplicate_metric_type", metric_types=repeated)

    unsupported: list[str] = sorted(set(submitted) - set(SUPPORTED_REQUIREMENTS))
    if unsupported:
        raise InvalidRecipeVersionError(
            "unsupported_metric_type",
            metric_types=unsupported,
            supported_metric_types=sorted(SUPPORTED_REQUIREMENTS),
        )

    missing: list[str] = sorted(set(SUPPORTED_REQUIREMENTS) - set(submitted))
    if missing:
        raise InvalidRecipeVersionError("missing_metric_type", metric_types=missing)


def validate_recipe_version(payload: RecipeVersionCreate) -> None:
    """Check a whole submitted version before anything is written.

    The checks run from the widest failure to the narrowest — the version, then
    the stage, then the set of requirements, then the values inside each one —
    so a client is told what is actually wrong rather than whichever narrower
    rule the graph happened to break first.

    Nothing here touches the database. That is what makes the refusal complete:
    an invalid version is rejected before a single row of it is staged, so no
    partial recipe can be left behind by a failure halfway down.

    Args:
        payload: The submitted version.

    Raises:
        InvalidRecipeVersionError: If any structural rule is broken.
    """
    if payload.version_number != SUPPORTED_VERSION_NUMBER:
        raise InvalidRecipeVersionError(
            "unsupported_version_number",
            version_number=payload.version_number,
            supported_version_number=SUPPORTED_VERSION_NUMBER,
        )

    stage: RecipeStageCreate = payload.stage
    if stage.code != SUPPORTED_STAGE_CODE:
        raise InvalidRecipeVersionError(
            "unsupported_stage_code",
            stage_code=stage.code,
            supported_stage_code=SUPPORTED_STAGE_CODE,
        )
    if stage.sequence_number != SUPPORTED_STAGE_SEQUENCE_NUMBER:
        raise InvalidRecipeVersionError(
            "unsupported_stage_sequence_number",
            sequence_number=stage.sequence_number,
            supported_sequence_number=SUPPORTED_STAGE_SEQUENCE_NUMBER,
        )

    validate_requirement_set(stage)
    for requirement in stage.requirements:
        validate_requirement(requirement)


class CropService:
    """Applies the crop invariants over a single request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repository around one session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: CropRepository = CropRepository(session)

    async def create_crop(self, payload: CropCreate) -> Crop:
        """Register a new crop.

        The code is checked before the insert so the common case returns a
        precise failure, and the unique index is relied on as well so two
        concurrent requests cannot both succeed.

        Args:
            payload: The validated request body.

        Returns:
            The persisted crop, with its generated id and timestamp.

        Raises:
            CropCodeExistsError: If the code is already taken.
        """
        if await self._repository.get_by_code(payload.code) is not None:
            raise CropCodeExistsError(payload.code)

        crop = Crop(
            code=payload.code,
            display_name=payload.display_name,
            scientific_name=payload.scientific_name,
        )
        self._repository.add(crop)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise CropCodeExistsError(payload.code) from error
        return crop

    async def get_crop(self, crop_id: UUID) -> Crop:
        """Load one crop, archived ones included.

        Args:
            crop_id: Identifier to look up.

        Returns:
            The requested crop.

        Raises:
            CropNotFoundError: If no crop has that identifier.
        """
        crop: Crop | None = await self._repository.get_by_id(crop_id)
        if crop is None:
            raise CropNotFoundError(crop_id)
        return crop

    async def list_crops(
        self,
        params: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[Crop], int]:
        """Return one page of crops with the unpaged total.

        An unknown ``code`` is not an error here: a filter matching nothing
        yields an empty page rather than a failure.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the crop with that exact code.

        Returns:
            A tuple of the page's crops and the total matching the filter.
        """
        return await self._repository.list_page(params, code=code)


class GrowingRecipeService:
    """Applies the recipe invariants over a single request's session.

    Creating a recipe creates its whole graph. That is not a convenience: a
    recipe identity carries no agronomic value, so an identity that existed
    without a published version would be a resource nothing could be grown
    against, and every reader would have to handle it.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        The crop repository is held as well: creating a recipe has to resolve
        its crop, and that read belongs in the data-access layer.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._repository: GrowingRecipeRepository = GrowingRecipeRepository(session)
        self._graph: RecipeGraphRepository = RecipeGraphRepository(session)
        self._crops: CropRepository = CropRepository(session)

    async def create_recipe(self, payload: GrowingRecipeCreate) -> GrowingRecipeRead:
        """Create a recipe identity, its published version, stage and requirements.

        The whole graph is written inside the request's one transaction, so
        either all of it is committed or none of it is. The version is validated
        before anything is staged, which is what keeps a rejected requirement
        from leaving a recipe and a version behind it.

        Args:
            payload: The validated request body.

        Returns:
            The complete created graph.

        Raises:
            ReferencedCropNotFoundError: If the referenced crop does not exist.
            InvalidRecipeVersionError: If the submitted version is not one this
                milestone can publish.
            RecipeCodeExistsError: If the recipe code is already taken.
        """
        if await self._crops.get_by_id(payload.crop_id) is None:
            raise ReferencedCropNotFoundError(payload.crop_id)

        validate_recipe_version(payload.version)

        if await self._repository.get_by_code(payload.code) is not None:
            raise RecipeCodeExistsError(payload.code)

        recipe = GrowingRecipe(
            crop_id=payload.crop_id,
            code=payload.code,
            name=payload.name,
        )
        self._repository.add(recipe)
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise RecipeCodeExistsError(payload.code) from error

        published_at = utc_now()
        version = RecipeVersion(
            recipe_id=recipe.id,
            version_number=payload.version.version_number,
            status=RecipeVersionStatus.PUBLISHED,
            published_at=published_at,
            created_at=published_at,
        )
        self._graph.add_version(version)
        await self._graph.flush()

        stage_payload: RecipeStageCreate = payload.version.stage
        stage = RecipeStage(
            recipe_version_id=version.id,
            code=stage_payload.code,
            name=stage_payload.name,
            sequence_number=stage_payload.sequence_number,
        )
        self._graph.add_stage(stage)
        await self._graph.flush()

        requirements: list[TargetRequirement] = [
            TargetRequirement(
                recipe_stage_id=stage.id,
                metric_type=requirement.metric_type,
                requirement_kind=requirement.requirement_kind,
                unit=requirement.unit,
                min_value=requirement.min_value,
                max_value=requirement.max_value,
                target_value=requirement.target_value,
            )
            for requirement in stage_payload.requirements
        ]
        self._graph.add_requirements(requirements)
        await self._graph.flush()

        return GrowingRecipeRead.from_parts(
            recipe,
            RecipeVersionRead.from_parts(
                version,
                RecipeStageRead.from_parts(
                    stage,
                    sorted(requirements, key=lambda item: item.metric_type),
                ),
            ),
        )

    async def get_recipe(self, recipe_id: UUID) -> GrowingRecipeRead:
        """Read one recipe with its whole published graph.

        Args:
            recipe_id: Identifier of the recipe to read.

        Returns:
            The recipe, its version, its stage and every requirement.

        Raises:
            RecipeNotFoundError: If no recipe has that identifier.
            RecipeVersionNotFoundError: If the recipe has no published version,
                which the atomic creation path rules out.
        """
        recipe: GrowingRecipe | None = await self._repository.get_by_id(recipe_id)
        if recipe is None:
            raise RecipeNotFoundError(recipe_id)
        return (await self._assemble([recipe]))[0]

    async def list_recipes(
        self,
        params: PageParams,
        *,
        code: str | None = None,
        crop_id: UUID | None = None,
    ) -> tuple[list[GrowingRecipeRead], int]:
        """Return one page of complete recipe graphs with the unpaged total.

        The page costs four statements whatever its size: the recipes, their
        versions, those versions' stages and those stages' requirements.

        An unknown ``code`` or ``crop_id`` is not an error here: a filter
        matching nothing yields an empty page rather than a failure.

        Args:
            params: The resolved ``limit``/``offset`` window.
            code: Restricts the result to the recipe with that exact code.
            crop_id: Restricts the result to the recipes of one crop.

        Returns:
            A tuple of the page's complete recipes and the total matching the
            filters.
        """
        recipes, total = await self._repository.list_page(params, code=code, crop_id=crop_id)
        return await self._assemble(recipes), total

    async def get_version(self, recipe_version_id: UUID) -> RecipeVersionRead:
        """Read one published version with its stage and requirements.

        Args:
            recipe_version_id: Identifier of the version to read.

        Returns:
            The version, its stage and every requirement.

        Raises:
            RecipeVersionNotFoundError: If no version has that identifier, or if
                it carries no stage.
        """
        version: RecipeVersion | None = await self._graph.get_version(recipe_version_id)
        if version is None:
            raise RecipeVersionNotFoundError(recipe_version_id)
        return RecipeVersionRead.from_parts(version, await self._assemble_stage(version))

    async def _assemble(self, recipes: list[GrowingRecipe]) -> list[GrowingRecipeRead]:
        """Load the graph of several recipes and fold it into their representations.

        Args:
            recipes: The recipe identities to describe, in the order they are
                to be returned.

        Returns:
            One complete representation per recipe, in the same order.

        Raises:
            RecipeVersionNotFoundError: If a recipe has no published version,
                or its version no stage. Neither is reachable through the API:
                the graph is created in one transaction.
        """
        if not recipes:
            return []

        versions: list[RecipeVersion] = await self._graph.list_versions(
            [recipe.id for recipe in recipes]
        )
        stages: list[RecipeStage] = await self._graph.list_stages(
            [version.id for version in versions]
        )
        requirements: list[TargetRequirement] = await self._graph.list_requirements(
            [stage.id for stage in stages]
        )

        version_by_recipe: dict[UUID, RecipeVersion] = {
            version.recipe_id: version for version in versions
        }
        stage_by_version: dict[UUID, RecipeStage] = {
            stage.recipe_version_id: stage for stage in stages
        }
        requirements_by_stage: dict[UUID, list[TargetRequirement]] = {
            stage.id: [] for stage in stages
        }
        for requirement in requirements:
            requirements_by_stage[requirement.recipe_stage_id].append(requirement)

        assembled: list[GrowingRecipeRead] = []
        for recipe in recipes:
            version: RecipeVersion | None = version_by_recipe.get(recipe.id)
            if version is None:
                raise RecipeVersionNotFoundError(recipe.id)
            stage: RecipeStage | None = stage_by_version.get(version.id)
            if stage is None:
                raise RecipeVersionNotFoundError(version.id)
            assembled.append(
                GrowingRecipeRead.from_parts(
                    recipe,
                    RecipeVersionRead.from_parts(
                        version,
                        RecipeStageRead.from_parts(stage, requirements_by_stage[stage.id]),
                    ),
                )
            )
        return assembled

    async def _assemble_stage(self, version: RecipeVersion) -> RecipeStageRead:
        """Load one version's stage together with its requirements.

        Args:
            version: The version to describe.

        Returns:
            Its stage with every requirement embedded.

        Raises:
            RecipeVersionNotFoundError: If the version carries no stage, which
                the atomic creation path rules out.
        """
        stages: list[RecipeStage] = await self._graph.list_stages([version.id])
        if not stages:
            raise RecipeVersionNotFoundError(version.id)
        stage: RecipeStage = stages[0]
        return RecipeStageRead.from_parts(
            stage,
            await self._graph.list_requirements([stage.id]),
        )
