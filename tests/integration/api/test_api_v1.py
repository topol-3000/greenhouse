import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from ai_greenhouse.api.dependencies import (
    DatabaseHealthProbe,
    get_database_health_probe,
)
from ai_greenhouse.api.router import API_V1_PREFIX, api_v1_router
from ai_greenhouse.app import create_app
from ai_greenhouse.core.config import Settings

FAKE_DATABASE_URL = "postgresql+asyncpg://test_user:top-secret@test-db:5432/test"


@pytest.fixture
def settings(build_settings: Callable[..., Settings]) -> Settings:
    return build_settings(FAKE_DATABASE_URL, app_env="test")


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def override_health_probe(app: FastAPI) -> None:
    async def healthy_probe() -> None:
        return None

    async def provide_probe() -> DatabaseHealthProbe:
        return healthy_probe

    app.dependency_overrides[get_database_health_probe] = provide_probe


def test_versioned_prefix() -> None:
    assert API_V1_PREFIX == "/api/v1"
    assert api_v1_router.prefix == API_V1_PREFIX


def test_domain_routes_are_mounted_under_the_versioned_prefix(settings: Settings) -> None:
    app = create_app(settings)

    # ``app.routes`` holds lazy ``_IncludedRouter`` entries without a ``path``,
    # so the generated schema is what actually reflects the mounted contract.
    paths = app.openapi()["paths"]

    assert set(paths[f"{API_V1_PREFIX}/sites"]) == {"get", "post"}
    assert set(paths[f"{API_V1_PREFIX}/sites/{{site_id}}"]) == {"get", "patch"}
    assert set(paths[f"{API_V1_PREFIX}/control-loops"]) == {"get", "post"}
    assert set(paths[f"{API_V1_PREFIX}/commands"]) == {"get"}


def test_the_owner_dashboard_reads_stay_registered_and_unchanged(settings: Settings) -> None:
    """The endpoints the standalone owner dashboard reads survived the frontend removal.

    The dashboard moved to its own repository and talks to this one over the
    public API, so the operations it depends on are now a cross-repository
    contract rather than an internal detail of a page shipped alongside them.
    Paths, methods and the response fields it reads are asserted together: the
    frontend was removed, and none of this moved with it.

    ``point_kind`` is named explicitly because it is how a client tells a
    measurement from a control and a status point, which is the whole basis of
    what a monitoring view may display.
    """
    document = create_app(settings).openapi()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert set(paths[f"{API_V1_PREFIX}/facilities"]) == {"get", "post"}
    assert set(paths[f"{API_V1_PREFIX}/facilities/{{facility_id}}"]) == {"get", "patch"}
    assert set(paths[f"{API_V1_PREFIX}/facilities/{{facility_id}}/configuration"]) == {"get"}
    assert set(paths[f"{API_V1_PREFIX}/points"]) == {"get", "post"}
    assert set(paths[f"{API_V1_PREFIX}/points/{{point_id}}/state"]) == {"get"}
    assert set(paths[f"{API_V1_PREFIX}/points/{{point_id}}/telemetry"]) == {"get"}

    assert {"point_kind", "metric_type", "data_type", "unit", "code", "facility_id"} <= set(
        schemas["PointRead"]["properties"]
    )
    assert {"site", "facility", "control_zones", "points"} == set(
        schemas["FacilityConfigurationRead"]["properties"]
    )
    assert {"observed_at", "received_at", "value", "quality", "unit", "point_id"} <= set(
        schemas["TelemetrySampleRead"]["properties"]
    )


def test_no_simulation_lifecycle_operation_is_exposed(settings: Settings) -> None:
    """Executable simulation is Simulation Lab's; the cloud publishes none of it.

    Asserted against the generated document rather than the route table, because
    the contract a client reads is the document. A router mounted again, or a
    schema pulled back in by a stray annotation, fails here.
    """
    document = create_app(settings).openapi()
    schemas = document.get("components", {}).get("schemas", {})
    rendered = json.dumps(document)

    assert [path for path in document["paths"] if "simulation" in path] == []
    assert [name for name in schemas if "Simulation" in name] == []
    assert "simulation-runs" not in rendered
    assert "SimulationRun" not in rendered
    assert "simulation_run_id" not in rendered


