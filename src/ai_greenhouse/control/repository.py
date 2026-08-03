"""Data access for the control module.

This layer holds SQLAlchemy statements and nothing else. Which zone may host a
loop, and which point may play which role in it, belong to
:mod:`ai_greenhouse.control.service`.
"""

from uuid import UUID

from sqlalchemy import Row, Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams, paginate
from ai_greenhouse.control.models import Command, CommandSource, CommandState, ControlLoop
from ai_greenhouse.gateways.models import GatewayPoint
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.models import (
    ControlZone,
    Facility,
    ZonePointAssignment,
    ZonePointRole,
)


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

    async def get_by_measurement_point(self, point_id: UUID) -> ControlLoop | None:
        """Load the loop a measured point drives, if it drives one.

        This is the query automation runs for every accepted current sample, and
        the one the index on ``measurement_point_id`` exists for.

        Args:
            point_id: The point a sample was recorded on.

        Returns:
            The loop watching that point, or ``None`` when no loop does.
        """
        return await self._session.scalar(
            select(ControlLoop).where(ControlLoop.measurement_point_id == point_id)
        )

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


class CommandRepository:
    """Queries over the ``commands`` table.

    Decision content is append-only and there is no delete. The one permitted
    update stores a pending command's terminal acknowledgement.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the caller's session.

        Args:
            session: The transaction the command shares with the samples it
                refers to.
        """
        self._session: AsyncSession = session

    def add(self, command: Command) -> None:
        """Stage a fully applied command for insertion on the next flush.

        Args:
            command: The instance to persist.
        """
        self._session.add(command)

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        Flushing here is what surfaces a duplicate idempotency key while the
        caller can still roll the application back to its savepoint.
        """
        await self._session.flush()

    async def get_by_id(self, command_id: UUID) -> Command | None:
        """Load one command by primary key.

        Args:
            command_id: Identifier to look up.

        Returns:
            The matching command, or ``None`` when no row exists.
        """
        return await self._session.get(Command, command_id)

    async def get_zone(self, control_zone_id: UUID) -> ControlZone | None:
        """Load the zone a manual command is addressed to.

        Args:
            control_zone_id: The zone named by the request.

        Returns:
            The matching zone, or ``None`` when no row exists.
        """
        return await self._session.get(ControlZone, control_zone_id)

    async def get_facility(self, facility_id: UUID) -> Facility | None:
        """Load the facility a zone belongs to.

        Read so that a manual command's target can be checked against the
        facility context of its zone rather than against the zone alone.

        Args:
            facility_id: The facility to resolve.

        Returns:
            The matching facility, or ``None`` when no row exists.
        """
        return await self._session.get(Facility, facility_id)

    async def get_point(self, point_id: UUID) -> Point | None:
        """Load one point named by a manual command request.

        Args:
            point_id: The point to resolve.

        Returns:
            The matching point, or ``None`` when no row exists.
        """
        return await self._session.get(Point, point_id)

    async def list_assignment_roles(
        self,
        control_zone_id: UUID,
        point_id: UUID,
    ) -> list[ZonePointRole]:
        """Read the parts one point plays in one zone.

        The same point can be assigned twice — a temperature that is both a
        primary measurement and a safety interlock — so the roles are returned
        as a set rather than as one answer.

        Args:
            control_zone_id: The zone whose composition to read.
            point_id: The point to look for in it.

        Returns:
            Every role that point holds in that zone, possibly empty.
        """
        return list(
            await self._session.scalars(
                select(ZonePointAssignment.role).where(
                    ZonePointAssignment.control_zone_id == control_zone_id,
                    ZonePointAssignment.point_id == point_id,
                )
            )
        )

    async def get_for_gateway_update(
        self,
        gateway_id: UUID,
        command_id: UUID,
    ) -> Command | None:
        """Lock a command only when it belongs to the named gateway."""
        return await self._session.scalar(
            select(Command)
            .where(Command.id == command_id, Command.gateway_id == gateway_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_pending_for_gateway(
        self,
        gateway_id: UUID,
        *,
        limit: int,
    ) -> list[Command]:
        """Return pending commands whose target and report points remain authorized."""
        target = GatewayPoint.__table__.alias("target_gateway_point")
        reported = GatewayPoint.__table__.alias("reported_gateway_point")
        statement = (
            select(Command)
            .join(
                target,
                (target.c.gateway_id == gateway_id)
                & (target.c.point_id == Command.target_point_id),
            )
            .join(
                reported,
                (reported.c.gateway_id == gateway_id)
                & (reported.c.point_id == Command.reported_point_id),
            )
            .where(
                Command.gateway_id == gateway_id,
                Command.state == CommandState.PENDING,
            )
            .order_by(Command.issued_at.asc(), Command.id.asc())
            .limit(limit)
        )
        return list(await self._session.scalars(statement))

    async def list_history(
        self,
        *,
        control_zone_id: UUID | None,
        control_loop_id: UUID | None,
        trigger_sample_id: UUID | None,
        target_point_id: UUID | None,
        source: CommandSource | None,
        idempotency_key: str | None,
        limit: int,
    ) -> list[Command]:
        """Read a bounded, newest-first window of commands.

        The complete order and limit are part of the statement. ``id DESC``
        breaks ties between commands written in the same instant, so repeated
        calls return the same order without Python sorting or slicing.

        Args:
            control_zone_id: Restricts the result to one zone when given.
            control_loop_id: Restricts the result to one loop when given.
            trigger_sample_id: Restricts the result to the commands one
                measurement caused when given.
            target_point_id: Restricts the result to one commanded point when
                given.
            source: Restricts the result to one author when given.
            idempotency_key: Restricts the result to the one command a key
                identifies when given. The unique index makes the answer zero
                or one row.
            limit: Maximum number of rows for PostgreSQL to return.

        Returns:
            Matching commands in ``created_at DESC, id DESC`` order.
        """
        statement: Select[tuple[Command]] = select(Command)
        if control_zone_id is not None:
            statement = statement.where(Command.control_zone_id == control_zone_id)
        if control_loop_id is not None:
            statement = statement.where(Command.control_loop_id == control_loop_id)
        if trigger_sample_id is not None:
            statement = statement.where(Command.trigger_sample_id == trigger_sample_id)
        if target_point_id is not None:
            statement = statement.where(Command.target_point_id == target_point_id)
        if source is not None:
            statement = statement.where(Command.source == source)
        if idempotency_key is not None:
            statement = statement.where(Command.idempotency_key == idempotency_key)
        statement = statement.order_by(
            Command.created_at.desc(),
            Command.id.desc(),
        ).limit(limit)
        return list(await self._session.scalars(statement))

    async def get_by_key(self, key: str) -> Command | None:
        """Load the one command an idempotency key identifies.

        Args:
            key: The idempotency key to resolve.

        Returns:
            The matching command, or ``None`` when no row carries that key.
        """
        return await self._session.scalar(select(Command).where(Command.idempotency_key == key))

    async def exists_with_key(self, key: str) -> bool:
        """Answer whether one decision has already been applied.

        Args:
            key: The derived idempotency key of the decision.

        Returns:
            ``True`` when a command with that key exists.
        """
        statement = select(select(Command.id).where(Command.idempotency_key == key).exists())
        return bool(await self._session.scalar(statement))

    async def insert_if_key_absent(self, command: Command) -> bool:
        """Atomically claim one idempotency key for one command.

        The unique index behind ``idempotency_key``, and not a check-then-insert
        window, is what decides which of two concurrent requests writes the row.
        The loser writes nothing at all — there is no second row to reconcile,
        and therefore no second delivery to the Edge — and reads the winner's
        command back through :meth:`get_by_key`.

        Args:
            command: The candidate row, fully built and validated.

        Returns:
            ``True`` when this call inserted the row, ``False`` when the key was
            already taken.
        """
        statement = (
            insert(Command)
            .values(
                id=command.id,
                source=command.source,
                idempotency_key=command.idempotency_key,
                control_zone_id=command.control_zone_id,
                control_loop_id=command.control_loop_id,
                trigger_sample_id=command.trigger_sample_id,
                target_point_id=command.target_point_id,
                reported_point_id=command.reported_point_id,
                gateway_id=command.gateway_id,
                desired_value=command.desired_value,
                state=command.state,
                result_control_sample_id=command.result_control_sample_id,
                result_status_sample_id=command.result_status_sample_id,
                issued_at=command.issued_at,
                executed_at=command.executed_at,
                acknowledged_at=command.acknowledged_at,
                rejection_reason=command.rejection_reason,
                created_at=command.created_at,
            )
            .on_conflict_do_nothing(index_elements=[Command.idempotency_key])
            .returning(Command.id)
        )
        return await self._session.scalar(statement) is not None
