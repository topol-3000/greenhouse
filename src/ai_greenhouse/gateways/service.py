"""Application use cases for gateway identity and point authorization."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.gateways.exceptions import (
    GatewayCodeNotFoundError,
    GatewayConfigurationConflictError,
    GatewayInactiveError,
    GatewayNotFoundError,
    GatewayPointConflictError,
    GatewaySiteInactiveError,
)
from ai_greenhouse.gateways.models import Gateway
from ai_greenhouse.gateways.repository import GatewayRepository
from ai_greenhouse.gateways.schemas import GatewayCreate
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import ReferencedPointNotFoundError
from ai_greenhouse.points.models import Point
from ai_greenhouse.topology.exceptions import SiteNotFoundError
from ai_greenhouse.topology.models import Site
from ai_greenhouse.topology.repository import SiteRepository


@dataclass(frozen=True, slots=True)
class PointAuthorizationResult:
    """Application result for one distinct requested point."""

    point_id: UUID
    newly_authorized: bool


def configuration_conflicts(
    gateway: Gateway,
    *,
    expected_site_id: UUID,
) -> list[dict[str, str]]:
    """Return normalized expected/actual differences for a stable gateway code."""
    conflicts: list[dict[str, str]] = []
    if gateway.site_id != expected_site_id:
        conflicts.append(
            {
                "field": "site_id",
                "expected": str(expected_site_id),
                "actual": str(gateway.site_id),
            }
        )
    if gateway.status is not StatusEnum.ACTIVE:
        conflicts.append(
            {
                "field": "status",
                "expected": StatusEnum.ACTIVE.value,
                "actual": gateway.status.value,
            }
        )
    return conflicts


class GatewayConfigurationService:
    """Provision gateways and manage their logical-point authorization."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the service to one request-owned transaction."""
        self._gateways = GatewayRepository(session)
        self._sites = SiteRepository(session)

    async def resolve_or_create(
        self,
        payload: GatewayCreate,
        *,
        gateway_id: UUID | None = None,
    ) -> tuple[Gateway, bool]:
        """Create a stable gateway or return the compatible existing resource.

        The code's unique index is claimed through one ``ON CONFLICT``
        statement. Concurrent equivalent requests therefore converge on one
        row, while concurrent incompatible requests resolve that same row and
        receive field-level conflict details.
        """
        existing = await self._gateways.get_by_code(payload.code)
        if existing is not None:
            self._ensure_compatible(existing, expected_site_id=payload.site_id)
            return existing, False

        site: Site | None = await self._sites.get_by_id(payload.site_id)
        if site is None:
            raise SiteNotFoundError(payload.site_id)
        if site.status is not StatusEnum.ACTIVE:
            raise GatewaySiteInactiveError(payload.site_id)

        candidate = Gateway(
            id=gateway_id or uuid4(),
            code=payload.code,
            site_id=payload.site_id,
        )
        created = await self._gateways.insert_if_code_absent(candidate)
        gateway = await self._gateways.get_by_code(payload.code)
        if gateway is None:
            raise RuntimeError("Gateway code claim completed without a readable gateway")
        self._ensure_compatible(gateway, expected_site_id=payload.site_id)
        return gateway, created

    async def get_gateway(self, gateway_id: UUID) -> Gateway:
        """Read one gateway by its operational UUID."""
        gateway = await self._gateways.get_by_id(gateway_id)
        if gateway is None:
            raise GatewayNotFoundError(gateway_id)
        return gateway

    async def get_gateway_by_code(self, code: str) -> Gateway:
        """Resolve one gateway by its stable provisioning code."""
        gateway = await self._gateways.get_by_code(code)
        if gateway is None:
            raise GatewayCodeNotFoundError(code)
        return gateway

    async def list_authorized_points(self, gateway_id: UUID) -> list[UUID]:
        """Read the normalized current authorization set."""
        await self.get_gateway(gateway_id)
        return await self._gateways.list_authorized_points(gateway_id)

    async def authorize_points(
        self,
        gateway_id: UUID,
        point_ids: Sequence[UUID],
    ) -> list[PointAuthorizationResult]:
        """Add distinct points without removing any existing authorization."""
        gateway = await self._gateways.get_by_id(gateway_id, for_update=True)
        if gateway is None:
            raise GatewayNotFoundError(gateway_id)
        if gateway.status is not StatusEnum.ACTIVE:
            raise GatewayInactiveError(gateway_id)

        site = await self._sites.get_by_id(gateway.site_id)
        if site is None:
            raise SiteNotFoundError(gateway.site_id)
        if site.status is not StatusEnum.ACTIVE:
            raise GatewaySiteInactiveError(gateway.site_id)

        unique_ids = tuple(dict.fromkeys(point_ids))
        points = await self._gateways.get_points(unique_ids)
        for point_id in unique_ids:
            point: Point | None = points.get(point_id)
            if point is None:
                raise ReferencedPointNotFoundError(point_id)
            if point.status is not StatusEnum.ACTIVE:
                raise GatewayPointConflictError(
                    point_id,
                    "point_not_active",
                    gateway_id=gateway_id,
                )
            if point.site_id != gateway.site_id:
                raise GatewayPointConflictError(
                    point_id,
                    "point_not_in_site",
                    gateway_id=gateway_id,
                )

        self._ensure_points_available(
            gateway_id,
            await self._gateways.point_owners(unique_ids),
        )
        inserted = await self._gateways.authorize_points_if_absent(gateway_id, unique_ids)
        self._ensure_points_available(
            gateway_id,
            await self._gateways.point_owners(unique_ids),
        )
        return [
            PointAuthorizationResult(
                point_id=point_id,
                newly_authorized=point_id in inserted,
            )
            for point_id in unique_ids
        ]

    async def create(
        self,
        *,
        site_id: UUID,
        point_ids: Sequence[UUID],
        gateway_id: UUID | None = None,
    ) -> Gateway:
        """Create the internal KAN-54 gateway shape through the same use cases.

        Existing in-process callers did not have a stable code. A deterministic
        code derived from the chosen operational UUID preserves that interface
        while ensuring every row is manageable through the new HTTP boundary.
        """
        resolved_id = gateway_id or uuid4()
        gateway, _ = await self.resolve_or_create(
            GatewayCreate(code=f"gateway-{resolved_id}", site_id=site_id),
            gateway_id=resolved_id,
        )
        await self.authorize_points(gateway.id, point_ids)
        return gateway

    @staticmethod
    def _ensure_compatible(gateway: Gateway, *, expected_site_id: UUID) -> None:
        """Raise the documented conflict when a stable code cannot be reused."""
        conflicts = configuration_conflicts(gateway, expected_site_id=expected_site_id)
        if conflicts:
            raise GatewayConfigurationConflictError(
                gateway_id=gateway.id,
                code=gateway.code,
                conflicts=conflicts,
            )

    @staticmethod
    def _ensure_points_available(gateway_id: UUID, owners: dict[UUID, UUID]) -> None:
        """Reject a point already owned by a different gateway."""
        for point_id, owner_id in owners.items():
            if owner_id != gateway_id:
                raise GatewayPointConflictError(
                    point_id,
                    "point_already_authorized",
                    gateway_id=gateway_id,
                    conflicting_gateway_id=owner_id,
                )
