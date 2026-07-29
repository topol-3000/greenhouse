"""HTTP and lifecycle coverage for persisted simulation runs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_greenhouse.simulation.exceptions import InvalidSimulationTransitionError
from ai_greenhouse.simulation.runtime import FAILURE_REASON
from ai_greenhouse.simulation.service import SimulationRunService
from ai_greenhouse.telemetry.schemas import TelemetrySampleRecord
from ai_greenhouse.telemetry.service import TelemetryService
from tests.integration.factories import (
    assign,
    count_rows,
    create_growbox,
    create_point,
)
from tests.integration.simulation.helpers import ManualClock, ManualTicker, install_runtime

SIMULATION_RUNS_URL: str = "/api/v1/simulation-runs"
NOW: datetime = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)


async def configured_climate(
    http_client: httpx.AsyncClient,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create a climate zone with the two required active numeric points."""
    growbox = await create_growbox(http_client)
    temperature = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
    )
    humidity = await create_point(
        http_client,
        growbox.site["id"],
        facility_id=growbox.facility["id"],
        code="air-humidity",
        name="Air humidity",
        metric_type="air_humidity",
        unit="%",
    )
    temperature_assignment = await assign(
        http_client,
        growbox.control_zone["id"],
        temperature["id"],
        "primary_measurement",
    )
    humidity_assignment = await assign(
        http_client,
        growbox.control_zone["id"],
        humidity["id"],
        "secondary_measurement",
    )
    assert temperature_assignment.status_code == 201, temperature_assignment.text
    assert humidity_assignment.status_code == 201, humidity_assignment.text
    return growbox.control_zone, temperature, humidity


async def configured_climate_zone(http_client: httpx.AsyncClient) -> dict[str, Any]:
    """Create and return a valid climate zone."""
    zone, _, _ = await configured_climate(http_client)
    return zone


def run_body(control_zone_id: str, **overrides: object) -> dict[str, object]:
    """Build a valid create request."""
    return {
        "control_zone_id": control_zone_id,
        "speed_multiplier": 60,
        "initial_temperature": 22.0,
        "initial_humidity": 65.0,
        "ambient_temperature": 30.0,
        "ambient_humidity": 50.0,
    } | overrides


