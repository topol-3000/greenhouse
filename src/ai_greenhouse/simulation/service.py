"""Business rules for simulation configuration, lifecycle and atomic steps."""

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_greenhouse.api.pagination import PageParams
from ai_greenhouse.control.automation import TelemetryIngestionService
from ai_greenhouse.infrastructure.database.base import StatusEnum
from ai_greenhouse.points.exceptions import PointStateNotFoundError
from ai_greenhouse.points.models import DataQuality, Point, PointCurrentState, PointDataType
from ai_greenhouse.points.repository import PointCurrentStateRepository
from ai_greenhouse.simulation.climate import ClimateState, step_climate, step_climate_v2
from ai_greenhouse.simulation.exceptions import (
    InvalidSimulationProgressError,
    InvalidSimulationTransitionError,
    InvalidSimulationZoneError,
    SimulationAlreadyRunningError,
    SimulationRunNotFoundError,
    SimulationZoneNotFoundError,
)
from ai_greenhouse.simulation.models import (
    CURRENT_MODEL_VERSION,
    MODEL_VERSION_V1,
    MODEL_VERSION_V2,
    SimulationRun,
    SimulationStatus,
)
from ai_greenhouse.simulation.repository import SimulationRunRepository
from ai_greenhouse.simulation.schemas import (
    ClimateV2Parameters,
    SimulationParameters,
    SimulationRunCreate,
)
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord
from ai_greenhouse.topology.models import ZonePointRole, ZoneType

NUMERIC_DATA_TYPES: frozenset[PointDataType] = frozenset(
    {PointDataType.FLOAT, PointDataType.INTEGER}
)
MEASUREMENT_ROLES: frozenset[ZonePointRole] = frozenset(
    {ZonePointRole.PRIMARY_MEASUREMENT, ZonePointRole.SECONDARY_MEASUREMENT}
)
FAN_POWER_METRIC_TYPE: str = "fan_power"
"""Metric of the logical point ``simple-climate-v2`` reads as its fan state.

The simulator resolves the point the same way the rest of the system addresses
it — by metric, data type and zone role — and never by identifier, so replacing
the simulated fan with a real one changes nothing here.
"""
TERMINAL_STATUSES: frozenset[SimulationStatus] = frozenset(
    {SimulationStatus.STOPPED, SimulationStatus.FAILED}
)


