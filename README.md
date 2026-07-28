# AI Greenhouse

[![CI](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml/badge.svg)](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml)

AI Greenhouse is a modular-monolith backend for incrementally building greenhouse
automation scenarios. Milestone 0 provided the runnable technical foundation:
FastAPI, PostgreSQL, migrations, configuration, structured logging, and a
database-aware health endpoint. Milestone 1 adds the topology, starting with the
`Site` entity.

The runtime is Python 3.14 with PostgreSQL 18. Authentication, telemetry,
simulation and frontend are not included yet.

## Prerequisites

- Docker with Docker Compose
- Optional for running outside containers: Python 3.14 and `uv`

The repository includes safe local defaults. Copying `.env.example` to `.env` is
optional and is only needed when overriding those defaults.

## Start the application

```bash
docker compose up --build
```

Startup follows this sequence:

```text
Wait for PostgreSQL readiness
        ↓
Run alembic upgrade head
        ↓
Start Uvicorn
```

Compose also waits for the PostgreSQL health check, but the API performs its own
bounded connection probe before running migrations.

Verify the service:

```bash
curl http://localhost:8000/health
```

Healthy response:

```json
{
  "status": "ok",
  "service": "ai-greenhouse-api",
  "database": "ok"
}
```

If PostgreSQL cannot execute the health query, the endpoint returns HTTP 503 with:

```json
{
  "status": "unavailable",
  "service": "ai-greenhouse-api",
  "database": "unavailable"
}
```

The response never includes connection strings, credentials, exception details,
or stack traces. Technical failure context is written to secret-redacted JSON logs.

## Sites API

A site is a physical location and the root of the topology. All domain
endpoints live under `/api/v1`.

```http
POST   /api/v1/sites
GET    /api/v1/sites?status=&limit=&offset=
GET    /api/v1/sites/{site_id}
PATCH  /api/v1/sites/{site_id}
```

Create a site:

```bash
curl -X POST http://localhost:8000/api/v1/sites \
  -H 'content-type: application/json' \
  -d '{"name": "Home", "code": "home", "timezone": "Europe/Kiev"}'
```

```json
{
  "id": "571fce69-8221-4b5c-8284-728854f2a451",
  "name": "Home",
  "code": "home",
  "timezone": "Europe/Kiev",
  "status": "active",
  "created_at": "2026-07-27T21:47:59.246859Z",
  "updated_at": "2026-07-27T21:47:59.246863Z"
}
```

Rules worth knowing before calling it:

- `code` is a slug matching `^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?$`, unique
  across all sites, and fixed once the site exists;
- `timezone` must be a valid IANA name and defaults to `UTC`;
- `name` is stripped and must be 1–200 characters afterwards;
- `PATCH` accepts `name`, `timezone` and `status` only;
- there is no `DELETE`. A site is retired with `PATCH {"status": "archived"}`
  and stays readable by id.

Collections come back in a paginated envelope ordered by `created_at ASC,
id ASC`, with `limit` defaulting to 50 and capped at 200:

```json
{"items": [], "total": 0, "limit": 50, "offset": 0}
```

Every failure uses one envelope, and never carries SQL, driver messages or
stack traces:

```json
{
  "error": {
    "code": "site_code_conflict",
    "message": "Site code already exists",
    "details": {"code": "home"}
  }
}
```

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body | 422 | `validation_error` |
| Site does not exist | 404 | `site_not_found` |
| `code` already taken | 409 | `site_code_conflict` |
| `PATCH` names `code` | 409 | `immutable_field` |

## Facilities API

A facility is a growing or infrastructure object inside a site: a growbox, a
greenhouse, a hydroponic rack, a seedling room or a water preparation node.

```http
POST   /api/v1/facilities
GET    /api/v1/facilities?site_id=&facility_type=&status=&limit=&offset=
GET    /api/v1/facilities/{facility_id}
PATCH  /api/v1/facilities/{facility_id}
```

Create a facility:

```bash
curl -X POST http://localhost:8000/api/v1/facilities \
  -H 'content-type: application/json' \
  -d '{"site_id": "571fce69-8221-4b5c-8284-728854f2a451",
       "name": "Basil Growbox", "code": "basil-growbox",
       "facility_type": "growbox"}'
```

