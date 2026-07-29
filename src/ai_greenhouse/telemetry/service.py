"""Telemetry business rules and the single write boundary of the system.

This module must not import FastAPI. It raises the domain failures declared in
:mod:`ai_greenhouse.telemetry.exceptions` and, for a sample naming a point that
does not exist, the one declared by the points module, which
``ai_greenhouse.api.errors`` turns into responses.

Transactions: the service flushes so that constraint violations surface while
they can still be translated into a domain failure. The final commit or rollback
belongs to the caller — the ``get_session`` request dependency, or the
simulation runtime's per-step session. The sample insert and the state update
therefore happen inside one transaction: a step that fails after the insert
takes the insert down with it, and leaves neither an orphan sample nor a state
row updated halfway.

:meth:`TelemetryService.record_sample` is the *only* code path in the project
that writes ``point_current_states``. Not a trigger, not the simulator, not a
repository reached from somewhere else. Both properties the projection has to
hold — that a re-delivered sample changes nothing, and that a late sample does
not roll the current value back — are decisions rather than constraints, and a
decision made in one place is a decision that can be trusted.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import (
    PointNotFoundError,
    PointStateNotFoundError,
    ReferencedPointNotFoundError,
)
from ai_greenhouse.points.models import Point, PointCurrentState, PointDataType
from ai_greenhouse.points.repository import PointCurrentStateRepository, PointRepository
from ai_greenhouse.telemetry.exceptions import (
    ArchivedPointError,
    InvalidTelemetryWindowError,
    TelemetryValueTypeError,
)
from ai_greenhouse.telemetry.models import TelemetrySample
from ai_greenhouse.telemetry.repository import TelemetrySampleRepository
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord


class TelemetryService:
    """Admits measured values, and keeps the current-state projection true."""

    def __init__(self, session: AsyncSession) -> None:
        """Build the service and its repositories around one session.

        The point and state repositories are held as well: recording a sample
        has to resolve the point it names and lock the projection it updates,
        and both reads belong in the data-access layer.

        Args:
            session: The session of the request or simulation step being served.
        """
        self._samples: TelemetrySampleRepository = TelemetrySampleRepository(session)
        self._points: PointRepository = PointRepository(session)
        self._states: PointCurrentStateRepository = PointCurrentStateRepository(session)

    async def list_history(
        self,
        point_id: UUID,
        *,
        observed_from: datetime | None,
        observed_to: datetime | None,
        limit: int,
    ) -> list[TelemetrySample]:
        """Read one point's samples inside an optional inclusive time window.

        Args:
            point_id: Point whose history was requested.
            observed_from: Inclusive lower bound on measurement time.
            observed_to: Inclusive upper bound on measurement time.
            limit: Maximum number of samples to return.

        Returns:
            Samples in deterministic newest-first order.

        Raises:
            PointNotFoundError: If the path names no point.
            InvalidTelemetryWindowError: If the lower bound is after the upper
                bound.
        """
        point: Point | None = await self._points.get_by_id(point_id)
        if point is None:
            raise PointNotFoundError(point_id)
        if observed_from is not None and observed_to is not None and observed_from > observed_to:
            raise InvalidTelemetryWindowError
        return await self._samples.list_history(
            point_id,
            observed_from=observed_from,
            observed_to=observed_to,
            limit=limit,
        )

    async def record_sample(self, record: TelemetrySampleRecord) -> None:
        """Record one measurement and advance the point's state if it is newer.

        The steps run in this order for a reason. The point is resolved and the
        value judged against it before anything is written, so a rejected sample
        leaves no trace. The insert then decides whether there is any work left:
        a sample already recorded is a re-delivery, and returning there is what
        keeps a replaying producer from bumping ``revision`` a second time. Only
        then is the state row locked, because the lock is held to the end of the
        transaction and there is no reason to hold it across the insert.

        The projection is replaced only by a *strictly* newer measurement. A
        sample arriving late — a buffered gateway flushing, a step replayed out
        of sequence — is still recorded in full, and the current value stays the
        most recent one. ``revision`` therefore counts actual replacements, and
        never decreases.

        Args:
            record: The measurement offered by the producer. Its ``unit`` is
                ignored in favour of the point's.

        Raises:
            ReferencedPointNotFoundError: If no point has that identifier.
            ArchivedPointError: If the point is archived.
            TelemetryValueTypeError: If the value contradicts the point's
                ``data_type``, ``null`` included.
            PointStateNotFoundError: If the point exists without a projection,
                which the schema and the point creation path together rule out.
        """
        point: Point | None = await self._points.get_by_id(record.point_id)
        if point is None:
            raise ReferencedPointNotFoundError(record.point_id)
        if point.status is not StatusEnum.ACTIVE:
            raise ArchivedPointError(record.point_id)

        self._check_value_type(point_id=point.id, data_type=point.data_type, value=record.value)

        sample = TelemetrySample(
            id=record.id,
            point_id=point.id,
            simulation_run_id=record.simulation_run_id,
            value=record.value,
            unit=point.unit,
            observed_at=record.observed_at,
            received_at=record.received_at,
            quality=record.quality,
        )
        if not await self._samples.insert_if_absent(sample):
            return

        state: PointCurrentState | None = await self._states.get_for_update(point.id)
        if state is None:
            raise PointStateNotFoundError(point.id)
        if state.observed_at is not None and record.observed_at <= state.observed_at:
            return

        state.value = record.value
        state.observed_at = record.observed_at
        state.received_at = record.received_at
        state.quality = record.quality
        state.revision += 1
        await self._states.flush()

    @staticmethod
    def _check_value_type(*, point_id: UUID, data_type: PointDataType, value: Any) -> None:
        """Check a submitted value against the type the point declares.

        ``bool`` is rejected for an ``integer`` point although Python calls it
        one. A point declared ``integer`` counts something, and admitting
        ``True`` as ``1`` would put a value into the history that no consumer
        reading the point's declared type expects to find there. An ``int`` is
        accepted for a ``float`` point in the other direction, because JSON
        draws no line there and ``22`` is a perfectly good temperature.

        Args:
            point_id: The point the value was addressed to, for the failure.
            data_type: The type that point declares.
            value: The submitted value.

        Raises:
            TelemetryValueTypeError: If the value is of another type, or is
                ``None``.
        """
        match data_type:
            case PointDataType.FLOAT:
                accepted: bool = isinstance(value, int | float) and not isinstance(value, bool)
            case PointDataType.INTEGER:
                accepted = isinstance(value, int) and not isinstance(value, bool)
            case PointDataType.BOOLEAN:
                accepted = isinstance(value, bool)
            case PointDataType.STRING:
                accepted = isinstance(value, str)
            case _:
                accepted = False
        if not accepted:
            raise TelemetryValueTypeError(point_id, data_type.value, value)
