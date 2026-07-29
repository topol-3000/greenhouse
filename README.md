# AI Greenhouse

[![CI](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml/badge.svg)](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml)

AI Greenhouse is a modular-monolith backend for incrementally building greenhouse
automation scenarios. It runs on FastAPI with PostgreSQL, Alembic migrations,
typed configuration, structured logging, and a database-aware health endpoint.

The implemented domain is the complete digital growbox topology — sites,
facilities, control zones, logical points, current-state projections,
zone-point assignments, and a single-request facility configuration — plus
append-only telemetry, current-state updates, telemetry history, deterministic
climate simulation, persisted runs, and a single-process runtime. The runtime is
Python 3.14 with PostgreSQL 18. Authentication, devices, control automation, and
frontend are not included.

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

## Seed the demo growbox

With PostgreSQL running, create the complete basil growbox with one command:

```bash
docker compose run --rm api python -m ai_greenhouse.seed demo
```

The command creates or finds this exact configuration:

```text
Site:         Home           code: home,          timezone: UTC
Facility:     Basil Growbox  code: basil-growbox, type: growbox
ControlZone:  Main Climate   code: main-climate,  type: climate

Points (all facility-scoped):
  air_temperature   measurement  float    unit: °C   range: -20..60
  air_humidity      measurement  float    unit: %    range: 0..100
  fan_power         control      boolean  no unit
  fan_running       status       boolean  no unit

Assignments on Main Climate:
  air_temperature -> primary_measurement
  air_humidity    -> secondary_measurement
  fan_power       -> control_output
  fan_running     -> status_feedback
```

The seed is idempotent. Running the command again finds the records by `code`
within their parent scope, creates no duplicates, and exits successfully. All
writes use the same domain services as the HTTP API. The command emits
structured JSON logs containing every created or found identifier; it does not
run automatically when the API container starts.

## Topology demo walkthrough

The following Bash session starts the stack, seeds it, discovers the generated
identifiers, and reads the complete topology API scenario:

```bash
docker compose up --build -d
docker compose run --rm api python -m ai_greenhouse.seed demo

curl -fsS http://localhost:8000/health

SITE_ID=$(
  curl -fsS 'http://localhost:8000/api/v1/sites' |
    python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "home"))'
)
curl -fsS "http://localhost:8000/api/v1/sites/${SITE_ID}"

FACILITY_ID=$(
  curl -fsS "http://localhost:8000/api/v1/facilities?site_id=${SITE_ID}" |
    python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "basil-growbox"))'
)
curl -fsS "http://localhost:8000/api/v1/facilities?site_id=${SITE_ID}"
curl -fsS "http://localhost:8000/api/v1/control-zones?facility_id=${FACILITY_ID}"
curl -fsS "http://localhost:8000/api/v1/points?facility_id=${FACILITY_ID}"

for POINT_ID in $(
  curl -fsS "http://localhost:8000/api/v1/points?facility_id=${FACILITY_ID}" |
    python -c 'import json,sys; print(*(x["id"] for x in json.load(sys.stdin)["items"]))'
); do
  curl -fsS "http://localhost:8000/api/v1/points/${POINT_ID}/state"
done

curl -fsS "http://localhost:8000/api/v1/facilities/${FACILITY_ID}/configuration"
```

Each point-state response, and every short state in the final configuration,
has `"quality": "no_data"` and `"value": null`. The topology defines the logical
identities and their empty state projections; it does not produce values. Values
arrive through telemetry, which the next walkthrough drives with the simulator.

## Simulation demo walkthrough

Start from the same seed; it supplies the topology and stable logical point IDs,
but creates no simulation run or telemetry. This copy-pasteable Bash session
discovers those IDs, creates and starts a run, reads changing temperature and
humidity, and stops the run:

```bash
docker compose up --build -d
docker compose run --rm api python -m ai_greenhouse.seed demo

FACILITY_ID=$(
  curl -fsS 'http://localhost:8000/api/v1/facilities' |
    docker compose exec -T api python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "basil-growbox"))'
)
ZONE_ID=$(
  curl -fsS "http://localhost:8000/api/v1/control-zones?facility_id=${FACILITY_ID}" |
    docker compose exec -T api python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "main-climate"))'
)
TEMPERATURE_ID=$(
  curl -fsS "http://localhost:8000/api/v1/points?facility_id=${FACILITY_ID}" |
    docker compose exec -T api python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "air_temperature"))'
)
HUMIDITY_ID=$(
  curl -fsS "http://localhost:8000/api/v1/points?facility_id=${FACILITY_ID}" |
    docker compose exec -T api python -c 'import json,sys; print(next(x["id"] for x in json.load(sys.stdin)["items"] if x["code"] == "air_humidity"))'
)

RUN=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/simulation-runs' \
    -H 'content-type: application/json' \
    -d "{\"control_zone_id\":\"${ZONE_ID}\",\"speed_multiplier\":3600,\"initial_temperature\":22.0,\"initial_humidity\":65.0,\"ambient_temperature\":30.0,\"ambient_humidity\":50.0}"
)
printf '%s\n' "${RUN}"
RUN_ID=$(
  printf '%s' "${RUN}" |
    docker compose exec -T api python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)

curl -fsS -X POST "http://localhost:8000/api/v1/simulation-runs/${RUN_ID}/start"
sleep 4

curl -fsS "http://localhost:8000/api/v1/simulation-runs/${RUN_ID}"
curl -fsS "http://localhost:8000/api/v1/points/${TEMPERATURE_ID}/state"
curl -fsS "http://localhost:8000/api/v1/points/${HUMIDITY_ID}/state"
curl -fsS "http://localhost:8000/api/v1/points/${TEMPERATURE_ID}/telemetry"
curl -fsS "http://localhost:8000/api/v1/points/${HUMIDITY_ID}/telemetry"

curl -fsS -X POST "http://localhost:8000/api/v1/simulation-runs/${RUN_ID}/stop"
SAMPLES_AT_STOP=$(
  curl -fsS "http://localhost:8000/api/v1/points/${TEMPERATURE_ID}/telemetry" |
    docker compose exec -T api python -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))'
)
sleep 2
SAMPLES_AFTER_STOP=$(
  curl -fsS "http://localhost:8000/api/v1/points/${TEMPERATURE_ID}/telemetry" |
    docker compose exec -T api python -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))'
)
test "${SAMPLES_AT_STOP}" = "${SAMPLES_AFTER_STOP}"
curl -fsS "http://localhost:8000/api/v1/simulation-runs/${RUN_ID}"
```

Creation returns HTTP 201 with `"status": "created"`. Start returns HTTP 202
with `"status": "running"`, an immediate initial sample for each simulated
point, and `"step_index": 1`. At `speed_multiplier: 3600`, every later
real-second tick advances virtual time by one hour, so both state values move
toward the ambient values and each telemetry history accumulates samples.

The history is newest first. Its `observed_at` is the model's virtual instant,
while `received_at` is the real intake instant; after four ticks they differ by
almost four hours. Both point states have `"quality": "simulated"`. Stop returns
HTTP 200 with `"status": "stopped"`, and the final equality check proves that no
new temperature sample appeared after the stop response.

## API conventions and errors

`GET /health` intentionally stays at the unversioned root. Every domain
endpoint lives under `/api/v1`.

Collection endpoints use the same envelope and accept `limit` and `offset`:

```json
{"items": [], "total": 0, "limit": 50, "offset": 0}
```

`limit` defaults to 50 and must be between 1 and 200; `offset` must not be
negative. A window outside those bounds is refused with HTTP 422
`validation_error` rather than quietly clamped — a client paging with
`limit=500` and stepping `offset` by 500 would otherwise have skipped three
fifths of the collection without being told.

Every domain failure uses one error envelope:

```json
{
  "error": {
    "code": "facility_not_found",
    "message": "Facility not found",
    "details": {"facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88"}
  }
}
```

The status codes have consistent meanings:

| HTTP | Meaning |
| --- | --- |
| 404 | An entity named by the request does not exist. Where its identifier was written — path, query or body — makes no difference. |
| 409 | Existing state or a relationship between entities conflicts with the operation: a duplicate code, an archived parent, an immutable field, a facility on the wrong site, an invalid assignment. |
| 422 | The request shape or a field value is invalid: an unknown enum member, a malformed slug, a pagination window out of bounds, a unit that the point's data type cannot carry. |

Every refusal to change a field fixed at creation uses the one code
`immutable_field` and names the fields in `details.fields`:

```json
{
  "error": {
    "code": "immutable_field",
    "message": "A facility's code and site cannot be changed after creation",
    "details": {"fields": ["site_id"], "facility_id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88"}
  }
}
```

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
id ASC`, with `limit` defaulting to 50 and accepted between 1 and 200:

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
GET    /api/v1/facilities/{facility_id}/configuration?include_archived=
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
| `site_id` references no site | 404 | `site_not_found` |
| Facility does not exist | 404 | `facility_not_found` |
| `code` already taken on that site | 409 | `facility_code_conflict` |
| Site is archived | 409 | `parent_archived` |
| `PATCH` names `code` or `site_id` | 409 | `immutable_field` |

### Facility configuration

The whole growbox in one request: what it is, where it stands, how it is divided
into zones and which points those zones read and drive.

```bash
curl http://localhost:8000/api/v1/facilities/9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88/configuration
```

```json
{
  "facility": {
    "id": "9d1b0f13-6a2c-4d21-9f7e-1c5b0e2a4d88",
    "name": "Basil Growbox",
    "code": "basil-growbox",
    "facility_type": "growbox",
    "status": "active"
  },
  "site": {
    "id": "571fce69-8221-4b5c-8284-728854f2a451",
    "name": "Home",
    "code": "home",
    "timezone": "UTC"
  },
  "control_zones": [
    {
      "id": "4c8f2a01-77b3-4e0d-8a51-3b6e0d9f1c22",
      "name": "Main Climate",
      "code": "main-climate",
      "zone_type": "climate",
      "status": "active",
      "points": [
        {
          "point_id": "0f9c3d55-2b71-4a19-9d0e-6a2f3c8b7e10",
          "code": "air_temperature",
          "role": "primary_measurement"
        }
      ]
    }
  ],
  "points": [
    {
      "id": "0f9c3d55-2b71-4a19-9d0e-6a2f3c8b7e10",
      "code": "air_temperature",
      "name": "Air temperature",
      "point_kind": "measurement",
      "metric_type": "air_temperature",
      "data_type": "float",
      "unit": "°C",
      "status": "active",
      "state": {"value": null, "quality": "no_data", "observed_at": null}
    }
  ]
}
```

- a point's own fields appear once, in `points`. A zone lists only the links, so
  a point taking part in three zones is still described once and cannot be
  described three inconsistent ways;
- archived zones and points are left out. `?include_archived=true` keeps them,
  and `status` on each entry says which is which;
- the document is assembled from a fixed number of queries whatever the size of
  the facility, and a regression test measures that;
- `GET /api/v1/points/{point_id}/state` remains the detailed view of one value;
  the configuration carries the short form of it.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Facility does not exist | 404 | `facility_not_found` |

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
POST   /api/v1/control-zones/{zone_id}/points
GET    /api/v1/control-zones/{zone_id}/points?limit=&offset=
DELETE /api/v1/control-zones/{zone_id}/points/{assignment_id}
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
  are accepted; the conflict between them is resolved by priority and policy,
  which belong to control loops and are not implemented;
- a zone cannot be created inside an archived facility;
- `PATCH` accepts `name`, `zone_type` and `status` only. A zone never moves
  between facilities, because the points assigned to it are scoped by that
  facility too;
- there is no `DELETE`, and a facility that still has zones cannot be deleted at
  the database level either (`ON DELETE RESTRICT`).

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `zone_type` | 422 | `validation_error` |
| `facility_id` references no facility | 404 | `facility_not_found` |
| Control zone does not exist | 404 | `control_zone_not_found` |
| `code` already taken in that facility | 409 | `control_zone_code_conflict` |
| Facility is archived | 409 | `parent_archived` |
| `PATCH` names `code` or `facility_id` | 409 | `immutable_field` |

### Zone point assignments

An assignment says that a point takes part in a zone and what part it plays
there. It is what turns three independent lists into one growbox description,
and what will let a control loop tell its process variable from its actuator
output.

```bash
curl -X POST http://localhost:8000/api/v1/control-zones/4c8f2a01-77b3-4e0d-8a51-3b6e0d9f1c22/points \
  -H 'content-type: application/json' \
  -d '{"point_id": "0f9c3d55-2b71-4a19-9d0e-6a2f3c8b7e10",
       "role": "primary_measurement"}'
