"""ORM models of the agronomy module.

Two rules govern these tables and outrank any convenience:

1. They hold no operational reference. ``facility_id``, ``control_zone_id``,
   ``point_id``, ``gateway_id``, ``device_id`` and any command or simulation
   column must never appear here. A recipe says what an environment should be,
   never which equipment produces it.
2. A published version and everything below it is immutable. There is no
   ``updated_at`` on the version, the stage or the requirements, and no API
   writes to them after the aggregate is created, so a version read today is
   the version that was published.

The three levels below :class:`GrowingRecipe` are separate tables rather than
one JSON document: a requirement is addressed, constrained and made unique by
the database — one requirement per metric in a stage — and a JSON blob would
move all of that into application code that nothing outside this module could
rely on.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_greenhouse.core.types import MAX_CODE_LENGTH, MAX_UNIT_LENGTH
from ai_greenhouse.infrastructure.database.base import (
    Base,
    StatusMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_now,
)

MAX_CROP_CODE_LENGTH: int = 64
MAX_CROP_DISPLAY_NAME_LENGTH: int = 120
MAX_SCIENTIFIC_NAME_LENGTH: int = 160
MAX_RECIPE_CODE_LENGTH: int = 80
MAX_RECIPE_NAME_LENGTH: int = 160
MAX_STAGE_CODE_LENGTH: int = 64
MAX_STAGE_NAME_LENGTH: int = 120

REQUIREMENT_VALUE_SHAPE_CONSTRAINT: str = "value_shape"
"""Name of the check that ties a requirement's values to its kind.

