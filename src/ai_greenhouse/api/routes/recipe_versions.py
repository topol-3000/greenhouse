"""HTTP endpoint for one published recipe version.

A version is addressable on its own because a consumer holds its identifier and
not always the recipe's. It answers with the stage and the requirements, so
nothing needs a private endpoint or database knowledge to display a complete
recipe.

There is no ``POST`` here: a version is published with its recipe, and no
``PATCH`` or ``DELETE``, because a published version is immutable.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.schemas import RecipeVersionRead
from ai_greenhouse.agronomy.service import GrowingRecipeService
from ai_greenhouse.api.dependencies import get_session

router: APIRouter = APIRouter(prefix="/recipe-versions", tags=["recipe versions"])


async def get_recipe_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GrowingRecipeService:
    """Build the recipe service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``GrowingRecipeService`` sharing the request's transaction.
    """
    return GrowingRecipeService(session)


RecipeServiceDep = Annotated[GrowingRecipeService, Depends(get_recipe_service)]


@router.get("/{recipe_version_id}", response_model=RecipeVersionRead)
async def get_recipe_version(
    recipe_version_id: UUID,
    service: RecipeServiceDep,
) -> RecipeVersionRead:
    """Read one published version with its stage and requirements.

    Args:
        recipe_version_id: Identifier of the version to read.
        service: The recipe service for this request.

    Returns:
        The complete version.
    """
    return await service.get_version(recipe_version_id)
