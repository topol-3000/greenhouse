"""Data access for the points module.

This layer holds SQLAlchemy statements and nothing else. Invariants, error
translation and the decision to commit belong to
:mod:`ai_greenhouse.points.service`.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point, PointCurrentState, PointKind


class PointRepository:
    """Queries over the ``points`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, point: Point) -> None:
        """Stage a new point for insertion on the next flush.

        Args:
            point: The instance to persist.
        """
        self._session.add(point)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, point_id: UUID) -> Point | None:
        """Load a point by primary key.

        Args:
            point_id: Identifier to look up.

        Returns:
            The matching point, or ``None`` when no row exists.
        """
        return await self._session.get(Point, point_id)

    async def get_by_code(self, site_id: UUID, code: str) -> Point | None:
        """Load a point by its code within one site.

        Args:
            site_id: The site the code is scoped to.
            code: The slug to look up.

        Returns:
            The matching point, or ``None`` when the code is free on that site.
            The same code on another site is a different point and is not
            returned here.
        """
        return await self._session.scalar(
            select(Point).where(Point.site_id == site_id, Point.code == code)
        )

    async def list_page(
        self,
        params: PageParams,
        *,
        site_id: UUID | None = None,
        facility_id: UUID | None = None,
        point_kind: PointKind | None = None,
        metric_type: str | None = None,
        status: StatusEnum | None = None,
    ) -> tuple[list[Point], int]:
        """Return one page of points together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            site_id: Restricts the result to one site when given.
            facility_id: Restricts the result to one facility when given.
            point_kind: Restricts the result to one role of point when given.
            metric_type: Restricts the result to one measured quantity when
                given.
            status: Restricts the result to one lifecycle state when given.

        Returns:
            A tuple of the page's points, ordered by ``created_at ASC, id ASC``,
            and the total number of points matching the filters.
        """
        statement: Select[tuple[Point]] = select(Point)
        if site_id is not None:
            statement = statement.where(Point.site_id == site_id)
        if facility_id is not None:
            statement = statement.where(Point.facility_id == facility_id)
        if point_kind is not None:
            statement = statement.where(Point.point_kind == point_kind)
        if metric_type is not None:
            statement = statement.where(Point.metric_type == metric_type)
        if status is not None:
            statement = statement.where(Point.status == status)
        return await paginate(self._session, statement, Point, params)


class PointCurrentStateRepository:
    """Queries over the ``point_current_states`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, state: PointCurrentState) -> None:
        """Stage a new state projection for insertion on the next flush.

        Args:
            state: The instance to persist.
        """
        self._session.add(state)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_point_id(self, point_id: UUID) -> PointCurrentState | None:
        """Load the state projection of one point.

        Args:
            point_id: The point whose projection to load; it is also the
                table's primary key.

        Returns:
            The matching projection, or ``None`` when no row exists.
        """
        return await self._session.get(PointCurrentState, point_id)

    async def get_for_update(self, point_id: UUID) -> PointCurrentState | None:
        """Load the state projection of one point and lock its row.

        The lock is held until the transaction ends, which is what lets the
        telemetry service compare a sample's ``observed_at`` against the stored
        one and act on the answer without a second writer changing it in
        between. ``populate_existing`` is set because a row already in the
        session's identity map would otherwise be returned with the values it
        had before the lock was taken — a stale answer to the only question the
        lock was acquired to ask.

        Args:
            point_id: The point whose projection to lock.

        Returns:
            The matching projection, or ``None`` when no row exists.
        """
        statement: Select[tuple[PointCurrentState]] = (
            select(PointCurrentState)
            .where(PointCurrentState.point_id == point_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(statement)
