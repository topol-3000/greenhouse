"""Data access for the control module.

This layer holds SQLAlchemy statements and nothing else. Which zone may host a
loop, and which point may play which role in it, belong to
:mod:`ai_greenhouse.control.service`.
"""

from uuid import UUID

from sqlalchemy import Row, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.control.models import ControlLoop
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.models import ControlZone, ZonePointAssignment


class ControlLoopRepository:
    """Queries over ``control_loops`` and the configuration needed to create one.

    There is no update and no delete. A loop is immutable, so adding either
    would give the rest of the application a way past a rule the module exists
    to hold.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the caller's session.

        Args:
            session: The session opened by ``get_session`` for this request.
        """
        self._session: AsyncSession = session

    def add(self, control_loop: ControlLoop) -> None:
        """Stage a new loop for insertion on the next flush.

        Args:
            control_loop: The instance to persist.
        """
        self._session.add(control_loop)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        The commit is owned by the ``get_session`` dependency, so flushing here
        surfaces constraint violations while the caller can still translate
        them into a domain failure.
        """
        await self._session.flush()

    async def get_by_id(self, control_loop_id: UUID) -> ControlLoop | None:
        """Load one loop by primary key.

        Args:
            control_loop_id: Identifier to look up.

        Returns:
            The matching loop, or ``None`` when no row exists.
        """
        return await self._session.get(ControlLoop, control_loop_id)

    async def get_zone(self, control_zone_id: UUID) -> ControlZone | None:
        """Load the zone a loop is being configured for.

        Args:
            control_zone_id: The zone named by the request.

        Returns:
            The matching zone, or ``None`` when no row exists.
        """
        return await self._session.get(ControlZone, control_zone_id)

    async def list_zone_points(
        self,
        control_zone_id: UUID,
    ) -> list[Row[tuple[ZonePointAssignment, Point]]]:
        """Load the points assigned to a zone together with their roles.

        Archived points are returned as well. Whether an archived point may be
        wired into a loop is a domain rule, and answering it here would report
        an archived point as one that is simply not in the zone.

        Args:
            control_zone_id: The zone whose composition to read.

        Returns:
            One row per assignment, carrying the link and the point.
        """
        statement: Select[tuple[ZonePointAssignment, Point]] = (
            select(ZonePointAssignment, Point)
            .join(Point, Point.id == ZonePointAssignment.point_id)
            .where(ZonePointAssignment.control_zone_id == control_zone_id)
        )
        return list((await self._session.execute(statement)).all())

    async def has_loop_for_zone(self, control_zone_id: UUID) -> bool:
        """Answer the concrete query served by the unique constraint.

        Args:
            control_zone_id: The zone to check.

        Returns:
            ``True`` when the zone already has a loop.
        """
        statement = select(
            select(ControlLoop.id).where(ControlLoop.control_zone_id == control_zone_id).exists()
        )
        return bool(await self._session.scalar(statement))

    async def list_page(
        self,
        params: PageParams,
        *,
        control_zone_id: UUID | None,
    ) -> tuple[list[ControlLoop], int]:
        """Return one page of loops together with the unpaged total.

        Args:
            params: The resolved ``limit``/``offset`` window.
            control_zone_id: Restricts the result to one zone when given.

        Returns:
            A tuple of the page's loops, ordered by ``created_at ASC, id ASC``,
            and the total number of loops matching the filter.
        """
        statement: Select[tuple[ControlLoop]] = select(ControlLoop)
        if control_zone_id is not None:
            statement = statement.where(ControlLoop.control_zone_id == control_zone_id)
        return await paginate(self._session, statement, ControlLoop, params)
