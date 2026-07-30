"""Database access for gateway identity and point ownership."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ai_greenhouse.gateways.models import Gateway, GatewayPoint
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.models import Point


class GatewayRepository:
    """Queries over gateways and their explicit point set."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to one transaction."""
        self._session = session

    def add(self, gateway: Gateway) -> None:
        """Stage a gateway."""
        self._session.add(gateway)

    def add_point(self, gateway_point: GatewayPoint) -> None:
        """Stage one point authorization."""
        self._session.add(gateway_point)

    async def flush(self) -> None:
        """Flush staged configuration."""
        await self._session.flush()

    async def get_by_id(self, gateway_id: UUID, *, for_update: bool = False) -> Gateway | None:
        """Load a gateway, optionally locking it."""
        statement: Select[tuple[Gateway]] = select(Gateway).where(Gateway.id == gateway_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_authorized_points(self, gateway_id: UUID) -> list[UUID]:
        """Return the gateway's explicit point identifiers."""
        return list(
            await self._session.scalars(
                select(GatewayPoint.point_id).where(GatewayPoint.gateway_id == gateway_id)
            )
        )

    async def owns_point(self, gateway_id: UUID, point_id: UUID) -> bool:
        """Answer whether one point is explicitly authorized."""
        return bool(
            await self._session.scalar(
                select(
                    select(GatewayPoint.point_id)
                    .where(
                        GatewayPoint.gateway_id == gateway_id,
                        GatewayPoint.point_id == point_id,
                    )
                    .exists()
                )
            )
        )

    async def gateway_for_command_points(
        self,
        target_point_id: UUID,
        reported_point_id: UUID,
    ) -> Gateway | None:
        """Find the active gateway owning both sides of a command."""
        target = aliased(GatewayPoint)
        reported = aliased(GatewayPoint)
        return await self._session.scalar(
            select(Gateway)
            .join(
                target,
                and_(
                    target.gateway_id == Gateway.id,
                    target.point_id == target_point_id,
                ),
            )
            .join(
                reported,
                and_(
                    reported.gateway_id == Gateway.id,
                    reported.point_id == reported_point_id,
                ),
            )
            .where(Gateway.status == StatusEnum.ACTIVE)
        )

    async def get_points(self, point_ids: Sequence[UUID]) -> dict[UUID, Point]:
        """Load a set of points keyed by identity."""
        if not point_ids:
            return {}
        points = await self._session.scalars(select(Point).where(Point.id.in_(point_ids)))
        return {point.id: point for point in points}
