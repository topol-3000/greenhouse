"""A clean cloud starts, serves and stays empty.

Starting the application against a migrated database used to be the moment the
Basil Growbox appeared. It no longer is: the cloud owns the domain APIs and none
of the data reachable through them. What is asserted here is the whole of that
promise — the lifespan runs, the public reads answer, and every domain table is
still empty afterwards.

Alembic's own version table is infrastructure metadata, not data, and is
deliberately not counted.
"""

from typing import Final

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.factories import count_rows

DOMAIN_TABLES: Final[tuple[str, ...]] = (
    "sites",
    "facilities",
    "control_zones",
    "points",
    "point_current_states",
    "zone_point_assignments",
    "control_loops",
    "commands",
    "telemetry_samples",
    "gateways",
    "gateway_points",
    "edge_telemetry_messages",
)
"""Every table a client's data lands in. A new one belongs in this list."""


async def test_startup_creates_no_domain_resource(
    app: FastAPI,
    connection: AsyncConnection,
) -> None:
    """The lifespan opens the database, and writes nothing to it."""
    async with app.router.lifespan_context(app):
        pass

    counts = {table: await count_rows(connection, table) for table in DOMAIN_TABLES}

    assert counts == dict.fromkeys(DOMAIN_TABLES, 0)


async def test_the_public_reads_answer_on_an_empty_cloud(
    http_client: httpx.AsyncClient,
    connection: AsyncConnection,
) -> None:
    """Health, the dashboard page and the domain collections all work with no data.

    A deployment that needed a seed before it could answer would fail here: an
    empty collection is a successful answer, not a missing precondition.
    """
    health = await http_client.get("/health")
    page = await http_client.get("/")
    sites = await http_client.get("/api/v1/sites")
    gateways = await http_client.get("/api/v1/gateways/by-code/absent-gateway")

    assert health.status_code == 200, health.text
    assert health.json()["database"] == "ok"
    assert page.status_code == 200
    assert sites.status_code == 200
    assert sites.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    # The management plane is available and simply holds nothing yet.
    assert gateways.status_code == 404
    assert await count_rows(connection, "sites") == 0


async def test_topology_and_gateway_management_stay_available(
    http_client: httpx.AsyncClient,
) -> None:
    """Provisioning a clean cloud through the public APIs is what replaced the seed.

    One site, one gateway and the point authorization that binds them: the
    minimum an external client needs before it can submit telemetry, created
    entirely over HTTP.
    """
    site = await http_client.post("/api/v1/sites", json={"name": "Home", "code": "home"})
    assert site.status_code == 201, site.text
    site_id = site.json()["id"]

    point = await http_client.post(
        "/api/v1/points",
        json={
            "site_id": site_id,
            "code": "air_temperature",
            "name": "Air Temperature",
            "point_kind": "measurement",
            "metric_type": "air_temperature",
            "data_type": "float",
            "unit": "°C",
        },
    )
    assert point.status_code == 201, point.text

    gateway = await http_client.post(
        "/api/v1/gateways",
        json={"site_id": site_id, "code": "external-gateway"},
    )
    assert gateway.status_code == 201, gateway.text
    gateway_id = gateway.json()["gateway"]["id"]

    authorized = await http_client.post(
        f"/api/v1/gateways/{gateway_id}/points",
        json={"point_ids": [point.json()["id"]]},
    )
    configuration = await http_client.get(f"/api/v1/gateways/{gateway_id}/configuration")
    authorized_points = await http_client.get(f"/api/v1/gateways/{gateway_id}/points")

    assert authorized.status_code == 200, authorized.text
    assert configuration.status_code == 200, configuration.text
    assert configuration.json()["code"] == "external-gateway"
    assert authorized_points.json()["point_ids"] == [point.json()["id"]]
