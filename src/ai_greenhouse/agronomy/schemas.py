"""Request and response schemas of the agronomy module.

Every creation body forbids unknown fields. A recipe is written once and read
for as long as it exists, so a misspelled requirement key has to fail loudly at
the moment it is submitted rather than be dropped and discovered later by
whoever wonders why the humidity was never held.

The read representations embed the whole published graph — recipe, version,
stage and requirements — because a consumer that has to reassemble a recipe from
several endpoints is a consumer that can render half of one.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from ai_greenhouse.agronomy.models import (
    MAX_CROP_CODE_LENGTH,
    MAX_CROP_DISPLAY_NAME_LENGTH,
    MAX_RECIPE_CODE_LENGTH,
    MAX_RECIPE_NAME_LENGTH,
    MAX_SCIENTIFIC_NAME_LENGTH,
    MAX_STAGE_CODE_LENGTH,
    MAX_STAGE_NAME_LENGTH,
    GrowingRecipe,
    RecipeStage,
    RecipeVersion,
    RecipeVersionStatus,
    RequirementKind,
    TargetRequirement,
)
from ai_greenhouse.core.types import CodeStr, UnitStr, name_type, slug_type
from ai_greenhouse.infrastructure.database.base import StatusEnum

DEFAULT_VERSION_NUMBER: int = 1
"""Version number assumed when a creation body leaves it out.

Which version numbers are actually accepted is a domain rule and lives in
:mod:`ai_greenhouse.agronomy.service`; this is only what an omitted field means.
"""

DEFAULT_STAGE_SEQUENCE_NUMBER: int = 1
"""Position assumed when a stage body leaves ``sequence_number`` out."""

CropCodeStr = slug_type(MAX_CROP_CODE_LENGTH)
"""Slug identifying a crop, for example ``basil``."""

CropDisplayNameStr = name_type(MAX_CROP_DISPLAY_NAME_LENGTH)
ScientificNameStr = name_type(MAX_SCIENTIFIC_NAME_LENGTH)
RecipeCodeStr = slug_type(MAX_RECIPE_CODE_LENGTH)
RecipeNameStr = name_type(MAX_RECIPE_NAME_LENGTH)
StageCodeStr = slug_type(MAX_STAGE_CODE_LENGTH)
StageNameStr = name_type(MAX_STAGE_NAME_LENGTH)

RequirementValue = Annotated[
    Decimal,
    Field(allow_inf_nan=False),
    PlainSerializer(float, return_type=float, when_used="json"),
]
"""One agronomic value, in the requirement's own unit.

Held as ``Decimal`` so the value written to the ``numeric`` column is the one
the client sent, and serialised back as a JSON number rather than as the string
Pydantic would otherwise produce. This mirrors the control loop's thresholds and
a point's range bounds, which describe the same kind of quantity.

``allow_inf_nan=False`` is what keeps ``Infinity`` and ``NaN`` out: both survive
a ``numeric`` column, and a band bounded by infinity is not a band.
"""


class CropCreate(BaseModel):
    """Body accepted by ``POST /api/v1/crops``.

    ``status`` is not accepted. A crop that has just been registered is active,
    and a field a client could only set to one meaningful value is a field that
    will eventually be set to the other one by accident.
    """

    model_config = ConfigDict(extra="forbid")

    code: CropCodeStr
    display_name: CropDisplayNameStr
    scientific_name: ScientificNameStr | None = None


class CropRead(BaseModel):
    """Representation of a crop returned by every crop endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    code: str
    display_name: str
    scientific_name: str | None
    status: StatusEnum
    created_at: datetime


class TargetRequirementCreate(BaseModel):
    """One requirement inside a submitted stage.

    Every value field is optional here and none of them is optional in the
    domain: which ones are required is decided by ``requirement_kind``, and that
    rule is applied by the service so a wrong combination is reported as an
    invalid recipe rather than as a missing field.
    """

    model_config = ConfigDict(extra="forbid")

    metric_type: CodeStr
    requirement_kind: RequirementKind
    unit: UnitStr
    min_value: RequirementValue | None = None
    max_value: RequirementValue | None = None
    target_value: RequirementValue | None = None