```

```json
{
  "id": "b71c4e90-5d2a-4f18-9c33-0a7e5b1d6f24",
  "control_zone_id": "4c8f2a01-77b3-4e0d-8a51-3b6e0d9f1c22",
  "point_id": "0f9c3d55-2b71-4a19-9d0e-6a2f3c8b7e10",
  "role": "primary_measurement",
  "created_at": "2026-07-28T09:52:41.118376Z",
  "point_code": "air_temperature",
  "point_name": "Air temperature",
  "point_kind": "measurement",
  "data_type": "float",
  "unit": "°C"
}
```

Rules worth knowing before calling it:

- `role` is one of `primary_measurement`, `secondary_measurement`,
  `control_output`, `status_feedback`, `safety_interlock` and
  `derived_indicator`;
- a role only suits some kinds of point: `control_output` needs a `control`
  point, `status_feedback` needs a `status` one, and both measurement roles need
  a `measurement` or `derived` one. `safety_interlock` and `derived_indicator`
  accept any kind — narrowing them is a domain decision that has not been made
  yet;
- a zone and a point of **different sites** cannot be linked, and a point that
  belongs to another facility of the same site cannot be either. A point with no
  facility belongs to the site as a whole — an outdoor temperature — and any
  zone on that site may read it;
- a zone has at most one `primary_measurement`: it is controlled against one
  process variable;
- the same point may hold two *different* roles in one zone; only the exact
  repetition of `(zone, point, role)` is refused;
- neither an archived zone nor an archived point can take part in a new link;
- `DELETE` is a real delete, and the only one in the API. An assignment is a
  link, not a record with a history, so there is nothing to archive — and the
  point itself is untouched by it.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `role` | 422 | `validation_error` |
| `point_id` references no point | 404 | `point_not_found` |
| Control zone does not exist | 404 | `control_zone_not_found` |
| Assignment does not exist in that zone | 404 | `assignment_not_found` |
| Zone or point is archived | 409 | `parent_archived` |
| Point belongs to another site | 409 | `cross_site_assignment` |
| Point belongs to another facility | 409 | `cross_facility_assignment` |
| Role does not suit the point's kind | 409 | `role_kind_mismatch` |
| Zone already has a primary measurement | 409 | `primary_measurement_exists` |
| That exact link already exists | 409 | `assignment_exists` |

## Points API

A point is the stable logical identity of a value: air temperature, fan power,
pump running. It carries the *meaning* of the value and deliberately nothing
about where that value comes from — no device, no channel, no GPIO pin, no
Modbus register. When a sensor is replaced, only its binding changes; the point,
its history and every rule referring to it do not.

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
  `derived` point is accepted, but nothing computes one yet;
- `data_type` is one of `float`, `integer`, `boolean` and `string`;
- `unit` is optional and **refused** for `boolean` points. A numeric point may
  leave it `null`: pH, an EC ratio and a light-utilisation index are
  dimensionless, and demanding a unit would only push a fake one into the data;
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
  The result is still validated against the point's `data_type`, so a range
  left on a point cannot outlive the type that carries it;
- there is no `DELETE`, and a site or facility that still has points cannot be
  deleted at the database level either (`ON DELETE RESTRICT`).

Every point owns exactly one state projection, created with it in the same
transaction. It starts empty, and no HTTP endpoint writes it: values reach it
only through the telemetry write boundary, which the simulator drives in
process.

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
makes the two differ by hours. `revision` counts the replacements the projection
has actually taken: it starts at `0` and increments only when a newer sample
replaces the current value, so a re-delivered sample leaves it alone.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unknown `point_kind` | 422 | `validation_error` |
| Inverted range in a creation body | 422 | `validation_error` |
| Unit given for a `boolean` point | 422 | `unit_not_allowed` |
| Range given for a non-numeric point | 422 | `range_not_allowed` |
| `PATCH` would invert the stored range | 422 | `invalid_value_range` |
| Point does not exist | 404 | `point_not_found` |
| `site_id` references no site | 404 | `site_not_found` |
| `facility_id` references no facility | 404 | `facility_not_found` |
| `code` already taken on that site | 409 | `point_code_conflict` |
| `facility_id` belongs to another site | 409 | `facility_not_in_site` |
| Site or facility is archived | 409 | `parent_archived` |
| `PATCH` names a field that defines the point | 409 | `immutable_field` |

## Control loops API

A control loop is one immutable automation rule of one climate zone: which
point it watches, which point it drives, which point reports back, and the
hysteresis band it decides on. It names points and never devices, so replacing
the hardware behind `fan_power` changes nothing here.

```http
POST   /api/v1/control-loops
GET    /api/v1/control-loops?control_zone_id=&limit=&offset=
GET    /api/v1/control-loops/{control_loop_id}
```

Configure the loop of the demo growbox:

```bash
curl -X POST http://localhost:8000/api/v1/control-loops \
  -H 'content-type: application/json' \
  -d '{"control_zone_id": "3a7f1c22-9e04-4b6d-8f31-7c05a2e9d641",
       "measurement_point_id": "b7d5e0a3-2c19-4f6b-8e02-5a1d7c3f9b40",
       "control_point_id": "c81a4f57-0d3e-4a92-b6c8-9f24e7031a5d",
       "status_point_id": "d92b5068-1e4f-4ba3-c7d9-0a35f8142b6e",
       "lower_threshold": 24.0, "upper_threshold": 26.0}'