async def create_run(
    http_client: httpx.AsyncClient,
    control_zone_id: str,
    **overrides: object,
) -> dict[str, Any]:
    """Create one run and require success."""
    response = await http_client.post(
        SIMULATION_RUNS_URL,
        json=run_body(control_zone_id, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_creation_persists_the_fixed_model_snapshot(
    http_client: httpx.AsyncClient,
) -> None:
    zone = await configured_climate_zone(http_client)

    response = await http_client.post(SIMULATION_RUNS_URL, json=run_body(zone["id"]))

    body = response.json()
    assert response.status_code == 201, response.text
    assert body["control_zone_id"] == zone["id"]
    assert body["status"] == "created"
    assert body["model_version"] == "simple-climate-v1"
    assert body["step_index"] == 0
    assert body["virtual_time"] is None
    assert body["started_at"] is None
    assert body["stopped_at"] is None
    assert body["failure_reason"] is None
    assert body["parameters"] == {
        "initial_temperature": 22.0,
        "initial_humidity": 65.0,
        "ambient_temperature": 30.0,
        "ambient_humidity": 50.0,
        "temperature_response_rate": 1.0 / 3600.0,
        "humidity_response_rate": 1.0 / 1800.0,
    }


async def test_get_list_filters_pagination_and_newest_first(
    http_client: httpx.AsyncClient,
) -> None:
    zone = await configured_climate_zone(http_client)
    first = await create_run(http_client, zone["id"], speed_multiplier=1)
    second = await create_run(http_client, zone["id"], speed_multiplier=2)

    read = await http_client.get(f"{SIMULATION_RUNS_URL}/{first['id']}")
    listing = await http_client.get(
        SIMULATION_RUNS_URL,
        params={
            "control_zone_id": zone["id"],
            "status": "created",
            "limit": 1,
            "offset": 0,
        },
    )
    next_page = await http_client.get(
        SIMULATION_RUNS_URL,
        params={"control_zone_id": zone["id"], "limit": 1, "offset": 1},
    )

    assert read.status_code == 200
    assert read.json() == first
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert [item["id"] for item in listing.json()["items"]] == [second["id"]]
    assert [item["id"] for item in next_page.json()["items"]] == [first["id"]]


async def test_missing_zone_and_run_return_not_found(
    http_client: httpx.AsyncClient,
) -> None:
    missing_zone = uuid4()
    missing_run = uuid4()

    create = await http_client.post(
        SIMULATION_RUNS_URL,
        json=run_body(str(missing_zone)),
    )
    read = await http_client.get(f"{SIMULATION_RUNS_URL}/{missing_run}")

    assert create.status_code == 404
    assert create.json()["error"]["code"] == "control_zone_not_found"
    assert read.status_code == 404
    assert read.json()["error"]["code"] == "simulation_run_not_found"


async def test_invalid_zone_configuration_is_refused_without_a_row(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    growbox = await create_growbox(http_client)

    response = await http_client.post(
        SIMULATION_RUNS_URL,
        json=run_body(growbox.control_zone["id"]),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_simulation_zone"
    assert await count_rows(connection, "simulation_runs") == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"speed_multiplier": 0},
        {"speed_multiplier": 3601},
        {"initial_humidity": -0.1},
        {"ambient_humidity": 100.1},
        {"model_version": "simple-climate-v2"},
    ],
)
async def test_invalid_request_boundaries_return_422(
    http_client: httpx.AsyncClient,
    overrides: dict[str, object],
) -> None:
    response = await http_client.post(
        SIMULATION_RUNS_URL,
        json=run_body(str(uuid4()), **overrides),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_second_run_is_refused_while_one_is_running(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    zone = await configured_climate_zone(http_client)
    first = await create_run(http_client, zone["id"])
    service = SimulationRunService(session)
    await service.mark_running(UUID(first["id"]), started_at=NOW)
    await session.commit()

    response = await http_client.post(SIMULATION_RUNS_URL, json=run_body(zone["id"]))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "simulation_already_running"


async def test_terminal_run_cannot_advance(
    http_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    zone = await configured_climate_zone(http_client)
    created = await create_run(http_client, zone["id"])
    run_id = UUID(created["id"])
    service = SimulationRunService(session)
    await service.mark_running(run_id, started_at=NOW)
    await service.advance_step(run_id, virtual_time=NOW + timedelta(minutes=1))
    await service.mark_stopped(run_id, stopped_at=NOW + timedelta(minutes=2))

    with pytest.raises(InvalidSimulationTransitionError):
        await service.advance_step(run_id, virtual_time=NOW + timedelta(minutes=3))


async def test_double_start_returns_conflict_without_a_second_task_or_initial_step(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    zone = await configured_climate_zone(http_client)
    created = await create_run(http_client, zone["id"])
    ticker = ManualTicker()
    runtime = install_runtime(app, clock=ManualClock(NOW), ticker=ticker)

    first = await http_client.post(f"{SIMULATION_RUNS_URL}/{created['id']}/start")
    second = await http_client.post(f"{SIMULATION_RUNS_URL}/{created['id']}/start")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_simulation_transition"
    assert runtime.running_task_count == 1
    assert await count_rows(connection, "telemetry_samples") == 2

    stopped = await http_client.post(f"{SIMULATION_RUNS_URL}/{created['id']}/stop")
    assert stopped.status_code == 200


async def test_mid_step_failure_rolls_back_both_points_and_progress_then_fails_run(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zone, temperature, humidity = await configured_climate(http_client)
    created = await create_run(http_client, zone["id"])
    ticker = ManualTicker()
    clock = ManualClock(NOW)
    runtime = install_runtime(app, clock=clock, ticker=ticker)
    started = await http_client.post(f"{SIMULATION_RUNS_URL}/{created['id']}/start")
    assert started.status_code == 202

    original_record_sample = TelemetryService.record_sample
    call_count = 0

    async def fail_before_second_sample(
        service: TelemetryService,
        record: TelemetrySampleRecord,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("database-password-must-not-be-persisted")
        await original_record_sample(service, record)

    monkeypatch.setattr(TelemetryService, "record_sample", fail_before_second_sample)
    clock.advance(seconds=1)
    await ticker.tick()

    failed = await http_client.get(f"{SIMULATION_RUNS_URL}/{created['id']}")
    temperature_state = await http_client.get(f"/api/v1/points/{temperature['id']}/state")
    humidity_state = await http_client.get(f"/api/v1/points/{humidity['id']}/state")

    assert failed.json()["status"] == "failed"
    assert failed.json()["failure_reason"] == FAILURE_REASON
    assert "password" not in failed.json()["failure_reason"]
    assert failed.json()["step_index"] == 1
    assert failed.json()["virtual_time"] == NOW.isoformat().replace("+00:00", "Z")
    assert await count_rows(connection, "telemetry_samples") == 2
    assert temperature_state.json()["revision"] == 1
    assert humidity_state.json()["revision"] == 1
    assert runtime.running_task_count == 0


async def test_startup_recovery_fails_every_persisted_running_run(
    app: FastAPI,
    http_client: httpx.AsyncClient,
    session: AsyncSession,
    connection: AsyncConnection,
) -> None:
    zone = await configured_climate_zone(http_client)
    created = await create_run(http_client, zone["id"])
    await SimulationRunService(session).mark_running(UUID(created["id"]), started_at=NOW)
    await session.commit()
    install_runtime(app, clock=ManualClock(NOW), ticker=ManualTicker())

    async with app.router.lifespan_context(app):
        statuses = (
            await connection.execute(text("SELECT status, failure_reason FROM simulation_runs"))
        ).all()

    assert statuses == [("failed", "application_startup_interruption")]


async def test_start_and_stop_of_a_missing_run_return_not_found(
    app: FastAPI,
    http_client: httpx.AsyncClient,
) -> None:
    install_runtime(app, clock=ManualClock(NOW), ticker=ManualTicker())
    missing = uuid4()

    start = await http_client.post(f"{SIMULATION_RUNS_URL}/{missing}/start")
    stop = await http_client.post(f"{SIMULATION_RUNS_URL}/{missing}/stop")

    assert start.status_code == 404
    assert start.json()["error"]["code"] == "simulation_run_not_found"
    assert stop.status_code == 404
    assert stop.json()["error"]["code"] == "simulation_run_not_found"