class SimulationRunService:
    """Validate, persist and advance one-shot simulation runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session
        self._repository = SimulationRunRepository(session)
        self._states = PointCurrentStateRepository(session)
        self._ingestion = TelemetryIngestionService(session)

    async def create_run(self, payload: SimulationRunCreate) -> SimulationRun:
        """Create a validated run in ``created`` state."""
        zone = await self._repository.get_zone(payload.control_zone_id)
        if zone is None:
            raise SimulationZoneNotFoundError(payload.control_zone_id)
        if zone.status is not StatusEnum.ACTIVE:
            raise InvalidSimulationZoneError(payload.control_zone_id, "zone_not_active")
        if zone.zone_type is not ZoneType.CLIMATE:
            raise InvalidSimulationZoneError(payload.control_zone_id, "zone_not_climate")

        await self._get_climate_points(payload.control_zone_id)
        if await self._repository.has_running_for_zone(payload.control_zone_id):
            raise SimulationAlreadyRunningError(payload.control_zone_id)

        run = SimulationRun(
            control_zone_id=payload.control_zone_id,
            status=SimulationStatus.CREATED,
            model_version=CURRENT_MODEL_VERSION,
            speed_multiplier=payload.speed_multiplier,
            parameters=payload.parameter_snapshot().model_dump(mode="json"),
            step_index=0,
        )
        self._repository.add(run)
        await self._repository.flush()
        return run

    async def get_run(self, run_id: UUID) -> SimulationRun:
        """Return one run or report it missing."""
        run = await self._repository.get_by_id(run_id)
        if run is None:
            raise SimulationRunNotFoundError(run_id)
        return run

    async def list_runs(
        self,
        params: PageParams,
        *,
        control_zone_id: UUID | None,
        status: SimulationStatus | None,
    ) -> tuple[list[SimulationRun], int]:
        """Return a filtered, newest-first page."""
        return await self._repository.list_page(
            params,
            control_zone_id=control_zone_id,
            status=status,
        )

    async def mark_running(self, run_id: UUID, *, started_at: datetime) -> SimulationRun:
        """Move a created run to running for the later runtime story."""
        run = await self._get_for_transition(run_id)
        if run.status is not SimulationStatus.CREATED:
            raise InvalidSimulationTransitionError(
                run.id,
                run.status.value,
                SimulationStatus.RUNNING.value,
            )
        if await self._repository.has_running_for_zone(run.control_zone_id):
            raise SimulationAlreadyRunningError(run.control_zone_id)
        run.status = SimulationStatus.RUNNING
        run.started_at = started_at
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise SimulationAlreadyRunningError(run.control_zone_id) from error
        return run

    async def advance_step(
        self,
        run_id: UUID,
        *,
        virtual_time: datetime,
    ) -> SimulationRun:
        """Advance progress only for a running run and strictly forward in time."""
        run = await self._get_for_transition(run_id)
        if run.status is not SimulationStatus.RUNNING:
            raise InvalidSimulationTransitionError(run.id, run.status.value, "advance")
        if run.virtual_time is not None and virtual_time <= run.virtual_time:
            raise InvalidSimulationProgressError(run.id)
        run.virtual_time = virtual_time
        run.step_index += 1
        await self._repository.flush()
        return run

    async def mark_stopped(self, run_id: UUID, *, stopped_at: datetime) -> SimulationRun:
        """Make a running run terminal."""
        run = await self._get_for_transition(run_id)
        if run.status is not SimulationStatus.RUNNING:
            raise InvalidSimulationTransitionError(
                run.id,
                run.status.value,
                SimulationStatus.STOPPED.value,
            )
        run.status = SimulationStatus.STOPPED
        run.stopped_at = stopped_at
        await self._repository.flush()
        return run

    async def mark_failed(
        self,
        run_id: UUID,
        *,
        stopped_at: datetime,
        reason: str,
    ) -> SimulationRun:
        """Make a non-terminal run failed with a safe reason."""
        run = await self._get_for_transition(run_id)
        if run.status in TERMINAL_STATUSES:
            raise InvalidSimulationTransitionError(
                run.id,
                run.status.value,
                SimulationStatus.FAILED.value,
            )
        run.status = SimulationStatus.FAILED
        run.stopped_at = stopped_at
        run.failure_reason = reason
        await self._repository.flush()
        return run

    async def start_with_initial_step(
        self,
        run_id: UUID,
        *,
        started_at: datetime,
    ) -> SimulationRun:
        """Start a run and atomically record its two initial values."""
        run = await self._get_for_transition(run_id)
        if run.status is not SimulationStatus.CREATED:
            raise InvalidSimulationTransitionError(
                run.id,
                run.status.value,
                SimulationStatus.RUNNING.value,
            )
        if await self._repository.has_running_for_zone(run.control_zone_id):
            raise SimulationAlreadyRunningError(run.control_zone_id)

        run.status = SimulationStatus.RUNNING
        run.started_at = started_at
        try:
            await self._repository.flush()
        except IntegrityError as error:
            raise SimulationAlreadyRunningError(run.control_zone_id) from error

        temperature, humidity = await self._get_climate_points(run.control_zone_id)
        parameters = self._validated_parameters(run)
        await self._record_values(
            run,
            temperature=temperature,
            humidity=humidity,
            state=ClimateState(
                temperature=parameters.initial_temperature,
                humidity=parameters.initial_humidity,
            ),
            observed_at=started_at,
            received_at=started_at,
        )
        return run

    async def advance_runtime_step(
        self,
        run_id: UUID,
        *,
        received_at: datetime,
    ) -> SimulationRun:
        """Atomically calculate and record one tick of a running model."""
        run = await self._get_for_transition(run_id)
        if run.status is not SimulationStatus.RUNNING:
            raise InvalidSimulationTransitionError(run.id, run.status.value, "advance")
        if run.virtual_time is None:
            raise InvalidSimulationProgressError(run.id)

        temperature, humidity = await self._get_climate_points(run.control_zone_id)
        temperature_state = await self._get_point_state(temperature.id)
        humidity_state = await self._get_point_state(humidity.id)
        current = ClimateState(
            temperature=self._numeric_state_value(temperature_state),
            humidity=self._numeric_state_value(humidity_state),
        )
        next_state = await self._next_climate_state(run, current)
        observed_at = run.virtual_time + timedelta(seconds=run.speed_multiplier)
        await self._record_values(
            run,
            temperature=temperature,
            humidity=humidity,
            state=next_state,
            observed_at=observed_at,
            received_at=received_at,
        )
        return run

    async def fail_running_runs(
        self,
        *,
        stopped_at: datetime,
        reason: str,
    ) -> int:
        """Fail every persisted running run during process lifecycle recovery."""
        runs = await self._repository.list_running_for_update()
        for run in runs:
            run.status = SimulationStatus.FAILED
            run.stopped_at = stopped_at
            run.failure_reason = reason
        if runs:
            await self._repository.flush()
        return len(runs)

    async def _record_values(
        self,
        run: SimulationRun,
        *,
        temperature: Point,
        humidity: Point,
        state: ClimateState,
        observed_at: datetime,
        received_at: datetime,
    ) -> None:
        """Record both model outputs before advancing persisted progress.

        The samples go through the shared ingestion path rather than straight to
        the telemetry boundary, so a simulated temperature drives automation on
        exactly the terms a real one would. The simulator itself knows nothing
        about that: it offers a measurement and is told nothing back.
        """
        values = (
            (temperature, self._value_for_point(temperature, state.temperature)),
            (humidity, self._value_for_point(humidity, state.humidity)),
        )
        for point, value in values:
            await self._ingestion.ingest(
                TelemetrySampleRecord(
                    id=simulation_sample_id(run.id, run.step_index, point.id),
                    point_id=point.id,
                    simulation_run_id=run.id,
                    value=value,
                    observed_at=observed_at,
                    received_at=received_at,
                    quality=DataQuality.SIMULATED,
                )
            )
        run.virtual_time = observed_at
        run.step_index += 1
        await self._repository.flush()

    def _validated_parameters(self, run: SimulationRun) -> SimulationParameters:
        """Validate one run's snapshot against the model version it persisted.

        This is the whole of the version dispatch: the persisted column decides
        which snapshot the stored parameters have to satisfy, and the validated
        type then decides which formula evaluates them. A run keeps the model it
        was created with, so a v1 row read after M4 still reaches frozen v1.

        Args:
            run: The persisted run being started or advanced.

        Returns:
            The v1 snapshot, or the v2 snapshot that extends it.

        Raises:
            ValidationError: If the stored parameters do not match the version.
            ValueError: If the stored version is not one this build executes.
                Neither is reachable from a request: the version is server-owned
                and the snapshot is written whole.
        """
        if run.model_version == MODEL_VERSION_V1:
            return SimulationParameters.model_validate(run.parameters)
        if run.model_version == MODEL_VERSION_V2:
            return ClimateV2Parameters.model_validate(run.parameters)
        raise ValueError("simulation run carries an unsupported model version")

    async def _next_climate_state(
        self,
        run: SimulationRun,
        current: ClimateState,
    ) -> ClimateState:
        """Evaluate one tick of the model this run persisted.

        Args:
            run: The running run being advanced.
            current: The state read from the two measurement projections.

        Returns:
            The next state of the run's own model version.
        """
        parameters = self._validated_parameters(run)
        delta_virtual_time = float(run.speed_multiplier)
        if isinstance(parameters, ClimateV2Parameters):
            return step_climate_v2(
                current,
                parameters,
                delta_virtual_time,
                fan_is_on=await self._fan_is_on(run.control_zone_id),
            )
        return step_climate(current, parameters, delta_virtual_time)

    async def _fan_is_on(self, control_zone_id: UUID) -> bool:
        """Read the zone's logical fan state as an input of the model step.

        The projection is read, never written and never locked: the simulator
        offers measurements and is told nothing back, and taking a lock here
        would invert the order automation acquires the same two rows in.

        A point that carries no value is off. That is a rule about *evaluating a
        step* and not about the point, which keeps its ``no_data`` state until a
        command actually sets it.

        Args:
            control_zone_id: The zone whose fan the run is cooled by.

        Returns:
            ``True`` only when the projection holds a boolean ``true``.
        """
        fan_power = await self._get_fan_power_point(control_zone_id)
        state = await self._states.get_by_point_id(fan_power.id)
        return state is not None and state.value is True

    async def _get_fan_power_point(self, control_zone_id: UUID) -> Point:
        """Resolve the one active boolean fan point the zone drives.

        Archived points are already excluded by the query, so a retired fan is
        simply not found. Nothing is chosen when the answer is ambiguous: a run
        cooled by a silently picked point would be a run nobody can reproduce.

        Args:
            control_zone_id: The zone whose composition to read.

        Returns:
            The zone's ``control_output`` fan point.

        Raises:
            InvalidSimulationZoneError: If the zone has no such point, more than
                one, or one whose data type cannot carry a fan state. Raised
                during a step, it fails the run with the runtime's bounded
                reason.
        """
        assignments = await self._repository.list_active_zone_points(control_zone_id)
        fans = [
            point
            for assignment, point in assignments
            if point.metric_type == FAN_POWER_METRIC_TYPE
            and point.data_type is PointDataType.BOOLEAN
            and assignment.role is ZonePointRole.CONTROL_OUTPUT
        ]
        if len(fans) != 1:
            raise InvalidSimulationZoneError(
                control_zone_id,
                "required_fan_power_point_missing_or_ambiguous",
            )
        return fans[0]

    async def _get_climate_points(self, control_zone_id: UUID) -> tuple[Point, Point]:
        """Resolve the unique temperature and humidity points of a run."""
        assignments = await self._repository.list_active_zone_points(control_zone_id)
        temperatures = [
            point
            for assignment, point in assignments
            if point.metric_type == "air_temperature"
            and point.data_type in NUMERIC_DATA_TYPES
            and assignment.role is ZonePointRole.PRIMARY_MEASUREMENT
        ]
        humidities = [
            point
            for assignment, point in assignments
            if point.metric_type == "air_humidity"
            and point.data_type in NUMERIC_DATA_TYPES
            and assignment.role in MEASUREMENT_ROLES
        ]
        if len(temperatures) != 1 or len(humidities) != 1:
            raise InvalidSimulationZoneError(
                control_zone_id,
                "required_climate_points_missing_or_ambiguous",
            )
        return temperatures[0], humidities[0]

    async def _get_point_state(self, point_id: UUID) -> PointCurrentState:
        """Lock one state row used as climate-model input."""
        state = await self._states.get_for_update(point_id)
        if state is None:
            raise PointStateNotFoundError(point_id)
        return state

    @staticmethod
    def _numeric_state_value(state: PointCurrentState) -> float:
        """Return a model input or fail a corrupted/non-numeric projection."""
        if isinstance(state.value, bool) or not isinstance(state.value, int | float):
            raise ValueError("simulation point state is not numeric")
        return float(state.value)

    @staticmethod
    def _value_for_point(point: Point, value: float) -> int | float:
        """Keep model output compatible with the point's declared data type."""
        if point.data_type is PointDataType.INTEGER:
            return round(value)
        return value

    async def _get_for_transition(self, run_id: UUID) -> SimulationRun:
        """Load and lock a run before changing lifecycle or progress."""
        run = await self._repository.get_for_update(run_id)
        if run is None:
            raise SimulationRunNotFoundError(run_id)
        return run


def simulation_sample_id(run_id: UUID, step_index: int, point_id: UUID) -> UUID:
    """Derive the stable telemetry identifier of one simulated measurement."""
    return uuid5(run_id, f"{step_index}:{point_id}")