```json
{
  "id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
  "site_id": "571fce69-8221-4b5c-8284-728854f2a451",
  "name": "Basil Growbox",
  "code": "basil-growbox",
  "facility_type": "growbox",
  "status": "active",
  "created_at": "2026-07-28T08:12:04.118420Z",
  "updated_at": "2026-07-28T08:12:04.118423Z"
}
```

Rules worth knowing before calling it:

- `facility_type` is one of `growbox`, `greenhouse`, `rack`, `seedling_room`
  and `utility`;
- `code` follows the same slug rule as a site, but is unique **within its
  site** only. Two sites may each hold a `basil-growbox`;
- a facility cannot be created inside an archived site;
- `PATCH` accepts `name`, `facility_type` and `status` only. A facility never
  moves between sites: that is a separate administrative operation, because the
  zones and points below it have to move with it;
- there is no `DELETE`, and a site that still has facilities cannot be deleted
  at the database level either (`ON DELETE RESTRICT`).

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `facility_type` | 422 | `validation_error` |
| `site_id` references no site | 422 | `site_not_found` |
| Facility does not exist | 404 | `facility_not_found` |
| `code` already taken on that site | 409 | `facility_code_conflict` |
| Site is archived | 409 | `parent_archived` |
| `PATCH` names `code` | 409 | `immutable_field` |
| `PATCH` names `site_id` | 409 | `facility_site_immutable` |

## Control zones API

A control zone is the part of a facility that is measured or controlled as one
unit: a climate zone, an irrigation zone, a lighting zone. It is a boundary of
*control*, not of physical space, so zones may overlap freely — including two
zones of the same type in one facility.

```http
POST   /api/v1/control-zones
GET    /api/v1/control-zones?facility_id=&zone_type=&status=&limit=&offset=
GET    /api/v1/control-zones/{zone_id}
PATCH  /api/v1/control-zones/{zone_id}
```

Create a control zone:

```bash
curl -X POST http://localhost:8000/api/v1/control-zones \
  -H 'content-type: application/json' \
  -d '{"facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
       "name": "Main Climate", "code": "main-climate",
       "zone_type": "climate"}'
```

```json
{
  "id": "4c8f2a01-77b3-4e0d-8a51-3b6e0d9f1c22",
  "facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
  "name": "Main Climate",
  "code": "main-climate",
  "zone_type": "climate",
  "status": "active",
  "created_at": "2026-07-28T09:31:17.402118Z",
  "updated_at": "2026-07-28T09:31:17.402121Z"
}
```

Rules worth knowing before calling it:

- `zone_type` is one of `climate`, `irrigation`, `lighting`, `measurement`,
  `nutrient_solution` and `safety`;
- there is **no** `site_id`, in the request or in the response. A zone reaches
  its site through its facility, which is what guarantees that a zone never
  crosses a facility boundary;
- `code` follows the same slug rule as a site, but is unique **within its
  facility** only. Two facilities may each hold a `main-climate`;
- overlapping zones are allowed on purpose. Two `climate` zones in one facility
  are accepted; the conflict between them is resolved later by priority and
  policy, which arrive with the control loops of Milestone 3;
- a zone cannot be created inside an archived facility;
- `PATCH` accepts `name`, `zone_type` and `status` only. A zone never moves
  between facilities, because the points assigned to it are scoped by that
  facility too;
- there is no `DELETE`, and a facility that still has zones cannot be deleted at
  the database level either (`ON DELETE RESTRICT`).

Assigning points to a zone (`/api/v1/control-zones/{zone_id}/points`) arrives
with Milestone 1.6.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `zone_type` | 422 | `validation_error` |
| `facility_id` references no facility | 422 | `facility_not_found` |
| Control zone does not exist | 404 | `control_zone_not_found` |
| `code` already taken in that facility | 409 | `control_zone_code_conflict` |
| Facility is archived | 409 | `parent_archived` |
| `PATCH` names `code` | 409 | `immutable_field` |
| `PATCH` names `facility_id` | 409 | `zone_facility_immutable` |

## Points API

A point is the stable logical identity of a value: air temperature, fan power,
pump running. It carries the *meaning* of the value and deliberately nothing
about where that value comes from — no device, no channel, no GPIO pin, no
Modbus register. When a sensor is replaced in Milestone 6, its binding changes
and the point, its history and every rule referring to it do not.

