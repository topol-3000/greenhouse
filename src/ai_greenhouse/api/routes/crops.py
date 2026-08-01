"""HTTP endpoints for crops.

This layer parses the request, picks the status code and serialises the result.
It contains no SQLAlchemy statement and no business rule; both live in
:mod:`ai_greenhouse.agronomy`.

There is no ``PATCH`` and no ``DELETE``. A crop is a stable entry in a shared
catalog: recipes are written against it, and renaming or removing one would
change what every recipe below it refers to.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.agronomy.models import Crop
from ai_greenhouse.agronomy.schemas import CropCodeStr, CropCreate, CropRead
from ai_greenhouse.agronomy.service import CropService
from ai_greenhouse.api.dependencies import get_session
from ai_greenhouse.api.pagination import Page, PageParams

router: APIRouter = APIRouter(prefix="/crops", tags=["crops"])


async def get_crop_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CropService:
    """Build the crop service on the request-scoped session.

    Args:
        session: The session opened for this request.

    Returns:
        A ``CropService`` sharing the request's transaction.
    """
    return CropService(session)


CropServiceDep = Annotated[CropService, Depends(get_crop_service)]


@router.post("", response_model=CropRead, status_code=status.HTTP_201_CREATED)
async def create_crop(payload: CropCreate, service: CropServiceDep) -> CropRead:
    """Register a crop in the catalog.

    Args:
        payload: The crop to create.
        service: The crop service for this request.

    Returns:
        The created crop, with HTTP 201.
    """
    crop: Crop = await service.create_crop(payload)
    return CropRead.model_validate(crop)


@router.get("", response_model=Page[CropRead])
async def list_crops(
    service: CropServiceDep,
    params: Annotated[PageParams, Depends(PageParams)],
    code: Annotated[
        CropCodeStr | None,
        Query(description="Restrict the result to the crop with this exact code."),
    ] = None,
) -> Page[CropRead]:
    """List crops, oldest first.

    Args:
        service: The crop service for this request.
        params: The requested collection window.
        code: Optional ``code`` filter from the query string.

    Returns:
        The paginated envelope, ordered by ``created_at ASC, id ASC``.
    """
    crops, total = await service.list_crops(params, code=code)
    return Page[CropRead](
        items=[CropRead.model_validate(crop) for crop in crops],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/{crop_id}", response_model=CropRead)
async def get_crop(crop_id: UUID, service: CropServiceDep) -> CropRead:
    """Read one crop, archived ones included.

    Args:
        crop_id: Identifier of the crop to read.
        service: The crop service for this request.

    Returns:
        The requested crop.
    """
    crop: Crop = await service.get_crop(crop_id)
    return CropRead.model_validate(crop)
