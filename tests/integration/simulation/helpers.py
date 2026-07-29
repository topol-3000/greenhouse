"""Controlled clock and ticker helpers for simulation integration tests."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_greenhouse.simulation.runtime import SimulationRuntime


class ManualTicker:
    """Release ticks from tests and acknowledge their completed transactions."""

    def __init__(self) -> None:
        self._ticks: asyncio.Queue[None] = asyncio.Queue()
        self._completed: asyncio.Queue[None] = asyncio.Queue()

    async def wait(self) -> None:
        """Wait for a test to release one tick."""
        await self._ticks.get()

    async def completed(self) -> None:
        """Tell the releasing test that the whole step has ended."""
        self._completed.put_nowait(None)

    async def tick(self) -> None:
        """Release exactly one tick and wait for its outcome without sleeping."""
        self.release()
        await self._completed.get()

    def release(self) -> None:
        """Release a tick without waiting for a task to consume it."""
        self._ticks.put_nowait(None)


class ManualClock:
    """Mutable timezone-aware clock used by runtime tests."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        """Return the controlled current time."""
        return self.now

    def advance(self, *, seconds: int) -> None:
        """Move the controlled time forward."""
        self.now += timedelta(seconds=seconds)


def install_runtime(
    app: FastAPI,
    *,
    clock: Callable[[], datetime],
    ticker: ManualTicker,
) -> SimulationRuntime:
    """Install a runtime that follows the test transaction and manual time."""
    runtime = SimulationRuntime(
        lambda: cast(
            async_sessionmaker[AsyncSession],
            app.state.session_factory,
        ),
        clock=clock,
        ticker_factory=lambda: ticker,
    )
    app.state.simulation_runtime = runtime
    return runtime
