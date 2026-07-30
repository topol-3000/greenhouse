"""Request, response and parameter-snapshot schemas for simulations."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_greenhouse.simulation.models import SimulationStatus

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Humidity = Annotated[FiniteFloat, Field(ge=0, le=100)]
SpeedMultiplier = Annotated[int, Field(ge=1, le=3600)]
PositiveResponseRate = Annotated[FiniteFloat, Field(gt=0)]
CoolingOffset = Annotated[FiniteFloat, Field(ge=0)]

TEMPERATURE_RESPONSE_RATE: float = 1.0 / 3600.0
HUMIDITY_RESPONSE_RATE: float = 1.0 / 1800.0
FAN_COOLING_OFFSET: float = 8.0
"""How far a running fan pulls the temperature target below ambient, in ``°C``.

Part of the snapshot rather than a constant read at every step: a run is
reproducible only if the numbers it was evaluated under are the numbers stored
with it.
"""


class SimulationParameters(BaseModel):
    """Typed and immutable parameter snapshot of ``simple-climate-v1``.

    Frozen in both senses: the instance cannot be mutated, and the fields are
    the complete snapshot of a model version that no longer changes. A persisted
    v1 run still validates against exactly these six values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_temperature: FiniteFloat
    initial_humidity: Humidity
    ambient_temperature: FiniteFloat
    ambient_humidity: Humidity
    temperature_response_rate: PositiveResponseRate
    humidity_response_rate: PositiveResponseRate


class ClimateV2Parameters(SimulationParameters):
    """Parameter snapshot of ``simple-climate-v2``: v1 plus the fan's effect.

    A subclass rather than a second flat schema, because v2 changes which target
    the temperature moves toward and nothing else. Both parents' rules still
    hold, so ``extra="forbid"`` keeps a v1 snapshot from validating as a v2 one
    and the other way round — which is what lets one persisted column serve both
    versions without a discriminator field.

    Attributes:
        fan_cooling_offset: Degrees the effective target drops by while the
            logical fan is on. This is a demonstration coefficient, not a
            calibrated physical response.
    """

    fan_cooling_offset: CoolingOffset


class SimulationRunCreate(BaseModel):
    """Body accepted by ``POST /api/v1/simulation-runs``."""

    model_config = ConfigDict(extra="forbid")

    control_zone_id: UUID
    speed_multiplier: SpeedMultiplier
    initial_temperature: FiniteFloat
    initial_humidity: Humidity
    ambient_temperature: FiniteFloat
    ambient_humidity: Humidity

    def parameter_snapshot(self) -> ClimateV2Parameters:
        """Build the complete, version-owned snapshot stored with the run.

        Returns:
            The snapshot of the current model version. The request supplies the
            starting and ambient conditions; the version supplies the response
            rates and the cooling offset.
        """
        return ClimateV2Parameters(
            initial_temperature=self.initial_temperature,
            initial_humidity=self.initial_humidity,
            ambient_temperature=self.ambient_temperature,
            ambient_humidity=self.ambient_humidity,
            temperature_response_rate=TEMPERATURE_RESPONSE_RATE,
            humidity_response_rate=HUMIDITY_RESPONSE_RATE,
            fan_cooling_offset=FAN_COOLING_OFFSET,
        )


class SimulationRunRead(BaseModel):
    """Representation returned by every simulation-run endpoint.

    ``parameters`` is the snapshot of whichever version the run persisted. The
    two shapes are mutually exclusive — a v1 snapshot has no cooling offset and a
    v2 one requires it — so the reader never has to guess which it received, and
    ``model_version`` says it outright.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    control_zone_id: UUID
    status: SimulationStatus
    model_version: str
    speed_multiplier: int
    parameters: ClimateV2Parameters | SimulationParameters
    virtual_time: datetime | None
    step_index: int
    started_at: datetime | None
    stopped_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