```http
POST   /api/v1/points
GET    /api/v1/points?site_id=&facility_id=&point_kind=&metric_type=&status=&limit=&offset=
GET    /api/v1/points/{point_id}
PATCH  /api/v1/points/{point_id}
GET    /api/v1/points/{point_id}/state
```

Create a point:

```bash
curl -X POST http://localhost:8000/api/v1/points \
  -H 'content-type: application/json' \
  -d '{"site_id": "0f9c4b2e-1d3a-4c58-9b77-2e6a1f0c8d34",
       "facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
       "code": "air_temperature", "name": "Air temperature",
       "point_kind": "measurement", "metric_type": "air_temperature",
       "data_type": "float", "unit": "°C",
       "min_value": -20, "max_value": 60}'
```

```json
{
  "id": "b7d5e0a3-2c19-4f6b-8e02-5a1d7c3f9b40",
  "site_id": "0f9c4b2e-1d3a-4c58-9b77-2e6a1f0c8d34",
  "facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
  "code": "air_temperature",
  "name": "Air temperature",
  "point_kind": "measurement",
  "metric_type": "air_temperature",
  "data_type": "float",
  "unit": "°C",
  "min_value": -20.0,
  "max_value": 60.0,
  "status": "active",
  "created_at": "2026-07-28T09:44:02.118374Z",
  "updated_at": "2026-07-28T09:44:02.118376Z"
}
```

Rules worth knowing before calling it:

- `point_kind` is one of `measurement`, `control`, `status` and `derived`. A
  `derived` point is accepted, but nothing computes one until a later
  milestone;
- `data_type` is one of `float`, `integer`, `boolean` and `string`;
- `unit` is **required** for `float` and `integer` points and **refused** for
  `boolean` ones. A bare `22` that might be Celsius or Fahrenheit would make
  every later target and alert threshold ambiguous;
- `min_value` and `max_value` are only accepted for `float` and `integer`
  points, and the lower end may not exceed the upper one;
- `site_id` is required and `facility_id` is not. A point may belong to the
  site as a whole — an outdoor sensor serves every facility on it. When a
  facility is given, it has to be one of that site's;
- `code` follows the same slug rule as a site, and is unique **within its
  site**, not within its facility. Two facilities on one site cannot both hold
  an `air_temperature`; two different sites can;
- a point cannot be created inside an archived site or facility;
- `PATCH` accepts `name`, `unit`, `min_value`, `max_value` and `status` only.
  `code`, `site_id`, `facility_id`, `point_kind`, `metric_type` and `data_type`
  all define what the point *means*: changing one would reinterpret every value
  already recorded against the point without touching any of them;
- `unit`, `min_value` and `max_value` are nullable, so in a `PATCH` an explicit
  `null` clears them while omitting the field leaves the stored value alone.
  The result is still validated against the point's `data_type`, so clearing
  the unit of a float point is refused;
- there is no `DELETE`, and a site or facility that still has points cannot be
  deleted at the database level either (`ON DELETE RESTRICT`).

Every point owns exactly one state projection, created with it in the same
transaction. Throughout Milestone 1 it is empty — nothing writes a point's
value yet, and no endpoint offers to:

```bash
curl http://localhost:8000/api/v1/points/b7d5e0a3-2c19-4f6b-8e02-5a1d7c3f9b40/state
```

```json
{
  "point_id": "b7d5e0a3-2c19-4f6b-8e02-5a1d7c3f9b40",
  "value": null,
  "observed_at": null,
  "received_at": null,
  "quality": "no_data",
  "revision": 0,
  "updated_at": "2026-07-28T09:44:02.118376Z"
}
```

`observed_at` and `received_at` stay apart on purpose: a buffered edge gateway
makes the two differ by hours. `revision` exists so that Milestone 2 can add
concurrent-update semantics without a migration, and stays at `0` until then.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `point_kind` | 422 | `validation_error` |
| Inverted range in a creation body | 422 | `validation_error` |
| `site_id` references no site | 422 | `site_not_found` |
| `facility_id` references no facility | 422 | `facility_not_found` |
| `facility_id` belongs to another site | 422 | `facility_not_in_site` |
| Unit missing on a `float` or `integer` point | 422 | `unit_required` |
| Unit given for a `boolean` point | 422 | `unit_not_allowed` |
| Range given for a non-numeric point | 422 | `range_not_allowed` |
| `PATCH` would invert the stored range | 422 | `invalid_value_range` |
| Point does not exist | 404 | `point_not_found` |
| `code` already taken on that site | 409 | `point_code_conflict` |
| Site or facility is archived | 409 | `parent_archived` |
| `PATCH` names a field that defines the point | 409 | `immutable_field` |

