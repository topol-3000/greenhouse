"""Database access for Edge producer-message identities."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.edge.models import EdgeTelemetryMessage


class EdgeTelemetryMessageRepository:
    """Race-safe operations over the producer-message ledger."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the envelope transaction."""
        self._session = session

    async def get(
        self,
        gateway_id: UUID,
        message_id: UUID,
    ) -> EdgeTelemetryMessage | None:
        """Load one producer identity."""
        return await self._session.get(
            EdgeTelemetryMessage,
            {"gateway_id": gateway_id, "message_id": message_id},
        )

    async def get_many(
        self,
        gateway_id: UUID,
        message_ids: Sequence[UUID],
    ) -> dict[UUID, EdgeTelemetryMessage]:
        """Load all existing identities for one envelope."""
        if not message_ids:
            return {}
        statement: Select[tuple[EdgeTelemetryMessage]] = select(EdgeTelemetryMessage).where(
            EdgeTelemetryMessage.gateway_id == gateway_id,
            EdgeTelemetryMessage.message_id.in_(message_ids),
        )
        rows = await self._session.scalars(statement)
        return {row.message_id: row for row in rows}

    async def insert_if_absent(
        self,
        *,
        gateway_id: UUID,
        message_id: UUID,
        sample_id: UUID,
        content: dict[str, Any],
        received_at: datetime,
    ) -> bool:
        """Claim a gateway-scoped identity atomically."""
        statement: Insert = (
            insert(EdgeTelemetryMessage)
            .values(
                gateway_id=gateway_id,
                message_id=message_id,
                sample_id=sample_id,
                content=content,
                received_at=received_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    EdgeTelemetryMessage.gateway_id,
                    EdgeTelemetryMessage.message_id,
                ]
            )
            .returning(EdgeTelemetryMessage.message_id)
        )
        return await self._session.scalar(statement) is not None
