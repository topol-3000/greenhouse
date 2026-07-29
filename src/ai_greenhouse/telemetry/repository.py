"""Data access for the telemetry module.

This layer holds SQLAlchemy statements and nothing else. Which sample may be
recorded, and what a re-delivered one means, belong to
:mod:`ai_greenhouse.telemetry.service`.
"""

from typing import Any

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