The rule lives in the database and not only in the service because it is the
whole meaning of ``requirement_kind``: a ``range`` with a ``target_value``, or a
``duration_per_day`` with bounds, is not a requirement anyone can read.
"""


class RequirementKind(StrEnum):
    """Shape of the values a target requirement carries.

    The kind decides which of ``min_value``, ``max_value`` and ``target_value``
    are present, which is why the two are constrained together rather than
    trusting each column on its own.
    """

    RANGE = "range"
    DURATION_PER_DAY = "duration_per_day"


class RecipeVersionStatus(StrEnum):
    """Lifecycle of a recipe version.

    One member. Drafts, deprecation and publishing transitions are deliberately
    absent: a version exists only once it has been published, and it is an enum
    rather than a constant so the stored value carries a ``CHECK`` like every
    other enum column, and so a second state arrives as a member instead of as
    free text nothing validates.
    """

    PUBLISHED = "published"


class Crop(UUIDPrimaryKeyMixin, StatusMixin, Base):
    """A plant species or crop that recipes are written for.

    A crop is generic knowledge and belongs to no site: two installations
    growing basil refer to the same concept, which is why ``code`` is unique
    across the whole catalog rather than scoped to anything.

    There is no ``updated_at``: the catalog offers no update endpoint, so an
    instant that is never rewritten would carry nothing ``created_at`` does not
    already say.

    Attributes:
        code: Stable slug, unique across the catalog and immutable, for example
            ``basil``.
        display_name: Human-readable label, 1-120 characters after stripping.
        scientific_name: Botanical name when one is known, for example
            ``Ocimum basilicum``.
        status: Lifecycle state; a crop is archived rather than deleted.
        created_at: Instant the crop was registered.
    """

    __tablename__ = "crops"

    code: Mapped[str] = mapped_column(
        String(MAX_CROP_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(MAX_CROP_DISPLAY_NAME_LENGTH),
        nullable=False,
    )
    scientific_name: Mapped[str | None] = mapped_column(
        String(MAX_SCIENTIFIC_NAME_LENGTH),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"Crop(id={self.id!r}, code={self.code!r})"


class GrowingRecipe(UUIDPrimaryKeyMixin, StatusMixin, Base):
    """The stable identity of one way of growing a crop.

    The identity carries no agronomic value at all. What a recipe *asks for*
    lives in its versions, so that improving the numbers publishes a new version
    instead of silently changing what every past reader was told.

    ``code`` is unique across the catalog. The model is single-tenant: there is
    no organization to scope a recipe code to, and inventing one now would
    fix a boundary nothing has decided yet.

    Attributes:
        crop_id: The crop the recipe grows, fixed at creation. ``ON DELETE
            RESTRICT``: a crop with recipes cannot be removed underneath them.
        code: Stable slug, unique across the catalog, for example
            ``basil-default``.
        name: Human-readable label, 1-160 characters after stripping.
        status: Lifecycle state; a recipe is archived rather than deleted.
        created_at: Instant the recipe was registered.
    """

    __tablename__ = "growing_recipes"

    crop_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("crops.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(
        String(MAX_RECIPE_CODE_LENGTH),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(MAX_RECIPE_NAME_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return f"GrowingRecipe(id={self.id!r}, code={self.code!r})"


class RecipeVersion(UUIDPrimaryKeyMixin, Base):
    """One immutable, published statement of what a recipe asks for.

    Nothing updates a version once it exists: it has no ``updated_at``, the API
    exposes neither ``PATCH`` nor ``DELETE`` for it, and its stage and
    requirements are written in the same transaction it is. A consumer that read
    version 1 yesterday reads the same numbers today.

    Attributes:
        recipe_id: The recipe this version belongs to. ``ON DELETE RESTRICT``.
        version_number: Position in the recipe's history, counted from 1 and
            unique within the recipe.
        status: Fixed at ``published``; see :class:`RecipeVersionStatus`.
        published_at: Instant the version became readable, which is the instant
            it was created.
        created_at: Instant the row was written.
    """

    __tablename__ = "recipe_versions"
    __table_args__ = (
        UniqueConstraint(
            "recipe_id",
            "version_number",
            name="uq_recipe_versions_recipe_id_version_number",
        ),
        CheckConstraint("version_number > 0", name="version_number_positive"),
    )

    recipe_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("growing_recipes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    status: Mapped[RecipeVersionStatus] = enum_column(
        RecipeVersionStatus,
        constraint_name="status",
        nullable=False,
        default=RecipeVersionStatus.PUBLISHED,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"RecipeVersion(id={self.id!r}, recipe_id={self.recipe_id!r}, "
            f"version_number={self.version_number!r})"
        )


class RecipeStage(UUIDPrimaryKeyMixin, Base):
    """One phase of a version, with the environment it asks for.

    A stage carries no duration and no advancement rule. When a stage ends is a
    property of a running grow cycle, not of the recipe that describes it, and
    keeping the two apart is what lets one version drive cycles of different
    lengths.

    Attributes:
        recipe_version_id: The version the stage belongs to. ``ON DELETE
            RESTRICT``.
        code: Stable slug, unique within the version, for example
            ``vegetative``.
        name: Human-readable label, 1-120 characters after stripping.
        sequence_number: Position within the version, counted from 1 and unique
            within it.
    """

    __tablename__ = "recipe_stages"
    __table_args__ = (
        UniqueConstraint(
            "recipe_version_id",
            "code",
            name="uq_recipe_stages_recipe_version_id_code",
        ),
        UniqueConstraint(
            "recipe_version_id",
            "sequence_number",
            name="uq_recipe_stages_recipe_version_id_sequence_number",
        ),
        CheckConstraint("sequence_number > 0", name="sequence_number_positive"),
    )

    recipe_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("recipe_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(MAX_STAGE_CODE_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_STAGE_NAME_LENGTH), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer(), nullable=False)

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"RecipeStage(id={self.id!r}, "
            f"recipe_version_id={self.recipe_version_id!r}, code={self.code!r})"
        )


class TargetRequirement(UUIDPrimaryKeyMixin, Base):
    """What one metric should be during one stage.

    ``metric_type`` is the same vocabulary a point is described by — a slug such
    as ``air_temperature`` — and deliberately not a foreign key: a recipe is
    written before any growbox exists, and naming a point here would tie generic
    agronomy to one installation's hardware.

    Exactly one requirement exists per ``(stage, metric)``. That is a unique
    constraint and not a service rule, because two rows for the same metric
    would leave "what should the temperature be" unanswerable.

    Attributes:
        recipe_stage_id: The stage this requirement belongs to. ``ON DELETE
            RESTRICT``.
        metric_type: The quantity being required, as a slug.
        requirement_kind: Shape of the values; see :class:`RequirementKind`.
        unit: Unit the values are expressed in, for example ``°C`` or ``h/day``.
        min_value: Lower bound of a ``range``; ``NULL`` for any other kind.
        max_value: Upper bound of a ``range``; ``NULL`` for any other kind.
        target_value: The value asked for by a ``duration_per_day``; ``NULL``
            for a ``range``.
    """

    __tablename__ = "target_requirements"
    __table_args__ = (
        UniqueConstraint(
            "recipe_stage_id",
            "metric_type",
            name="uq_target_requirements_recipe_stage_id_metric_type",
        ),
        CheckConstraint(
            "(requirement_kind = 'range'"
            " AND min_value IS NOT NULL AND max_value IS NOT NULL"
            " AND target_value IS NULL AND min_value < max_value)"
            " OR (requirement_kind = 'duration_per_day'"
            " AND target_value IS NOT NULL"
            " AND min_value IS NULL AND max_value IS NULL"
            " AND target_value > 0 AND target_value <= 24)",
            name=REQUIREMENT_VALUE_SHAPE_CONSTRAINT,
        ),
    )

    recipe_stage_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("recipe_stages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_type: Mapped[str] = mapped_column(String(MAX_CODE_LENGTH), nullable=False)
    requirement_kind: Mapped[RequirementKind] = enum_column(
        RequirementKind,
        constraint_name="requirement_kind",
        nullable=False,
    )
    unit: Mapped[str] = mapped_column(String(MAX_UNIT_LENGTH), nullable=False)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)

    def __repr__(self) -> str:
        """Return a debug-friendly representation without the full row."""
        return (
            f"TargetRequirement(id={self.id!r}, "
            f"recipe_stage_id={self.recipe_stage_id!r}, metric_type={self.metric_type!r})"
        )
