"""Domain failures raised by the agronomy module.

These subclass the shared hierarchy in ``ai_greenhouse.core.exceptions`` and
carry no HTTP knowledge. ``ai_greenhouse.api.errors`` maps them to responses.

A duplicate code is reported as ``crop_code_exists`` and ``recipe_code_exists``
rather than as the ``*_code_conflict`` the older modules use. The catalog's
public contract names these two codes, and inventing a third spelling of the
same answer would help nobody; the existing codes are untouched.
"""

from typing import Any
from uuid import UUID

from ai_greenhouse.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ReferencedEntityNotFoundError,
)

__all__ = [
    "CropCodeExistsError",
    "CropNotFoundError",
    "InvalidRecipeVersionError",
    "RecipeCodeExistsError",
    "RecipeNotFoundError",
    "RecipeVersionNotFoundError",
    "ReferencedCropNotFoundError",
]


class CropNotFoundError(NotFoundError):
    """No crop exists with the requested identifier."""

    code = "crop_not_found"

    def __init__(self, crop_id: UUID) -> None:
        """Report the missing crop.

        Args:
            crop_id: The identifier that could not be resolved.
        """
        super().__init__("Crop not found", details={"crop_id": str(crop_id)})


class ReferencedCropNotFoundError(ReferencedEntityNotFoundError):
    """A request body references a crop that does not exist.

    Distinct from :class:`CropNotFoundError` only in where the identifier came
    from. Both answer HTTP 404 with the same ``crop_not_found`` code, because
    both describe the same missing entity.
    """

    code = "crop_not_found"

    def __init__(self, crop_id: UUID) -> None:
        """Report the missing referenced crop.

        Args:
            crop_id: The identifier that could not be resolved.
        """
        super().__init__("Crop not found", details={"crop_id": str(crop_id)})


class CropCodeExistsError(ConflictError):
    """Another crop already uses the requested code."""

    code = "crop_code_exists"

    def __init__(self, crop_code: str) -> None:
        """Report the duplicate code.

        Args:
            crop_code: The code that is already taken.
        """
        super().__init__("Crop code already exists", details={"code": crop_code})


class RecipeNotFoundError(NotFoundError):
    """No growing recipe exists with the requested identifier."""

    code = "recipe_not_found"

    def __init__(self, recipe_id: UUID) -> None:
        """Report the missing recipe.

        Args:
            recipe_id: The identifier that could not be resolved.
        """
        super().__init__("Growing recipe not found", details={"recipe_id": str(recipe_id)})


class RecipeCodeExistsError(ConflictError):
    """Another growing recipe already uses the requested code."""

    code = "recipe_code_exists"

    def __init__(self, recipe_code: str) -> None:
        """Report the duplicate code.

        Args:
            recipe_code: The code that is already taken.
        """
        super().__init__("Growing recipe code already exists", details={"code": recipe_code})


class RecipeVersionNotFoundError(NotFoundError):
    """No recipe version exists with the requested identifier.

    Also raised when a recipe exists without its published version, which the
    atomic creation path rules out. It is reported rather than assumed away so
    that a graph broken by a direct database edit surfaces as a precise failure
    instead of a crash, exactly as a lost point projection does.
    """

    code = "recipe_version_not_found"

    def __init__(self, recipe_version_id: UUID) -> None:
        """Report the missing version.

        Args:
            recipe_version_id: The identifier that could not be resolved.
        """
        super().__init__(
            "Recipe version not found",
            details={"recipe_version_id": str(recipe_version_id)},
        )


class InvalidRecipeVersionError(DomainError):
    """The submitted graph is not a recipe version this milestone can publish.

    One code covers every structural refusal — the version number, the stage,
    the set of requirements and the values inside them — because they are all
    the same answer: what was submitted is not a publishable version. Which rule
    was broken is named in ``details.reason``, so a client can tell the cases
    apart without a code per rule, and adding a rule later does not add a code
    a client has to learn.
    """

    code = "invalid_recipe_version"
    http_status = 422

    def __init__(self, reason: str, **details: Any) -> None:
        """Report the rejected graph.

        Args:
            reason: Stable machine-readable slug naming the broken rule.
            **details: Further context, such as the metric the rule was broken
                on. Values are plain strings, numbers or lists of them; nothing
                from the database or the exception chain reaches a response.
        """
        super().__init__(
            "The submitted recipe version is not valid",
            details={"reason": reason, **details},
        )
