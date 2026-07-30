"""Minimal gateway configuration service for v1 Edge integration."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.gateways.exceptions import (
    GatewayPointConflictError,
)
from ai_greenhouse.gateways.models import Gateway, GatewayPoint
from ai_greenhouse.gateways.repository import GatewayRepository
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.topology.exceptions import SiteNotFoundError
from ai_greenhouse.topology.repository import SiteRepository


class GatewayConfigurationService:
    """Create the smallest gateway configuration needed by contract v1."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to one transaction."""
        self._gateways = GatewayRepository(session)
        self._sites = SiteRepository(session)

    async def create(
        self,
        *,
        site_id: UUID,
        point_ids: Sequence[UUID],
        gateway_id: UUID | None = None,
    ) -> Gateway:
        """Create an active gateway and its complete authorized point set."""
        site = await self._sites.get_by_id(site_id)
        if site is None:
            raise SiteNotFoundError(site_id)
        if site.status is not StatusEnum.ACTIVE:
            raise GatewayPointConflictError(next(iter(point_ids), site_id), "site_not_active")

        unique_ids = tuple(dict.fromkeys(point_ids))
        points = await self._gateways.get_points(unique_ids)
        for point_id in unique_ids:
            point = points.get(point_id)
            if point is None:
                raise ReferencedPointNotFoundError(point_id)
            if point.site_id != site_id:
                raise GatewayPointConflictError(point_id, "point_not_in_site")

        gateway = Gateway(id=gateway_id or uuid4(), site_id=site_id)
        self._gateways.add(gateway)
        for point_id in unique_ids:
            self._gateways.add_point(GatewayPoint(gateway_id=gateway.id, point_id=point_id))
        try:
            await self._gateways.flush()
        except IntegrityError as error:
            contested = next(iter(unique_ids), site_id)
            raise GatewayPointConflictError(contested, "point_already_authorized") from error
        return gateway
