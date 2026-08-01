"""Resolve the temperature band one control-loop evaluation consumes.

The Growing Recipe describes required conditions, while ``hysteresis-v1``
describes how a fan reacts to them.  ``RuntimeTarget`` is the boundary between
those concerns: cultivation snapshots a recipe requirement at activation and
control consumes only that snapshot, never the live recipe graph.

Resolution happens once per evaluation.  Bounds and provenance travel in one
immutable value so a command cannot record a different source from the one its
decision used.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.control.models import ControlLoop
from ai_greenhouse.cultivation.models import (
    RUNTIME_TARGET_UNIT,
    RuntimeTarget,
    RuntimeTargetMetric,
)
from ai_greenhouse.cultivation.repository import RuntimeTargetRepository


class EffectiveBoundsSource(StrEnum):
    """The persisted configuration one evaluation used."""

    CONTROL_LOOP = "control_loop"
    RUNTIME_TARGET = "runtime_target"


@dataclass(frozen=True, slots=True)
class EffectiveTemperatureBounds:
    """One evaluation's inseparable temperature bounds and provenance."""

    lower: Decimal
    upper: Decimal
    source: EffectiveBoundsSource
    runtime_target_id: UUID | None


class EffectiveTemperatureBoundsResolver:
    """Resolve an active target for a loop, or its immutable legacy fallback."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the resolver to the evaluation transaction.

        Args:
            session: The transaction shared by target selection and command
                creation.
        """
        self._targets = RuntimeTargetRepository(session)

    async def resolve(self, loop: ControlLoop) -> EffectiveTemperatureBounds:
        """Resolve the loop's transaction-time temperature source exactly once.

        Args:
            loop: The loop currently evaluating an accepted current sample.

        Returns:
            Valid target bounds and provenance when an active target exists;
            otherwise the loop's legacy thresholds with null provenance.

        Raises:
            RuntimeError: If an active target violates the internal snapshot
                invariants. It is deliberately not treated as absent, because
                falling back would execute a different source silently.
        """
        target = await self._targets.get_active_for_evaluation(loop.id)
        if target is None:
            return EffectiveTemperatureBounds(
                lower=loop.lower_threshold,
                upper=loop.upper_threshold,
                source=EffectiveBoundsSource.CONTROL_LOOP,
                runtime_target_id=None,
            )
        return self._from_runtime_target(target)

    @staticmethod
    def _from_runtime_target(target: RuntimeTarget) -> EffectiveTemperatureBounds:
        """Validate and package one active snapshot without consulting its recipe."""
        lower: Decimal = target.lower_value
        upper: Decimal = target.upper_value
        if (
            target.metric_type is not RuntimeTargetMetric.AIR_TEMPERATURE
            or target.unit != RUNTIME_TARGET_UNIT
            or not lower.is_finite()
            or not upper.is_finite()
            or lower >= upper
        ):
            raise RuntimeError("active runtime target violates temperature invariants")
        return EffectiveTemperatureBounds(
            lower=lower,
            upper=upper,
            source=EffectiveBoundsSource.RUNTIME_TARGET,
            runtime_target_id=target.id,
        )