def test_no_agronomy_or_grow_cycle_operation_is_exposed(settings: Settings) -> None:
    """Milestone 5's public surface is gone from the document a client reads.

    The generated schema rather than the route table, for the same reason as
    above: a router mounted again, or a response model pulled back in by a stray
    annotation, fails here. ``runtime_target_id`` is checked over the whole
    rendered document because it was a *field* of a surviving resource and not a
    path of its own.
    """
    document = create_app(settings).openapi()
    schemas = document.get("components", {}).get("schemas", {})
    rendered = json.dumps(document)
    removed_paths = (
        "crops",
        "growing-recipes",
        "recipe-versions",
        "grow-cycles",
        "runtime-targets",
    )
    removed_schemas = ("Crop", "Recipe", "GrowCycle", "GrowStage", "RuntimeTarget", "Requirement")

    for fragment in removed_paths:
        assert [path for path in document["paths"] if fragment in path] == []
    for fragment in removed_schemas:
        assert [name for name in schemas if fragment in name] == []
    assert "runtime_target_id" not in rendered
    assert "active_runtime_target" not in rendered
    # The command representation is the one that carried the provenance field.
    command = schemas["CommandRead"]["properties"]
    assert "runtime_target_id" not in command
    assert {"control_loop_id", "trigger_sample_id", "idempotency_key"} <= set(command)


async def test_a_rolled_back_agronomy_path_answers_the_established_not_found(
    settings: Settings,
) -> None:
    """A client still calling M5 gets the same 404 an unknown path has always got.

    Not a 405, not a 500 and not a redirect: a removed router leaves an ordinary
    unrouted path, which is what an external client's retry loop already knows
    how to read.
    """
    app = create_app(settings)

    async with client(app) as http_client:
        responses = {
            path: await http_client.get(f"{API_V1_PREFIX}{path}")
            for path in ("/crops", "/growing-recipes", "/grow-cycles", "/runtime-targets")
        }
        unknown = await http_client.get(f"{API_V1_PREFIX}/never-existed")

    assert {path: response.status_code for path, response in responses.items()} == dict.fromkeys(
        responses,
        404,
    )
    for response in responses.values():
        assert response.json() == unknown.json()


async def test_startup_registers_no_simulation_service_or_task(settings: Settings) -> None:
    """The application owns an engine and a session factory, and no runtime.

    The lifespan is entered and left for real: a background ticker or a recovery
    pass reintroduced later would have to be registered somewhere on the app,
    and this is the assertion that says there is nowhere for it to go.
    """
    app = create_app(settings)
    before = set(asyncio.all_tasks())

    async with app.router.lifespan_context(app):
        during = {task for task in asyncio.all_tasks() if task not in before}
        state = vars(app.state).get("_state", {})

    assert not hasattr(app.state, "simulation_runtime")
    assert set(state) == {"settings", "database_engine", "session_factory"}
    assert during == set()


def test_no_domain_route_escapes_the_versioned_prefix(settings: Settings) -> None:
    app = create_app(settings)

    unversioned = [
        path
        for path in app.openapi()["paths"]
        if not path.startswith(API_V1_PREFIX) and path != "/health"
    ]

    assert unversioned == [], "only /health lives outside /api/v1"


async def test_versioned_router_is_mounted(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_v1_router, "routes", list(api_v1_router.routes))
    probe_router = APIRouter()

    @probe_router.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"mounted": True}

    api_v1_router.include_router(probe_router)
    app = create_app(settings)

    async with client(app) as http_client:
        response = await http_client.get(f"{API_V1_PREFIX}/probe")

    assert response.status_code == 200
    assert response.json() == {"mounted": True}


async def test_health_stays_at_the_root_and_is_unchanged(settings: Settings) -> None:
    app = create_app(settings)
    override_health_probe(app)

    async with client(app) as http_client:
        root_response = await http_client.get("/health")
        versioned_response = await http_client.get(f"{API_V1_PREFIX}/health")

    assert root_response.status_code == 200
    assert root_response.json() == {
        "status": "ok",
        "service": "ai-greenhouse-api",
        "database": "ok",
    }
    assert versioned_response.status_code == 404, "/health must not be duplicated under /api/v1"
