"""Collection window and envelope shared by every list endpoint."""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.infrastructure.database.base import Base

DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 200


class PageParams:
    """Requested collection window.

    ``limit`` is clamped into ``[MIN_LIMIT, MAX_LIMIT]`` so an oversized page
    request degrades to the maximum page instead of failing. ``offset`` is
    rejected when negative, because there is no sensible value to fall back to.
    """

    def __init__(
        self,
        limit: Annotated[
            int,
            Query(description=f"Maximum number of items to return, clamped to {MAX_LIMIT}."),
        ] = DEFAULT_LIMIT,
        offset: Annotated[
            int,
            Query(ge=0, description="Number of items to skip."),
        ] = 0,
    ) -> None:
        if offset < 0:
            raise ValueError("offset must not be negative")
        self.limit = min(max(limit, MIN_LIMIT), MAX_LIMIT)
        self.offset = offset

    def __repr__(self) -> str:
        return f"PageParams(limit={self.limit}, offset={self.offset})"


class Page[ItemT](BaseModel):
    """Envelope returned by every collection endpoint."""

    model_config = ConfigDict(frozen=True)

    items: list[ItemT]
    total: int
    limit: int
    offset: int


async def paginate[ModelT: Base](
    session: AsyncSession,
    statement: Select[tuple[ModelT]],
    entity: type[ModelT],
    params: PageParams,
) -> tuple[list[ModelT], int]:
    """Apply the collection window to ``statement`` and return items with the total.

    Ordering is always ``created_at ASC, id ASC`` so paging over an unchanged
    collection never repeats or skips a row.
    """
    total = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    window = (
        statement.order_by(entity.created_at.asc(), entity.id.asc())
        .limit(params.limit)
        .offset(params.offset)
    )
    items = list(await session.scalars(window))
    return items, int(total or 0)
