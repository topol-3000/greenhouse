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

TEMPERATURE_RESPONSE_RATE: float = 1.0 / 3600.0
HUMIDITY_RESPONSE_RATE: float = 1.0 / 1800.0


class SimulationParameters(BaseModel):
    """Typed and immutable parameter snapshot of ``simple-climate-v1``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_temperature: FiniteFloat
    initial_humidity: Humidity
    ambient_temperature: FiniteFloat
    ambient_humidity: Humidity
    temperature_response_rate: PositiveResponseRate
    humidity_response_rate: PositiveResponseRate


class SimulationRunCreate(BaseModel):
    """Body accepted by ``POST /api/v1/simulation-runs``."""

    model_config = ConfigDict(extra="forbid")

    control_zone_id: UUID
    speed_multiplier: SpeedMultiplier
    initial_temperature: FiniteFloat
    initial_humidity: Humidity
    ambient_temperature: FiniteFloat
    ambient_humidity: Humidity

    def parameter_snapshot(self) -> SimulationParameters:
        """Build the complete, version-owned snapshot stored with the run."""
        return SimulationParameters(
            initial_temperature=self.initial_temperature,
            initial_humidity=self.initial_humidity,
            ambient_temperature=self.ambient_temperature,
            ambient_humidity=self.ambient_humidity,
            temperature_response_rate=TEMPERATURE_RESPONSE_RATE,
            humidity_response_rate=HUMIDITY_RESPONSE_RATE,
        )


class SimulationRunRead(BaseModel):
    """Representation returned by every simulation-run endpoint."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    control_zone_id: UUID
    status: SimulationStatus
    model_version: str
    speed_multiplier: int
    parameters: SimulationParameters
    virtual_time: datetime | None
    step_index: int
    started_at: datetime | None
    stopped_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
