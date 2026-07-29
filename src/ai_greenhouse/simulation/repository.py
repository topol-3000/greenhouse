"""Data access for persisted simulation runs and their zone configuration."""

from uuid import UUID

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point
from ai_greenhouse.simulation.models import SimulationRun, SimulationStatus
from ai_greenhouse.topology.models import ControlZone, ZonePointAssignment


class SimulationRunRepository:
    """Queries over ``simulation_runs`` and the configuration needed to create one."""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    def add(self, run: SimulationRun) -> None:
        """Stage a new run."""
        self._session.add(run)

    async def flush(self) -> None:
        """Flush pending changes without owning the transaction commit."""
        await self._session.flush()

    async def get_by_id(self, run_id: UUID) -> SimulationRun | None:
        """Load one run by identifier."""
        return await self._session.get(SimulationRun, run_id)

    async def get_for_update(self, run_id: UUID) -> SimulationRun | None:
        """Load and lock one run for a lifecycle change."""
        return await self._session.scalar(
            select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
        )

    async def get_zone(self, control_zone_id: UUID) -> ControlZone | None:
        """Load the zone selected for a run."""
        return await self._session.get(ControlZone, control_zone_id)

    async def list_active_zone_points(
        self,
        control_zone_id: UUID,
    ) -> list[Row[tuple[ZonePointAssignment, Point]]]:
        """Load active points assigned to a zone and their roles."""
        statement: Select[tuple[ZonePointAssignment, Point]] = (
            select(ZonePointAssignment, Point)
            .join(Point, Point.id == ZonePointAssignment.point_id)
            .where(
                ZonePointAssignment.control_zone_id == control_zone_id,
                Point.status == StatusEnum.ACTIVE,
            )
        )
        return list((await self._session.execute(statement)).all())

    async def has_running_for_zone(self, control_zone_id: UUID) -> bool:
        """Answer the concrete query served by the partial unique index."""
        statement = select(
            select(SimulationRun.id)
            .where(
                SimulationRun.control_zone_id == control_zone_id,
                SimulationRun.status == SimulationStatus.RUNNING,
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def list_page(
        self,
        params: PageParams,
        *,
        control_zone_id: UUID | None,
        status: SimulationStatus | None,
    ) -> tuple[list[SimulationRun], int]:
        """Return a filtered page in ``created_at DESC, id DESC`` order."""
        statement: Select[tuple[SimulationRun]] = select(SimulationRun)
        if control_zone_id is not None:
            statement = statement.where(SimulationRun.control_zone_id == control_zone_id)
        if status is not None:
            statement = statement.where(SimulationRun.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        window = (
            statement.order_by(SimulationRun.created_at.desc(), SimulationRun.id.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
        return list(await self._session.scalars(window)), int(total or 0)