class RecipeStageCreate(BaseModel):
    """The single stage of a submitted version.

    ``requirements`` is a list because a stage genuinely has several, so a
    missing, repeated or extra metric is a property of the *graph* and is
    reported as one. The stage itself is not a list: a version carries exactly
    one stage in this milestone, and offering a collection would advertise a
    shape the domain does not yet accept.
    """

    model_config = ConfigDict(extra="forbid")

    code: StageCodeStr
    name: StageNameStr
    sequence_number: int = DEFAULT_STAGE_SEQUENCE_NUMBER
    requirements: list[TargetRequirementCreate]


class RecipeVersionCreate(BaseModel):
    """The published version created together with its recipe.

    ``status`` and ``published_at`` are not accepted. A version is published by
    the act of creating it, and letting a client name either would make the
    catalog's one immutability guarantee a client's decision.
    """

    model_config = ConfigDict(extra="forbid")

    version_number: int = DEFAULT_VERSION_NUMBER
    stage: RecipeStageCreate


class GrowingRecipeCreate(BaseModel):
    """Body accepted by ``POST /api/v1/growing-recipes``.

    The whole graph arrives in one request because it is created in one
    transaction: a recipe without a version, or a version without its
    requirements, describes nothing and must never be reachable.
    """

    model_config = ConfigDict(extra="forbid")

    crop_id: UUID
    code: RecipeCodeStr
    name: RecipeNameStr
    version: RecipeVersionCreate


class TargetRequirementRead(BaseModel):
    """One requirement as it is returned inside a stage."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    recipe_stage_id: UUID
    metric_type: str
    requirement_kind: RequirementKind
    unit: str
    min_value: RequirementValue | None
    max_value: RequirementValue | None
    target_value: RequirementValue | None


class RecipeStageRead(BaseModel):
    """One stage with every requirement it carries."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    recipe_version_id: UUID
    code: str
    name: str
    sequence_number: int
    requirements: list[TargetRequirementRead]

    @classmethod
    def from_parts(
        cls,
        stage: RecipeStage,
        requirements: list[TargetRequirement],
    ) -> Self:
        """Build the stage representation from the stage and its requirements.

        Args:
            stage: The stored stage.
            requirements: Its requirements, already loaded by the caller in a
                deterministic order. They are passed in rather than reached
                through a relationship so that reading a page of recipes cannot
                turn into one query per stage.

        Returns:
            The stage with its requirements embedded.
        """
        return cls(
            id=stage.id,
            recipe_version_id=stage.recipe_version_id,
            code=stage.code,
            name=stage.name,
            sequence_number=stage.sequence_number,
            requirements=[
                TargetRequirementRead.model_validate(requirement) for requirement in requirements
            ],
        )


class RecipeVersionRead(BaseModel):
    """Response of ``GET /api/v1/recipe-versions/{recipe_version_id}``.

    The stage and its requirements are embedded rather than referenced. A
    consumer asking what a version says is asking for the numbers, and a
    representation that answered with identifiers alone would need private
    endpoints or database knowledge to be useful.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    recipe_id: UUID
    version_number: int
    status: RecipeVersionStatus
    published_at: datetime
    created_at: datetime
    stage: RecipeStageRead

    @classmethod
    def from_parts(cls, version: RecipeVersion, stage: RecipeStageRead) -> Self:
        """Build the version representation from the version and its stage.

        Args:
            version: The stored version.
            stage: Its already-assembled stage.

        Returns:
            The version with its stage embedded.
        """
        return cls(
            id=version.id,
            recipe_id=version.recipe_id,
            version_number=version.version_number,
            status=version.status,
            published_at=version.published_at,
            created_at=version.created_at,
            stage=stage,
        )


class GrowingRecipeRead(BaseModel):
    """Representation of a recipe returned by every recipe endpoint."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    crop_id: UUID
    code: str
    name: str
    status: StatusEnum
    created_at: datetime
    version: RecipeVersionRead

    @classmethod
    def from_parts(cls, recipe: GrowingRecipe, version: RecipeVersionRead) -> Self:
        """Build the recipe representation from the identity and its version.

        Args:
            recipe: The stored recipe identity.
            version: Its already-assembled published version.

        Returns:
            The complete recipe graph.
        """
        return cls(
            id=recipe.id,
            crop_id=recipe.crop_id,
            code=recipe.code,
            name=recipe.name,
            status=recipe.status,
            created_at=recipe.created_at,
            version=version,
        )
