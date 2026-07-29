"""Application-scoped scheduling for persisted simulation runs."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.infrastructure.database.base import utc_now
from ai_greenhouse.simulation.exceptions import (
    InvalidSimulationTransitionError,
    SimulationAlreadyRunningError,
    SimulationRunNotFoundError,
)
from ai_greenhouse.simulation.models import SimulationRun
from ai_greenhouse.simulation.service import SimulationRunService

FAILURE_REASON: str = "simulation_step_failed"
STARTUP_INTERRUPTION_REASON: str = "application_startup_interruption"
SHUTDOWN_INTERRUPTION_REASON: str = "application_shutdown_interruption"

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class Ticker(Protocol):
    """Injectable source of ticks and deterministic completion acknowledgements."""

    async def wait(self) -> None:
        """Wait until the next tick is due."""

    async def completed(self) -> None:
        """Acknowledge that the released tick has finished."""


class OneSecondTicker:
    """Production ticker capped at one tick per real second."""

    async def wait(self) -> None:
        """Wait one real second."""
        await asyncio.sleep(1)

    async def completed(self) -> None:
        """Complete the ticker contract without extra work."""


SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession]]
Clock = Callable[[], datetime]
TickerFactory = Callable[[], Ticker]


class SimulationRuntime:
    """Own one background task for every run active in this process."""

    def __init__(
        self,
        session_factory_provider: SessionFactoryProvider,
        *,
        clock: Clock = utc_now,
        ticker_factory: TickerFactory = OneSecondTicker,
    ) -> None:
        self._session_factory_provider = session_factory_provider
        self._clock = clock
        self._ticker_factory = ticker_factory
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._registry_lock = asyncio.Lock()

    @property
    def running_task_count(self) -> int:
        """Return the number of tasks currently owned by the registry."""
        return len(self._tasks)

    async def start(self, run_id: UUID) -> SimulationRun:
        """Commit the initial step, then register exactly one ticking task."""
        async with self._registry_lock:
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                raise InvalidSimulationTransitionError(run_id, "running", "running")
            if task is not None:
                self._tasks.pop(run_id, None)

            try:
                run = await self._start_initial_step(run_id)
            except (
                InvalidSimulationTransitionError,
                SimulationAlreadyRunningError,
                SimulationRunNotFoundError,
            ):
                raise
            except Exception as error:
                await self._mark_failed(run_id, error)
                raise

            task = asyncio.create_task(
                self._run_ticks(run_id),
                name=f"simulation-run-{run_id}",
            )
            self._tasks[run_id] = task
            return run

    async def stop(self, run_id: UUID) -> SimulationRun:
        """Cancel and await a task before committing its stopped transition."""
        async with self._registry_lock:
            task = self._tasks.get(run_id)
            if task is None or task.done():
                if task is not None:
                    self._tasks.pop(run_id, None)
                await self._raise_current_transition(run_id, "stop")

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._tasks.pop(run_id, None)

            session_factory = self._session_factory_provider()
            async with session_factory() as session, session.begin():
                return await SimulationRunService(session).mark_stopped(
                    run_id,
                    stopped_at=self._clock(),
                )

    async def recover_interrupted_runs(self) -> int:
        """Fail persisted running rows left behind by an earlier process."""
        return await self._fail_all_running(STARTUP_INTERRUPTION_REASON)

    async def shutdown(self) -> None:
        """Cancel all tasks and leave no run falsely marked as active."""
        async with self._registry_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        await self._fail_all_running(SHUTDOWN_INTERRUPTION_REASON)

    async def _start_initial_step(self, run_id: UUID) -> SimulationRun:
        """Run the immediate initial step in its own transaction."""
        session_factory = self._session_factory_provider()
        async with session_factory() as session, session.begin():
            return await SimulationRunService(session).start_with_initial_step(
                run_id,
                started_at=self._clock(),
            )

    async def _run_ticks(self, run_id: UUID) -> None:
        """Advance one run until it is stopped or a step fails."""
        ticker = self._ticker_factory()
        try:
            while True:
                await ticker.wait()
                try:
                    await self._advance_step(run_id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self._mark_failed(run_id, error)
                    return
                finally:
                    await ticker.completed()
        finally:
            current = asyncio.current_task()
            if self._tasks.get(run_id) is current:
                self._tasks.pop(run_id, None)

    async def _advance_step(self, run_id: UUID) -> None:
        """Advance one tick in a short session and transaction."""
        session_factory = self._session_factory_provider()
        async with session_factory() as session, session.begin():
            await SimulationRunService(session).advance_runtime_step(
                run_id,
                received_at=self._clock(),
            )

    async def _mark_failed(self, run_id: UUID, error: Exception) -> None:
        """Persist a bounded safe reason and log technical failure context."""
        try:
            session_factory = self._session_factory_provider()
            async with session_factory() as session, session.begin():
                await SimulationRunService(session).mark_failed(
                    run_id,
                    stopped_at=self._clock(),
                    reason=FAILURE_REASON,
                )
        except Exception:
            logger.exception(
                "simulation_failure_transition_failed",
                run_id=str(run_id),
                step_exception_type=type(error).__name__,
            )
            return
        logger.exception(
            "simulation_step_failed",
            run_id=str(run_id),
            exception_type=type(error).__name__,
            exc_info=error,
        )

    async def _fail_all_running(self, reason: str) -> int:
        """Fail all persisted running rows in one process-lifecycle transaction."""
        session_factory = self._session_factory_provider()
        async with session_factory() as session, session.begin():
            return await SimulationRunService(session).fail_running_runs(
                stopped_at=self._clock(),
                reason=reason,
            )

    async def _raise_current_transition(self, run_id: UUID, transition: str) -> None:
        """Raise 404 or the lifecycle conflict matching persisted state."""
        session_factory = self._session_factory_provider()
        async with session_factory() as session:
            run = await SimulationRunService(session).get_run(run_id)
            raise InvalidSimulationTransitionError(
                run.id,
                run.status.value,
                transition,
            )
