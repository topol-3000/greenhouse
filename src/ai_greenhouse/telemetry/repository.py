"""Data access for the telemetry module.

This layer holds SQLAlchemy statements and nothing else. Which sample may be
recorded, and what a re-delivered one means, belong to
:mod:`ai_greenhouse.telemetry.service`.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.telemetry.models import TelemetrySample


class TelemetrySampleRepository:
    """Queries over the ``telemetry_samples`` table.

    The table is append-only, so there is no update and no delete here. Adding
    one would give the rest of the application a way to rewrite history that the
    module exists to deny.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the request-scoped session.

        Args:
            session: The session opened by ``get_session`` for this request, or
                by the caller driving one simulation step.
        """
        self._session: AsyncSession = session

    async def list_history(
        self,
        point_id: UUID,
        *,
        observed_from: datetime | None,
        observed_to: datetime | None,
        limit: int,
    ) -> list[TelemetrySample]:
        """Read a bounded, newest-first history for one point.

        The complete order and limit are part of the SQL statement. In
        particular, ``id DESC`` breaks ties between samples observed at the
        same instant, so repeated calls return the same order without Python
        sorting or slicing.

        Args:
            point_id: Point whose samples to read.
            observed_from: Inclusive lower bound on ``observed_at``.
            observed_to: Inclusive upper bound on ``observed_at``.
            limit: Maximum number of rows for PostgreSQL to return.

        Returns:
            Matching samples in ``observed_at DESC, id DESC`` order.
        """
        statement: Select[tuple[TelemetrySample]] = select(TelemetrySample).where(
            TelemetrySample.point_id == point_id
        )
        if observed_from is not None:
            statement = statement.where(TelemetrySample.observed_at >= observed_from)
        if observed_to is not None:
            statement = statement.where(TelemetrySample.observed_at <= observed_to)
        statement = statement.order_by(
            TelemetrySample.observed_at.desc(),
            TelemetrySample.id.desc(),
        ).limit(limit)
        return list(await self._session.scalars(statement))

    async def insert_if_absent(self, sample: TelemetrySample) -> bool:
        """Insert one sample unless its identifier is already taken.

        Written as ``INSERT ... ON CONFLICT DO NOTHING`` rather than as a read
        followed by an insert: the check and the insert are then one statement,
        so two producers replaying the same sample at the same moment cannot
        both find the row absent. The instance is never added to the session,
        which is what keeps the conflicting row from being flushed a second time
        as an ordinary ORM insert.

        Args:
            sample: The row to write. Fully populated by the service, including
                the identifier the producer chose.

        Returns:
            ``True`` when the row was written, ``False`` when a row with that
            identifier already existed.
        """
        values: dict[str, Any] = {
            "id": sample.id,
            "point_id": sample.point_id,
            "simulation_run_id": sample.simulation_run_id,
            "value": sample.value,
            "unit": sample.unit,
            "observed_at": sample.observed_at,
            "received_at": sample.received_at,
            "quality": sample.quality,
        }
        statement: Insert = (
            insert(TelemetrySample)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[TelemetrySample.id])
            .returning(TelemetrySample.id)
        )
        return await self._session.scalar(statement) is not None
