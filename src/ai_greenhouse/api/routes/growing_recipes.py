"""HTTP endpoints for growing recipes.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.agronomy`.

There is no ``PATCH`` and no ``DELETE``, here or under a version, a stage or a
requirement. A published version is immutable: correcting it means publishing
another one, and a catalog that let a version be edited would leave every grow
that ran against it describing something that no longer exists.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.schemas import (
    GrowingRecipeCreate,
    GrowingRecipeRead,
    RecipeCodeStr,
)
from ai_greenhouse.agronomy.service import GrowingRecipeService
from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams

router: APIRouter = APIRouter(prefix="/growing-recipes", tags=["growing recipes"])


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


@router.post("", response_model=GrowingRecipeRead, status_code=status.HTTP_201_CREATED)
async def create_growing_recipe(
    payload: GrowingRecipeCreate,
    service: RecipeServiceDep,
) -> GrowingRecipeRead:
    """Publish a recipe, its first version, its stage and its requirements at once.

    The whole graph is created in one transaction: a rejected requirement
    leaves no recipe, no version and no stage behind.

    Args:
        payload: The recipe and the version to publish with it.
        service: The recipe service for this request.

    Returns:
        The complete created graph, with HTTP 201.
    """
    return await service.create_recipe(payload)


@router.get("", response_model=Page[GrowingRecipeRead])
async def list_growing_recipes(
    service: RecipeServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    code: Annotated[
        RecipeCodeStr | None,
        Query(description="Restrict the result to the recipe with this exact code."),
    ] = None,
    crop_id: Annotated[
        UUID | None,
        Query(description="Restrict the result to the recipes of one crop."),
    ] = None,
) -> Page[GrowingRecipeRead]:
    """List recipes with their complete published graph, oldest first.

    Args:
        service: The recipe service for this request.
        params: The requested collection window.
        code: Optional ``code`` filter from the query string.
        crop_id: Optional ``crop_id`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    recipes, total = await service.list_recipes(params, code=code, crop_id=crop_id)
    return Page[GrowingRecipeRead](
        items=recipes,
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{recipe_id}", response_model=GrowingRecipeRead)
async def get_growing_recipe(
    recipe_id: UUID,
    service: RecipeServiceDep,
) -> GrowingRecipeRead:
    """Read one recipe with its version, stage and requirements.

    Args:
        recipe_id: Identifier of the recipe to read.
        service: The recipe service for this request.

    Returns:
        The complete recipe graph.
    """
    return await service.get_recipe(recipe_id)