## Configuration

| Variable | Default | Required |
| --- | --- | --- |
| `APP_NAME` | `ai-greenhouse-api` | No |
| `APP_ENV` | `local` | No |
| `APP_HOST` | `0.0.0.0` | No |
| `APP_PORT` | `8000` | No |
| `LOG_LEVEL` | `INFO` | No |
| `DATABASE_URL` | Compose supplies a safe local URL | Yes outside Compose |
| `POSTGRES_DB` | `ai_greenhouse` | No |
| `POSTGRES_USER` | `ai_greenhouse` | No |
| `POSTGRES_PASSWORD` | `ai_greenhouse_dev` | No |

If PostgreSQL credentials are overridden, set `DATABASE_URL` to the matching
SQLAlchemy async URL as well. Environment-specific `.env` files are ignored by Git.

## Development commands

```bash
# Stop the application
docker compose down

# Apply migrations manually
docker compose run --rm api alembic upgrade head

# Create a migration
docker compose run --rm api alembic revision --autogenerate -m "description"

# Run tests against the real PostgreSQL service
docker compose run --rm api pytest

# Run linting
docker compose run --rm api ruff check .

# Check formatting
docker compose run --rm api ruff format --check .

# Confirm runtime versions
docker compose run --rm api python --version
docker compose exec postgres postgres --version
```

For local checks after `uv sync`:

```bash
uv run ruff check .
uv run ruff format --check .
```

The integration test is skipped outside Compose when no `DATABASE_URL` is
configured. It always uses PostgreSQL when it runs; SQLite is not a supported
substitute.

To remove the local database volume and verify a completely clean initialization:

```bash
# Warning: deletes the local Compose database volume.
docker compose down -v
docker compose up --build
```

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to
`main` and on every pull request. A run in progress for the same ref is
cancelled when a newer commit arrives. Both jobs are required to pass: a Ruff
violation, a formatting difference, a failing test, or a Dockerfile that no
longer builds all fail the workflow.

| Job | What it runs |
| --- | --- |
| `lint` | `ruff check` and `ruff format --check` |
| `test` | `docker compose build api`, then `docker compose run --rm api pytest` |

The two jobs deliberately reach the code by different routes, and both are the
commands used locally rather than CI-only equivalents:

- `lint` installs from `uv.lock` with `uv sync --frozen` on the runner, so the
  Ruff version matching the lockfile is the one that judges the code;
- `test` goes through Compose, so CI builds the same image and talks to the
  same PostgreSQL 18 service as `docker compose up --build`. The container
  entrypoint waits for the database and applies `alembic upgrade head` before
  pytest starts, which also proves the migrations apply to an empty database.
  Compose supplies `DATABASE_URL`, so the integration tests execute against
  real PostgreSQL instead of skipping.

Compose defaults cover every variable the workflow needs, so CI requires no
repository secrets.

## Project structure

```text
src/ai_greenhouse/
├── api/                 # HTTP routes and dependencies
├── core/                # Settings, logging, shared value types and exceptions
├── topology/            # Site, Facility and ControlZone: the physical structure
├── points/              # Point and PointCurrentState: the logical values
└── infrastructure/
    └── database/        # Async engine, metadata, readiness, and health probe
```

Each domain module keeps the same split: `routes/` handles HTTP only,
`service.py` holds the invariants, `repository.py` holds the SQLAlchemy
queries, and `exceptions.py` holds domain failures that know nothing about
HTTP. No SQL reaches the route layer and no FastAPI import reaches the service
layer.

Alembic is the only supported schema-change mechanism; `metadata.create_all()`
is not used, in the tests either. A new module's `models.py` must be imported
in [`migrations/env.py`](migrations/env.py), otherwise autogenerate and
`alembic check` compare against metadata that does not know the table.

Integration tests run against a real PostgreSQL instance, migrate through
Alembic, and isolate each test by rolling back a surrounding transaction.