```

```json
{
  "id": "e03c6179-2f50-4cb4-d8ea-1b46094253af",
  "control_zone_id": "3a7f1c22-9e04-4b6d-8f31-7c05a2e9d641",
  "measurement_point_id": "b7d5e0a3-2c19-4f6b-8e02-5a1d7c3f9b40",
  "control_point_id": "c81a4f57-0d3e-4a92-b6c8-9f24e7031a5d",
  "status_point_id": "d92b5068-1e4f-4ba3-c7d9-0a35f8142b6e",
  "policy_type": "hysteresis-v1",
  "lower_threshold": 24.0,
  "upper_threshold": 26.0,
  "created_at": "2026-07-30T08:12:44.201883Z"
}
```

Rules worth knowing before calling it:

- `policy_type` is fixed at `hysteresis-v1` and is not accepted in the request.
  There is one policy, and a field a client can only set to a single value is a
  field that will be set to something else the moment a second policy exists;
- a zone gets **one** loop. A second one is refused rather than accepted
  alongside the first, because a command already applied records the
  configuration it was decided under;
- the zone has to be an active `climate` zone;
- each of the three points has to be active and assigned to *that* zone in the
  role the loop needs it in: `measurement_point_id` as its `primary_measurement`,
  `control_point_id` as a `control_output`, `status_point_id` as a
  `status_feedback`;
- the measurement point is numeric, carries `metric_type` `air_temperature` and
  is measured in `°C` — nothing in the flow converts units, so a point reporting
  Fahrenheit would be compared against Celsius thresholds;
- the control and status points are `boolean` and carry `metric_type`
  `fan_power` and `fan_running`;
- `lower_threshold` must be strictly below `upper_threshold`. A band of zero
  width has no hysteresis left in it;
- there is no `PATCH` and no `DELETE`. The configuration is what a decision was
  taken under, so it is written once and read afterwards.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Invalid request body or unordered band | 422 | `validation_error` |
| Loop does not exist | 404 | `control_loop_not_found` |
| `control_zone_id` references no zone | 404 | `control_zone_not_found` |
| A point identifier references no point | 404 | `point_not_found` |
| Zone is archived or is not a climate zone | 409 | `invalid_control_loop_zone` |
| A point is archived, outside the zone, or of the wrong kind, type, metric or unit | 409 | `invalid_control_loop_point` |
| Zone already has a loop | 409 | `control_loop_exists` |

## Boundaries

The seed deliberately creates a topology-only growbox — no simulation,
automation, telemetry, or device fixtures — and the following are not part of
the system:

- no `Area` physical-space hierarchy;
- no assignment history — removing a zone-point assignment removes only that
  current link;
- no authentication or authorization;
- no devices, channels, bindings, or physical addresses;
- no public telemetry ingestion endpoint, and no device or edge infrastructure;
- no commands and no UI;
- no policy versions, schedules, PID or rules engine: a control loop carries
  its own `hysteresis-v1` thresholds and nothing else;
- no distributed workers: the simulation runtime is single-process and lives
  inside the API application.

Point state is filled by append-only telemetry, which the in-process simulator
drives.

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
| `POSTGRES_PORT` | `5432` | No |

If PostgreSQL credentials are overridden, set `DATABASE_URL` to the matching
SQLAlchemy async URL as well. Environment-specific `.env` files are ignored by Git.

Compose publishes PostgreSQL on `POSTGRES_PORT`, so a local `psql` or a GUI
client can reach the development database directly:

```bash
psql postgresql://ai_greenhouse:ai_greenhouse_dev@localhost:5432/ai_greenhouse
```

Set `POSTGRES_PORT` to something else when 5432 is already taken on the host.
The `api` service still reaches the database over the Compose network on 5432,
so `DATABASE_URL` does not change with it.

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
├── topology/            # Site, Facility, ControlZone and zone point assignments
├── points/              # Point and PointCurrentState: the logical values
├── telemetry/           # Append-only samples and read-only history
├── simulation/          # Deterministic climate runs and in-process runtime
├── control/             # Immutable hysteresis control loops
├── seed/                # Explicit, idempotent demo growbox seed
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
`tests/integration/test_migrations.py` goes further and checks every migration
on a scratch database of its own: `upgrade head` on an empty database, a clean
`alembic check`, and a `downgrade base` that really leaves nothing behind.

[`tests/README.md`](tests/README.md) states which layer asserts what. Read it
before adding tests — it is what keeps one rule from being checked four times.
