"""Request and response schemas of the cultivation module.

The creation body forbids unknown fields and accepts only what a caller can
actually decide. Status, current stage, start and end are all derived or
assigned by the server: a cycle that could be created ``active``, or started at
an instant a client chose, would make every guarantee about its stage instance
and its runtime target a guarantee about what the client happened to send.

The read representation embeds the zone, the current stage and the active
runtime target, because a client asking about a running cycle is asking what it
is being grown against right now. It stops there: the recipe graph behind the
stage has its own endpoint, and duplicating it here would give one recipe two
representations that could disagree.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from ai_greenhouse.agronomy.models import RecipeStage
from ai_greenhouse.core.types import name_type, slug_type
from ai_greenhouse.cultivation.models import (
    MAX_GROW_CYCLE_CODE_LENGTH,
    MAX_GROW_CYCLE_NAME_LENGTH,
    GrowCycle,
    GrowCycleStatus,
    RuntimeTarget,
    RuntimeTargetMetric,
)

GrowCycleCodeStr = slug_type(MAX_GROW_CYCLE_CODE_LENGTH)
"""Slug identifying a grow cycle, for example ``basil-demo-cycle``."""

GrowCycleNameStr = name_type(MAX_GROW_CYCLE_NAME_LENGTH)

TargetValue = Annotated[
    Decimal,
    Field(allow_inf_nan=False),
    PlainSerializer(float, return_type=float, when_used="json"),
]
"""One end of a materialized band, in the target's own unit.

Held as ``Decimal`` so the value read back is the one the requirement carried,
and serialised as a JSON number rather than as the string Pydantic would
otherwise produce. This mirrors the recipe requirement it was copied from and
the control loop's thresholds, which describe the same quantity.
"""


class GrowCycleCreate(BaseModel):
    """Body accepted by ``POST /api/v1/grow-cycles``.

    ``status``, ``current_stage_id``, ``started_at`` and ``ended_at`` are not
    accepted, and ``extra="forbid"`` is what turns sending one into a refusal
    rather than into a silently dropped field. A newly created cycle is always
    ``planned``, and its stage is the one its recipe version carries.

    ``planned_start_at`` is the exception: it is what an operator *intends*, it
    changes no lifecycle rule, and nothing schedules an activation from it.
    """

    model_config = ConfigDict(extra="forbid")

    code: GrowCycleCodeStr
    name: GrowCycleNameStr
    facility_id: UUID
    climate_zone_id: UUID
    recipe_version_id: UUID
    planned_start_at: datetime | None = None


class RuntimeTargetRead(BaseModel):
    """One materialized band, as every runtime-target read returns it.

    Both source links are returned beside the values. A consumer that wants to
    know why a band says 22-26 follows ``target_requirement_id`` to the
    requirement it was copied from; the values themselves never change even if
    that requirement's surroundings are archived later.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    control_loop_id: UUID
    grow_cycle_id: UUID
    target_requirement_id: UUID
    metric_type: RuntimeTargetMetric
    lower_value: TargetValue
    upper_value: TargetValue
    unit: str
    effective_from: datetime
    effective_to: datetime | None
    created_at: datetime


class RuntimeTargetListRead(BaseModel):
    """A count-free collection of runtime targets.

    No total, like command and telemetry history and unlike the paged
    collections. The list is a bounded newest-first window over a table that only
    grows, and a ``COUNT(*)`` over it would grow with the history while
    answering a question nobody asked.
    """

    model_config = ConfigDict(frozen=True)

    items: list[RuntimeTargetRead]


class GrowCycleStageRead(BaseModel):
    """The stage a cycle is currently running, as a compact summary.

    The stage's requirements are deliberately absent. What the cycle is actually
    being driven against is ``active_runtime_target``; what the recipe asks for
    in full is one ``GET /api/v1/recipe-versions/{id}`` away.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    recipe_version_id: UUID
    code: str
    name: str
    sequence_number: int


class GrowCycleRead(BaseModel):
    """Representation of a grow cycle returned by every cycle endpoint.

    ``active_runtime_target`` is ``null`` whenever the cycle has none — before
    activation and after it ends. A terminal cycle never reports its closed
    target here: it is history, and history is read from
    ``GET /api/v1/runtime-targets``.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    code: str
    name: str
    facility_id: UUID
    climate_zone_id: UUID
    recipe_version_id: UUID
    current_stage_id: UUID
    status: GrowCycleStatus
    planned_start_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    current_stage: GrowCycleStageRead
    active_runtime_target: RuntimeTargetRead | None

    @classmethod
    def from_parts(
        cls,
        cycle: GrowCycle,
        control_zone_id: UUID,
        stage: RecipeStage,
        active_runtime_target: RuntimeTarget | None,
    ) -> Self:
        """Build the cycle representation from the rows that make it up.

        The parts are passed in rather than reached through relationships so
        that reading a page of cycles cannot turn into one query per cycle.

        Args:
            cycle: The stored cycle.
            control_zone_id: The zone of its single climate assignment.
            stage: The recipe stage ``current_stage_id`` names.
            active_runtime_target: Its open target, or ``None`` when it has
                none.

        Returns:
            The complete current representation of the cycle.
        """
        return cls(
            id=cycle.id,
            code=cycle.code,
            name=cycle.name,
            facility_id=cycle.facility_id,
            climate_zone_id=control_zone_id,
            recipe_version_id=cycle.recipe_version_id,
            current_stage_id=cycle.current_stage_id,
            status=cycle.status,
            planned_start_at=cycle.planned_start_at,
            started_at=cycle.started_at,
            ended_at=cycle.ended_at,
            created_at=cycle.created_at,
            current_stage=GrowCycleStageRead.model_validate(stage),
            active_runtime_target=(
                RuntimeTargetRead.model_validate(active_runtime_target)
                if active_runtime_target is not None
                else None
            ),
        )
