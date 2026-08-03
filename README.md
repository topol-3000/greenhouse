# AI Greenhouse

[![CI](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml/badge.svg)](https://github.com/topol-3000/greenhouse/actions/workflows/ci.yml)

AI Greenhouse is the **cloud API/backend** for incrementally building greenhouse
automation scenarios. It is a modular monolith on FastAPI with PostgreSQL,
Alembic migrations, typed configuration, structured logging, and a
database-aware health endpoint. It serves an HTTP API and nothing else — there
is no owner-facing dashboard in this repository and starting it does not expose
one.

The implemented domain is the complete digital growbox topology — sites,
facilities, control zones, logical points, current-state projections,
zone-point assignments, and a single-request facility configuration — plus
append-only telemetry, current-state updates, telemetry history, and the first
automation loop: an accepted temperature is evaluated by a hysteresis policy and
turns a logical fan on or off through an idempotent command. Administrative
clients can provision stable Edge gateway identities and logical-point
authorization through HTTP, then use the Cloud ↔ Edge v1 data plane for
telemetry and command delivery.

The cloud creates no data of its own. A clean deployment starts with empty
domain tables and stays that way until a client provisions a facility through
the public APIs, and every measurement comes from an external producer — real
equipment, or the environment simulation that lives in the separate
[`greenhouse-simulation-lab`](https://github.com/topol-3000/greenhouse-simulation-lab)
repository.

The runtime is Python 3.14 with PostgreSQL 18. Production authentication,
devices, and frontend code of any kind are not included.

## Repositories and how they meet

| Repository | Owns | Reaches this one by |
| --- | --- | --- |
| `greenhouse` (here) | The cloud API, the domain and the database. | — |
| [`greenhouse-dashboard`](https://github.com/topol-3000/greenhouse-dashboard) | The **sole owner-facing UI**: everything an owner sees. | The public HTTP API |
| [`greenhouse-simulation-lab`](https://github.com/topol-3000/greenhouse-simulation-lab) | Executable environment simulation and virtual devices. | The public Cloud ↔ Edge v1 API |

The repositories communicate **only** through the public HTTP API. This backend
holds no frontend code, asset, template or static mount; it does not embed,
redirect to, proxy or host the dashboard, and it carries no CORS configuration
on the dashboard's behalf. Serving the dashboard and this API from one origin —
a development proxy, or whatever a deployment uses — is the dashboard
repository's responsibility.

Real edge producers are separate again: like Simulation Lab, they are ordinary
clients of the Cloud ↔ Edge v1 contract and run nothing inside the cloud.

The reasoning is recorded in
[`docs/decisions/0003-api-only-backend-and-separate-owner-dashboard.md`](docs/decisions/0003-api-only-backend-and-separate-owner-dashboard.md).

## Cloud ↔ Edge contracts

The transport-neutral v1.0 telemetry and command contract is published under
[`contracts/cloud-edge/v1/`](contracts/cloud-edge/v1/README.md). It contains
standalone JSON Schemas, valid and invalid examples, idempotency and lifecycle
semantics, error behavior, compatibility rules, and the implemented HTTP/JSON
mapping:

```http
POST /api/v1/edge/telemetry
GET  /api/v1/edge/gateways/{gateway_id}/commands
PUT  /api/v1/edge/gateways/{gateway_id}/commands/{command_id}/acknowledgement
```

These operations are the Edge data plane. Their closed v1 representations stay
independent of the administrative provisioning API described next.

## Gateway management-plane API

An administrative provisioning client can configure the gateway-specific part
of a clean cloud without importing cloud Python code, executing a seed, or
accessing PostgreSQL:

```http
POST /api/v1/gateways
GET  /api/v1/gateways/by-code/{code}
GET  /api/v1/gateways/{gateway_id}
GET  /api/v1/gateways/{gateway_id}/configuration
GET  /api/v1/gateways/{gateway_id}/points
POST /api/v1/gateways/{gateway_id}/points
```

Provision or resolve a gateway by its globally stable code:

```bash
curl -X POST http://localhost:8000/api/v1/gateways \
  -H 'content-type: application/json' \
  -d '{"code":"north-gateway",
       "site_id":"11111111-1111-1111-1111-111111111111"}'
```

The first equivalent request returns HTTP 201 and `"outcome": "created"`;
retries return HTTP 200 and `"outcome": "existing"`. Both carry the same
gateway resource and normalized functional configuration:

```json
{
  "outcome": "created",
  "gateway": {
    "id": "22222222-2222-2222-2222-222222222222",
    "code": "north-gateway",
    "site_id": "11111111-1111-1111-1111-111111111111",
    "status": "active",
    "created_at": "2026-07-30T12:00:00Z",
    "updated_at": "2026-07-30T12:00:00Z"
  },
  "configuration": {
    "gateway_id": "22222222-2222-2222-2222-222222222222",
    "code": "north-gateway",
    "site_id": "11111111-1111-1111-1111-111111111111"
  }
}
```

`code` is the stable provisioning lookup key. `gateway.id` is the cloud-assigned
operational UUID sent unchanged in telemetry envelopes and used in command poll
and acknowledgement paths. The configuration representation deliberately omits
timestamps and persistence details so clients can compare functional values.

Reusing a code with a different `site_id`, or reusing an archived gateway,
returns HTTP 409 with `error.code = "gateway_configuration_conflict"`. Its
details name the conflicting gateway and code and include a `conflicts` list
whose entries carry `field`, `expected`, and `actual`.

Point authorization is additive: it never removes existing authorization.

```bash
curl -X POST \
  http://localhost:8000/api/v1/gateways/22222222-2222-2222-2222-222222222222/points \
  -H 'content-type: application/json' \
  -d '{"point_ids":[
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444"
      ]}'
```

Duplicate identifiers in one request are normalized to their first occurrence.
The response reports each distinct point as `"authorized"` or
`"already_authorized"`, and an equivalent retry creates no rows. A point must
exist, be active, belong to the gateway's site, and not be authorized to another
gateway. `GET .../points` returns the complete sorted authorization set.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| Malformed body, code, UUID, or empty point list | 422 | `validation_error` |
| Gateway does not exist by UUID or code | 404 | `gateway_not_found` |
| Site does not exist | 404 | `site_not_found` |
| Logical point does not exist | 404 | `point_not_found` |
| Stable code has incompatible functional configuration | 409 | `gateway_configuration_conflict` |
| Gateway is archived | 409 | `gateway_inactive` |
| Site is archived | 409 | `gateway_site_inactive` |
| Point is archived, belongs to another site, or has another gateway owner | 409 | `gateway_point_conflict` |

These are administrative management-plane operations and are isolated under
their own router/tag for a future administrative authorization policy. The
application does not yet implement production authentication, users, RBAC, or
multi-tenancy; deployments must not treat the current unauthenticated boundary
as the final production security model.

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

Nothing else runs first. There is no seed, no bootstrap command and no startup
hook that writes domain data, so a clean database is still empty when the API
starts answering: no site, no facility, no zone, no point, no gateway, no
telemetry and no command. An explicit command — `pytest`, `alembic`, a shell —
runs exactly that. Everything the system holds is created by a client through
the public APIs, described under
[Provision a growbox over HTTP](#provision-a-growbox-over-http).

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

Browse the API at <http://localhost:8000/docs>, or read the generated schema at
<http://localhost:8000/openapi.json>. `GET /` is not a route: this service serves
no page, so it answers HTTP 404 like any other unrouted path.

## The owner dashboard

The owner-facing UI is the separate
[`greenhouse-dashboard`](https://github.com/topol-3000/greenhouse-dashboard)
repository. It is not served from here, and `docker compose up --build` starts an
API and no dashboard.

The dashboard is an ordinary client of the public API described below: it reads
facilities, facility configuration, point metadata and current state, telemetry
history and commands, and adds no endpoint of its own. Nothing in this
repository is shaped for it — no aggregate resource, no dashboard-specific
response and no CORS configuration — and it carries its own build, delivery and
same-origin proxying. Run it from its own checkout, following that repository's
README.

## Where the data comes from

Measurements reach the cloud only over the public Cloud ↔ Edge v1 telemetry
boundary. To watch the fan switch on and off locally without any physical
equipment, run the environment simulation from the separate
[`greenhouse-simulation-lab`](https://github.com/topol-3000/greenhouse-simulation-lab)
repository against this cloud. It owns the Basil Growbox scenario, the virtual
sensors and actuators, the virtual gateway and the scenario lifecycle, and it
provisions its own topology through the public APIs described below. Start the
cloud first, then, from that repository's checkout:

```bash
uv run greenhouse-simulation-lab provision
uv run greenhouse-simulation-lab run
```

Stopping it with Ctrl+C stops the producer only. The cloud keeps running, and
every topology, telemetry sample, state and command it wrote stays exactly where
it is.

## Provision a growbox over HTTP

A clean cloud starts empty, so the first thing any client does is provision.
This is the complete sequence, in the order the API expects it, and it is the
same sequence an external provisioning client such as Simulation Lab performs.
Every request is a public endpoint; nothing here imports cloud Python code or
touches PostgreSQL.

```bash
docker compose up --build -d
curl -fsS http://localhost:8000/health

json() { python3 -c "import json,sys; print($1)"; }

SITE_ID=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/sites' \
    -H 'content-type: application/json' \
    -d '{"name":"Home","code":"home","timezone":"UTC"}' |
    json 'json.load(sys.stdin)["id"]'
)
FACILITY_ID=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/facilities' \
    -H 'content-type: application/json' \
    -d "{\"site_id\":\"${SITE_ID}\",\"name\":\"Basil Growbox\",\"code\":\"basil-growbox\",\"facility_type\":\"growbox\"}" |
    json 'json.load(sys.stdin)["id"]'
)
ZONE_ID=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/control-zones' \
    -H 'content-type: application/json' \
    -d "{\"facility_id\":\"${FACILITY_ID}\",\"name\":\"Main Climate\",\"code\":\"main-climate\",\"zone_type\":\"climate\"}" |
    json 'json.load(sys.stdin)["id"]'
)

create_point() {
  curl -fsS -X POST 'http://localhost:8000/api/v1/points' \
    -H 'content-type: application/json' \
    -d "{\"site_id\":\"${SITE_ID}\",\"facility_id\":\"${FACILITY_ID}\",\"code\":\"$1\",\"name\":\"$2\",\"point_kind\":\"$3\",\"metric_type\":\"$1\",\"data_type\":\"$4\",\"unit\":$5}" |
    json 'json.load(sys.stdin)["id"]'
}
assign() {
  curl -fsS -X POST "http://localhost:8000/api/v1/control-zones/${ZONE_ID}/points" \
    -H 'content-type: application/json' \
    -d "{\"point_id\":\"$1\",\"role\":\"$2\"}" > /dev/null
}

TEMPERATURE_ID=$(create_point air_temperature 'Air Temperature' measurement float '"°C"')
HUMIDITY_ID=$(create_point air_humidity 'Air Humidity' measurement float '"%"')
FAN_POWER_ID=$(create_point fan_power 'Fan Power' control boolean null)
FAN_RUNNING_ID=$(create_point fan_running 'Fan Running' status boolean null)

assign "${TEMPERATURE_ID}" primary_measurement
assign "${HUMIDITY_ID}" secondary_measurement
assign "${FAN_POWER_ID}" control_output
assign "${FAN_RUNNING_ID}" status_feedback

curl -fsS "http://localhost:8000/api/v1/facilities/${FACILITY_ID}/configuration"
```

The configuration response describes the whole growbox in one read: the site,
the facility, the zone with its four assignments in role order, and each point
with its short state. Every one of those states is `"quality": "no_data"` with
`"value": null`. The topology defines the logical identities and their empty
state projections; it does not produce values.

Points, codes and roles are yours to choose — this growbox is an example, not a
fixture the cloud knows about. A monitoring client reads whichever points a
facility defines, and every point it has not measured yet answers with
`"quality": "no_data"`.

Nothing about this is single-use: creating a site, a facility, a zone, a point
or an assignment that already exists is refused with a documented HTTP 409
rather than duplicated, so a provisioning client can resolve-or-create safely.

## Configure the fan automation

The control loop is what turns an accepted temperature into a fan command.
Configure one on the zone provisioned above:

```bash
LOOP_ID=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/control-loops' \
    -H 'content-type: application/json' \
    -d "{\"control_zone_id\":\"${ZONE_ID}\",\"measurement_point_id\":\"${TEMPERATURE_ID}\",\"control_point_id\":\"${FAN_POWER_ID}\",\"status_point_id\":\"${FAN_RUNNING_ID}\",\"lower_threshold\":24.0,\"upper_threshold\":26.0}" |
    json 'json.load(sys.stdin)["id"]'
)
```

A loop is immutable and there is one per zone: a second one, or a different
band, is refused instead of overwriting what is configured.

Automation reacts to whatever became the point's current temperature, whoever
measured it. Register a gateway and authorize it for the four points:

```bash
GATEWAY_ID=$(
  curl -fsS -X POST 'http://localhost:8000/api/v1/gateways' \
    -H 'content-type: application/json' \
    -d "{\"site_id\":\"${SITE_ID}\",\"code\":\"growbox-gateway\"}" |
    json 'json.load(sys.stdin)["gateway"]["id"]'
)
curl -fsS -X POST "http://localhost:8000/api/v1/gateways/${GATEWAY_ID}/points" \
  -H 'content-type: application/json' \
  -d "{\"point_ids\":[\"${TEMPERATURE_ID}\",\"${HUMIDITY_ID}\",\"${FAN_POWER_ID}\",\"${FAN_RUNNING_ID}\"]}"
```

The cycle then has two halves, and the gateway owns one of them: the cloud
decides and hands out a command, and the environment behind the gateway applies
it and reports the result back as ordinary telemetry. Both helpers below are
what a gateway does — Simulation Lab performs exactly this for you.

```bash
submit() {
  curl -fsS -X POST 'http://localhost:8000/api/v1/edge/telemetry' \
    -H 'content-type: application/json' \
    -d "{\"contract_version\":\"1.0\",\"gateway_id\":\"${GATEWAY_ID}\",\"messages\":[{\"message_id\":\"$(python3 -c 'import uuid; print(uuid.uuid4())')\",\"point_id\":\"$1\",\"data_type\":\"$2\",\"value\":$3,\"observed_at\":\"$4\",\"quality\":\"good\",\"source\":{\"kind\":\"$5\",\"id\":\"growbox.$6\"}}]}" > /dev/null
}

apply_pending() {
  PENDING=$(curl -fsS "http://localhost:8000/api/v1/edge/gateways/${GATEWAY_ID}/commands")
  COMMAND_ID=$(printf '%s' "${PENDING}" | json 'json.load(sys.stdin)["commands"][0]["command_id"]')
  DESIRED=$(printf '%s' "${PENDING}" | json 'str(json.load(sys.stdin)["commands"][0]["desired_value"]).lower()')
  curl -fsS -X PUT \
    "http://localhost:8000/api/v1/edge/gateways/${GATEWAY_ID}/commands/${COMMAND_ID}/acknowledgement" \
    -H 'content-type: application/json' \
    -d "{\"contract_version\":\"1.0\",\"gateway_id\":\"${GATEWAY_ID}\",\"command_id\":\"${COMMAND_ID}\",\"outcome\":\"applied\",\"acknowledged_at\":\"$1\"}" > /dev/null
  submit "${FAN_POWER_ID}" boolean "${DESIRED}" "$1" controller fan_power
  submit "${FAN_RUNNING_ID}" boolean "${DESIRED}" "$1" actuator fan_running
}

submit "${TEMPERATURE_ID}" float 27.0 '2026-07-30T09:00:00+00:00' sensor air_temperature
apply_pending '2026-07-30T09:00:30+00:00'

submit "${TEMPERATURE_ID}" float 25.0 '2026-07-30T09:01:00+00:00' sensor air_temperature

submit "${TEMPERATURE_ID}" float 23.0 '2026-07-30T09:02:00+00:00' sensor air_temperature
apply_pending '2026-07-30T09:02:30+00:00'

curl -fsS "http://localhost:8000/api/v1/commands?control_loop_id=${LOOP_ID}"
curl -fsS "http://localhost:8000/api/v1/points/${FAN_RUNNING_ID}/state"
curl -fsS "http://localhost:8000/api/v1/points/${TEMPERATURE_ID}/telemetry"
```

The loop answers:

```text
27.0 °C   above the band, fan is off   ->  command fan_power = true
25.0 °C   inside the band              ->  no command
23.0 °C   below the band, fan is on    ->  command fan_power = false
```

The command list comes back newest first, so the `false` command is first. Both
are `"state": "applied"` and carry the `acknowledged_at` the gateway reported;
each names the temperature sample that caused it, and `fan_running` ends at
`"value": false` with `"revision": 2`. No client ever sent a command: a command
is a consequence of accepted telemetry and there is no endpoint that creates
one.

The second half matters to the decision, not only to the hardware: the loop
switches the fan off because the fan was *reported* running, so a command nobody
applies stays `"pending"` and the next temperature below the band decides
nothing. That half of the cycle is the Cloud ↔ Edge v1 contract, documented
under [`contracts/cloud-edge/v1/`](contracts/cloud-edge/v1/README.md).

Follow one command's chain, or ask what a single measurement caused:

```bash
COMMAND_ID=$(
  curl -fsS "http://localhost:8000/api/v1/commands?control_loop_id=${LOOP_ID}&limit=1" |
    json 'json.load(sys.stdin)["items"][0]["id"]'
)
curl -fsS "http://localhost:8000/api/v1/commands/${COMMAND_ID}"

TRIGGER_ID=$(
  curl -fsS "http://localhost:8000/api/v1/commands/${COMMAND_ID}" |
    json 'json.load(sys.stdin)["trigger_sample_id"]'
)
curl -fsS "http://localhost:8000/api/v1/commands?trigger_sample_id=${TRIGGER_ID}"
```

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
      "reported_point_id": null,
      "status": "active",
      "state": {"value": null, "quality": "no_data", "observed_at": null}
    }
  ]
}
```

- a point's own fields appear once, in `points`. A zone lists only the links, so
  a point taking part in three zones is still described once and cannot be
  described three inconsistent ways;
- `reported_point_id` is the point's explicit
  [control → reported relationship](#the-control--reported-point-relationship).
  A client discovering what it may command reads it from here, before any
  command exists: it is `null` on everything that is not a control point, and on
  a control point whose feedback has not been configured;
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
  "unit": "°C",
  "reported_point_id": null
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
  "reported_point_id": null,
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
- `PATCH` accepts `name`, `unit`, `min_value`, `max_value`, `reported_point_id`
  and `status` only. `code`, `site_id`, `facility_id`, `point_kind`,
  `metric_type` and `data_type` all define what the point *means*: changing one
  would reinterpret every value already recorded against the point without
  touching any of them;
- `unit`, `min_value`, `max_value` and `reported_point_id` are nullable, so in a
  `PATCH` an explicit `null` clears them while omitting the field leaves the
  stored value alone. The result is still validated against the point's
  `data_type`, so a range left on a point cannot outlive the type that carries
  it;
- there is no `DELETE`, and a site or facility that still has points cannot be
  deleted at the database level either (`ON DELETE RESTRICT`).

### The control → reported point relationship

`reported_point_id` is the explicit answer to *which point says whether this
actuator is really on*. A control point names it; nothing else carries it, and
nothing derives it. It is the prerequisite for
[a manual command](#manual-commands), and it is published by
[the facility configuration](#facility-configuration) and by the zone
composition, so a client can find it before any command exists.

```bash
curl -X PATCH http://localhost:8000/api/v1/points/$FAN_POWER_ID \
  -H 'content-type: application/json' \
  -d '{"reported_point_id": "'"$FAN_RUNNING_ID"'"}'
```

It can also be supplied in the creation body. Both paths apply the same rules:

- only a `control` point may carry the relationship;
- the related point must exist, be `active`, and be a `status` point;
- its `data_type` must match the control point's, so a boolean actuator is
  reported back as a boolean;
- both points must belong to the same site **and** the same facility;
- a point may not report itself.

**Nothing about this relationship is ever inferred.** Not from a code, a name, a
unit, a `metric_type`, a substring or a position in a zone's point list. A
`fan_power` point and a `fan_running` point sitting beside each other in one zone
are unrelated until someone relates them, and an existing `ControlLoop` naming
both does not establish it either — the loop's own `control_point_id`/
`status_point_id` pair stays exactly where it is and is not copied here. The
migration that adds the column leaves it `NULL` everywhere for the same reason:
a relationship inferred for some points and absent for others would be a rule
that is true by accident.

Two halves of the rule are held by PostgreSQL directly, because they fit in one
row: `reported_point_id IS NULL OR point_kind = 'control'`, and
`reported_point_id <> id`. The rest spans two rows and is enforced by the
service.

| Situation | HTTP | `error.code` | `details.reason` |
| --- | --- | --- | --- |
| `reported_point_id` references no point | 404 | `point_not_found` | — |
| The owning point is not a `control` point | 409 | `invalid_reported_point` | `point_kind_not_control` |
| A point named itself | 409 | `invalid_reported_point` | `reported_point_is_self` |
| The related point is archived | 409 | `invalid_reported_point` | `reported_point_not_active` |
| The related point is not a `status` point | 409 | `invalid_reported_point` | `reported_point_kind_mismatch` |
| The two data types differ | 409 | `invalid_reported_point` | `reported_point_data_type_mismatch` |
| The related point is in another site or facility | 409 | `invalid_reported_point` | `reported_point_not_in_facility` |

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

Configure the loop of a climate zone:

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

## Commands API

A command is one boolean state change addressed to one logical control point. It
has two possible authors, and it says which one it had:

- a **control loop** decides one from an accepted measurement. Nothing a client
  does creates one, which is what keeps "the fan follows the policy" true;
- a **customer-facing client** asks for one directly, through the `POST` below,
  for one explicitly configured actuator and one boolean value.

Both kinds are written `pending` and leave the cloud through the same gateway
resolution, the same [Edge command polling](#cloud--edge-contracts) and the same
acknowledgement. There is no second queue and no second delivery path.

```http
POST /api/v1/commands
GET  /api/v1/commands?control_zone_id=&control_loop_id=&trigger_sample_id=&target_point_id=&source=&idempotency_key=&limit=
GET  /api/v1/commands/{command_id}
```

```bash
curl 'http://localhost:8000/api/v1/commands?control_zone_id=7c0f2b91-4d5e-4a03-9c18-2f6b8e1d4a72&limit=1'
```

```json
{
  "items": [
    {
      "id": "2fbf6c2f-3ed5-5207-b1e5-069f900506a0",
      "source": "control_loop",
      "idempotency_key": "hysteresis-v1:e03c6179-2f50-4cb4-d8ea-1b46094253af:08cfcf34-39d1-5052-b4ca-964cc1d0e0ee:off",
      "control_zone_id": "7c0f2b91-4d5e-4a03-9c18-2f6b8e1d4a72",
      "control_loop_id": "e03c6179-2f50-4cb4-d8ea-1b46094253af",
      "trigger_sample_id": "08cfcf34-39d1-5052-b4ca-964cc1d0e0ee",
      "target_point_id": "c81a4f57-0d3e-4a92-b6c8-9f24e7031a5d",
      "reported_point_id": "3b5a1d90-77c2-4e6f-b8a1-0d4e9c2f5613",
      "gateway_id": "1f2e3d4c-5b6a-4798-8c0d-1e2f3a4b5c6d",
      "desired_value": false,
      "state": "applied",
      "result_control_sample_id": null,
      "result_status_sample_id": null,
      "issued_at": "2026-07-30T09:02:14.660118Z",
      "executed_at": null,
      "acknowledged_at": "2026-07-30T09:02:16.204915Z",
      "rejection_reason": null,
      "created_at": "2026-07-30T09:02:14.674984Z"
    }
  ]
}
```

### The lifecycle

`state` is the delivery lifecycle and has exactly three values. No speculative
state — `failed`, `expired`, `cancelled`, `unavailable` — exists, because the
backend has no transition that would produce one.

| `state` | Terminal | Meaning |
| --- | --- | --- |
| `pending` | no | Written and waiting. The gateway may or may not have collected it yet. |
| `applied` | **yes** | The gateway reported that it carried the command out. |
| `rejected` | **yes** | The gateway reported that it could not, with a typed reason. |

Three things follow, and a customer-facing client that gets any of them wrong
will show the wrong thing:

- **HTTP acceptance is not physical application.** `201` means one command was
  persisted. Nothing has been sent anywhere yet, and nothing has moved;
- **`acknowledged_at` is receipt, not success.** A `pending` command with an
  `acknowledged_at` has reached the Edge and nothing more. It stays non-terminal
  until it becomes `applied` or `rejected`;
- **the desired value is not the reported state.** `desired_value` is what was
  asked for. What the actuator actually reports is the reported point's own
  state, read separately through `GET /api/v1/points/{point_id}/state`. The two
  can disagree for as long as the physical change takes — or forever, if it
  never happens — and neither one is derived from the other. Acknowledging a
  command writes no telemetry and touches no point state.

`rejection_reason` is typed rather than an open object: `{"code": "...",
"message": "..."}`, where `code` matches `^[a-z][a-z0-9_]*$`. It is the same
representation the gateway supplies over the Cloud ↔ Edge v1 acknowledgement, so
there is one shape and not two that can drift.

An absent acknowledgement means nothing has been reported. It is **not** evidence
that the Edge is offline, and this API never claims it is.

### Manual commands

`POST /api/v1/commands` is the boundary a customer-facing client switches one
actuator through. It is on/off only: there is no numeric, percentage, dimming,
speed or free-form JSON control anywhere in this backend.

```bash
curl -i -X POST http://localhost:8000/api/v1/commands \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: 6f1c2a80-9e34-4b57-8d21-0a7f5c3e9b46' \
  -d '{"control_zone_id": "7c0f2b91-4d5e-4a03-9c18-2f6b8e1d4a72",
       "target_point_id": "c81a4f57-0d3e-4a92-b6c8-9f24e7031a5d",
       "desired_value": true}'
```

```json
{
  "outcome": "created",
  "command": {
    "id": "d4b8e1a2-3c57-4f09-b6d8-2e5a7c1f4903",
    "source": "manual",
    "idempotency_key": "6f1c2a80-9e34-4b57-8d21-0a7f5c3e9b46",
    "control_zone_id": "7c0f2b91-4d5e-4a03-9c18-2f6b8e1d4a72",
    "control_loop_id": null,
    "trigger_sample_id": null,
    "target_point_id": "c81a4f57-0d3e-4a92-b6c8-9f24e7031a5d",
    "reported_point_id": "3b5a1d90-77c2-4e6f-b8a1-0d4e9c2f5613",
    "gateway_id": "1f2e3d4c-5b6a-4798-8c0d-1e2f3a4b5c6d",
    "desired_value": true,
    "state": "pending",
    "result_control_sample_id": null,
    "result_status_sample_id": null,
    "issued_at": "2026-08-03T09:14:02.118374Z",
    "executed_at": null,
    "acknowledged_at": null,
    "rejection_reason": null,
    "created_at": "2026-08-03T09:14:02.118374Z"
  }
}
```

The body is exactly three fields — `control_zone_id`, `target_point_id` and
`desired_value` — and refuses any other. `desired_value` is **strictly**
boolean: `1`, `"on"` and `"true"` are refused rather than coerced, which is the
same rule the telemetry boundary applies to a boolean point's value.

A manual command carries `source: "manual"`, and `control_loop_id` and
`trigger_sample_id` are `null`. Nothing fabricates a loop or a trigger sample to
fill them: a manual command is not a decision, and saying otherwise would make
every automatic command's provenance meaningless too.

#### Eligibility

Every rule below is applied to persisted domain data, and the refusal names the
reason:

| Situation | HTTP | `error.code` | `details.reason` |
| --- | --- | --- | --- |
| Body invalid, or `desired_value` not boolean | 422 | `validation_error` | — |
| `Idempotency-Key` missing or not a UUID | 422 | `validation_error` | — |
| `control_zone_id` references no zone | 404 | `control_zone_not_found` | — |
| `target_point_id` references no point | 404 | `point_not_found` | — |
| The zone is archived | 409 | `invalid_manual_command_target` | `zone_not_active` |
| The point belongs to another facility | 409 | `invalid_manual_command_target` | `point_not_in_zone_facility` |
| The point is not assigned to that zone | 409 | `invalid_manual_command_target` | `point_not_assigned_to_zone` |
| It is assigned, but not as `control_output` | 409 | `invalid_manual_command_target` | `assignment_role_not_control_output` |
| Its `point_kind` is not `control` | 409 | `invalid_manual_command_target` | `point_kind_not_control` |
| It is archived | 409 | `invalid_manual_command_target` | `point_not_active` |
| Its `data_type` is not `boolean` | 409 | `invalid_manual_command_target` | `point_data_type_not_boolean` |
| It names no reported point | 409 | `invalid_manual_command_target` | `reported_point_not_configured` |
| Its reported point is missing, archived, not a status point or not boolean | 409 | `invalid_manual_command_target` | `reported_point_not_found`, `reported_point_not_active`, `reported_point_kind_mismatch`, `reported_point_data_type_not_boolean` |
| No active gateway is authorized for both points | 409 | `invalid_manual_command_target` | `no_gateway_for_command_points` |
| The key already names a different request | 409 | `idempotency_key_conflict` | — |

**A point's code, name, unit, `metric_type` or position in a zone takes part in
none of this.** A measurement point called "Fan Power" with `metric_type:
fan_power` is refused exactly as firmly as one called anything else.

Two further rules the table cannot show:

- creation is atomic. A refused request leaves no row, has not consumed its
  idempotency key, and can be corrected and retried with the same key;
- creation never marks a command applied, never writes telemetry and never
  touches a point's current state. It is not a control loop either: no
  `ControlLoop` is created, read or changed by a manual command.

#### Idempotency

`Idempotency-Key` is a **required request header** carrying a **UUID**. The
server never generates or replaces a key a client supplied.

| Request | Answer |
| --- | --- |
| First time the key is seen | `201`, `outcome: "created"` |
| Same key, same zone, target and value | `200`, `outcome: "existing"`, the stored command |
| Same key, anything else different | `409 idempotency_key_conflict`, naming the stored `command_id` |

The identity of a logical request is its **source, zone, target point and
desired value**. Everything else about a stored command — its state, its
acknowledgement, its gateway — is what happened *to* it, so a replay long after
the fact returns the command as it is *now*, terminal state included, and still
causes nothing.

A replay writes no row and offers the Edge no second delivery, so retrying a
request whose response was lost cannot switch an actuator twice. The guarantee is
the unique index on `commands.idempotency_key` and one `INSERT ... ON CONFLICT DO
NOTHING`, not a lookup performed a moment before the insert: concurrent identical
requests converge on one row, and concurrent conflicting ones still cannot
produce two.

If the creation response is lost entirely, the key is enough to recover:

```bash
curl 'http://localhost:8000/api/v1/commands?idempotency_key=6f1c2a80-9e34-4b57-8d21-0a7f5c3e9b46'
```

Uniqueness makes the answer zero commands (the request never landed) or exactly
one (it did).

### Reading commands

- **`POST` creates manual commands only.** A control-loop command is still a
  consequence of accepted telemetry and cannot be requested;
- the list is newest first, ordered `created_at DESC, id DESC` — an order the
  query itself enforces — and carries no total. `limit` defaults to `100` and
  must be between `1` and `1000`;
- every filter is an exact match and several may be combined: `control_zone_id`,
  `control_loop_id`, `trigger_sample_id`, `target_point_id`, `source` and
  `idempotency_key`;
- `source` is `control_loop` or `manual`, read from the stored discriminator
  rather than guessed from a null `control_loop_id`;
- an automatic command is created only when a *new* sample became the point's
  current state. A re-delivered or late measurement changes nothing;
- an automatic command's `idempotency_key` is
  `hysteresis-v1:{control_loop_id}:{trigger_sample_id}:{on|off}`; a manual
  command's is the UUID the client supplied. Both live in one unique column;
- `result_control_sample_id` and `result_status_sample_id` are written only by
  the in-process loopback actuator, and are `null` for everything a gateway
  carries out. `executed_at` follows the same rule; `acknowledged_at` is what
  dates a gateway-delivered command;
- the sample identifiers are returned rather than embedded. Read the samples
  themselves through `GET /api/v1/points/{point_id}/telemetry`.

| Situation | HTTP | `error.code` |
| --- | --- | --- |
| `limit` outside `1..1000` | 422 | `validation_error` |
| Command does not exist | 404 | `command_not_found` |

**The Customer Portal UI is not in this repository.** This is the backend
capability it needs; the interface itself lives in `greenhouse-dashboard` and is
built there.

## Boundaries

The cloud owns the domain APIs and none of the data reachable through them: it
creates no site, facility, zone, point, gateway, telemetry sample or command of
its own, at startup or anywhere else. The following are not part of the system:

- no seed, demo dataset, bootstrap command or startup fixture. There is no
  cloud-owned Basil growbox;
- **no agronomy of any kind**: no crop, growing recipe, recipe version, stage,
  target requirement, grow cycle, stage instance or RuntimeTarget, and no
  recipe-driven automation. Milestone 5 delivered all of it and it was rolled
  back — see [Rolled back: Milestone 5](#rolled-back-milestone-5);
- no cultivar, inventory or generic policy/rules engine;
- no executable environment simulation, simulated time or virtual device: that
  is `greenhouse-simulation-lab`, which reaches this application only as an
  ordinary HTTP client;
- no `Area` physical-space hierarchy;
- no assignment history — removing a zone-point assignment removes only that
  current link;
- no production authentication, users, RBAC or multi-tenancy;
- no devices, channels, bindings, or physical addresses;
- no device discovery, MQTT, offline queue or driver registry;
- no owner-facing frontend of any kind: no page, asset, template, static mount,
  framework, build pipeline or design system, and no route that serves,
  redirects to or proxies one. The owner UI is the separate
  `greenhouse-dashboard` repository;
- no dashboard aggregate endpoint, no persisted event log and no WebSocket or
  SSE: a monitoring client polls the existing read resources;
- no CORS configuration: a client that needs this API on its own origin proxies
  it, which is that client's concern and not the backend's;
- no way for a client to create an *automatic* command: a control-loop command
  is a consequence of accepted telemetry, and `POST /api/v1/commands` creates
  manual commands only;
- no numeric, percentage, dimming, speed or free-form JSON command submission:
  [manual control](#manual-commands) is boolean on/off, for one explicitly
  configured control point at a time;
- no delivery attempts, retries, leases or expiry, and no separate execution
  record: a command carries the pull-delivery lifecycle and nothing else;
- no synchronous wait for physical application, and no Activity feed, command
  analytics or aggregation endpoint built on command history;
- no policy versions, schedules, PID or rules engine: a control loop carries
  its own `hysteresis-v1` thresholds and nothing else;
- no in-process producer and no distributed workers: the application owns no
  background task.

Point state is filled by append-only telemetry, and every producer offers it
through the one public Edge ingestion path.

## Rolled back: Milestone 5

Milestone 5 delivered an agronomy catalog, a grow cycle lifecycle and a
`RuntimeTarget` that fed the control loop. It was rolled back out of the active
product on 2026-08-03, before it had grown anything. These no longer exist —
not as a model, an endpoint, a table or a schema field:

| Removed | What it was |
| --- | --- |
| `Crop`, `GrowingRecipe`, `RecipeVersion`, `RecipeStage`, `TargetRequirement` | The agronomy catalog and its `/api/v1/crops`, `/growing-recipes` and `/recipe-versions` endpoints |
| `GrowCycle`, `GrowCycleZoneAssignment`, `GrowStageInstance` | The cycle lifecycle and its `/api/v1/grow-cycles` endpoints, including `activate`, `complete` and `abort` |
| `RuntimeTarget` | The immutable temperature snapshot, its `/api/v1/runtime-targets` reads, and its precedence over the control loop |
| `commands.runtime_target_id` | The provenance column and field on the command representation |
| The grow cycle section of the then-embedded dashboard | The read-only recipe, stage, target, humidity and photoperiod panel |

Those paths now answer HTTP 404 like any other unrouted path, and the generated
OpenAPI document names none of them.

**`hysteresis-v1` is back to one source.** A control loop decides on its own
immutable `lower_threshold` and `upper_threshold` and on nothing else. Every
other behaviour of the flow is unchanged: the same strict boundaries, the same
ON / no-op / OFF decisions, the same idempotency key and the same Cloud ↔ Edge
v1 delivery. An Edge client sees no difference whatsoever — the v1 envelope
never carried a runtime target.

Nothing is rewritten to achieve this. `migrations/versions/20260801_0016`,
`_0017` and `_0018` are published history, still present and still run;
[`20260803_0019`](migrations/versions/20260803_0019_remove_agronomy_and_grow_cycles.py)
is a **forward compensating migration** that drops `commands.runtime_target_id`
before `runtime_targets`, and the grow-lifecycle tables before the catalog they
reference. Its `downgrade` rebuilds the schema so the migration path round-trips
and `alembic check` stays clean at the old head — **it recovers no rows**. A
deployment that ran a grow cycle loses its crops, recipes, cycles and targets
for good; its telemetry, current state and command history are untouched.
Recovering the deleted rows is a restore from backup, not a downgrade.

The reasoning is recorded in
[`docs/decisions/0002-first-harvest-automation-boundary.md`](docs/decisions/0002-first-harvest-automation-boundary.md),
which supersedes ADR 0001.

## Where this is going: the first basil harvest

The next product direction is a **basil growing journal plus sensor
monitoring**. None of it is implemented yet — this section states the boundary
so nothing above is mistaken for a step towards something else.

- **Monitoring only**: temperature, air humidity, soil moisture and, optionally,
  measured light. The cloud records them and acts on none of them.
- **One planned automated function**: a lighting **photoperiod**.
- **Not implemented by the rollback**: journal entities, photos, an event
  timeline, charts of journal data, a lighting schedule and a manual override.
- **Out of scope for the first harvest**: automatic irrigation, fan, heating,
  humidity, nutrients, pH/EC, adaptive brightness, adaptive photoperiod and
  agronomic recipes.

The generic control loop and command infrastructure described above is kept
because a photoperiod is the kind of thing it exists for. It is not configured
or advertised for the first harvest: temperature and fan automation are a
demonstration of the mechanism, not a feature of the coming milestone.

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

After the rebuild the database holds the Alembic version row and nothing else:
`GET /health` reports `"database": "ok"` and `GET /api/v1/sites` answers
`{"items": [], "total": 0, ...}` until something provisions.

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
├── api/                 # HTTP routes, dependencies and the error envelope
├── core/                # Settings, logging, shared value types and exceptions
├── topology/            # Site, Facility, ControlZone and zone point assignments
├── points/              # Point and PointCurrentState: the logical values
├── telemetry/           # Append-only samples and read-only history
├── control/             # Hysteresis loops, commands and the actuator boundary
├── gateways/            # Stable gateway identities and point authorization
├── edge/                # The Cloud ↔ Edge v1 telemetry and command adapter
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
