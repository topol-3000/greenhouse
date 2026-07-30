# AI Greenhouse agent context

This is the short, mandatory context for coding agents. The repository remains
the source of truth for what is implemented; linked Confluence pages describe
the target and are opened only when the current task needs them.

## Current implementation

- AI Greenhouse is a Python 3.14 modular-monolith backend served by FastAPI.
- PostgreSQL 18 is the only supported database; SQLAlchemy 2 uses async
  sessions and Alembic owns every schema change.
- Pydantic v2 defines API and service-boundary schemas. Dependencies are locked
  with `uv`; Docker Compose is the standard runtime and integration-test path.
- `core` owns settings, structured secret-redacted logging, shared value types,
  enum storage helpers, and base domain exceptions.
- `infrastructure/database` owns the async engine, declarative metadata,
  request sessions, health checks, and database startup waiting.
- `api` owns HTTP routing, dependencies, pagination, translation of domain errors
  into the common response envelope, and delivery of the one same-origin
  dashboard page from `api/static/`. Domain endpoints stay under `/api/v1`.
- `topology` owns `Site`, `Facility`, `ControlZone`, and zone-point
  assignments.
- `points` owns stable logical `Point` identities and `PointCurrentState`.
- `telemetry` owns append-only `TelemetrySample` persistence, the idempotent
  write boundary, current-state projection updates, and read-only history.
- `simulation` owns `SimulationRun` persistence, the pure frozen
  `simple-climate-v1` model, the `simple-climate-v2` model whose temperature
  target follows logical fan state, and the single-process in-application runtime
  that drives runs. New runs are server-owned v2; execution dispatches on the
  persisted `model_version`.
- `control` owns the immutable `hysteresis-v1` `ControlLoop`, the pure policy,
  the idempotent applied `Command`, the loopback actuator boundary, and the
  source-independent ingestion path every in-process producer offers telemetry
  on.
- `gateways` owns stable administrative gateway codes, operational gateway UUIDs,
  normalized site configuration, and additive one-owner logical-point
  authorization. Its management API is separate from the Edge data plane.
- `edge` owns the backward-compatible Cloud ↔ Edge v1 HTTP adapter for telemetry
  submission, gateway-scoped command polling, and terminal acknowledgement.
- `seed` creates the idempotent basil-growbox demo through domain services,
  carries the `demo-init` bootstrap that adds the `24-26 °C` control loop, and
  carries the controlled automation-demonstration driver. All three are explicit
  commands; none is a FastAPI startup hook. The local Compose entrypoint invokes
  `demo-init` after migrations and before Uvicorn.
- Domain modules use `models.py`, `schemas.py`, `repository.py`, `service.py`,
  and `exceptions.py` when those layers are needed.
- Routes handle HTTP only. Services enforce invariants and must not import
  FastAPI. Repositories contain SQLAlchemy queries, not business rules.
- Services flush but do not commit. The request-scoped session dependency owns
  commit and rollback.
- `api/errors.py` maps safe domain failures to HTTP; credentials, SQL, driver
  detail, and stack traces must never enter responses.

## Scope

Implemented: the growbox topology with stable logical point IDs, append-only
telemetry, the current-state projection, telemetry history, simulation runs, both
deterministic climate models, the in-application runtime, hysteresis control-loop
configuration, the automation flow that turns an accepted temperature into an
applied fan command, the idempotent `demo-init` bootstrap, one same-origin
dashboard page that starts, observes and stops the demo run, administrative HTTP
provisioning of stable Edge gateways and their authorized logical points, and
the Cloud ↔ Edge v1 telemetry and command-delivery API.

Out of scope: devices, MQTT, production authentication, users/RBAC/multi-tenancy,
a frontend framework or build pipeline, distributed workers, WebSocket/SSE, a
persisted event log, a dashboard aggregate endpoint, manual fan control, and any
endpoint that creates a command.

## Invariants and development rules

- A logical `Point` is stable and separate from a physical sensor or channel.
  Do not add device bindings.
- Telemetry samples are append-only. Reusing a sample ID is idempotent and must
  not increment the state revision.
- An out-of-order sample stays in history but cannot replace a newer current
  state. `observed_at` and `received_at` have different meanings.
- Telemetry values match `Point.data_type`; `bool` is not an integer. The unit
  is copied from the point as a snapshot.
- In-process producers offer telemetry through `TelemetryIngestionService`, not
  to `TelemetryService` directly, so automation runs whatever the source.
- Automation acts only on a sample that became the current state. A command is
  persisted only once applied, and it and both result samples are atomic; a
  failed application never rolls back the measurement that triggered it.
- `fan_power` and `fan_running` are written only through the telemetry
  boundary. `fan_running` is an output and never a policy input, and never a
  simulation-model input.
- The simulator reads logical `fan_power` and never writes it. Automation gains
  no simulation dependency: the direction is one-way, always.
- `simple-climate-v1` is frozen. A persisted v1 run must keep producing what it
  produced before; a change to the model arrives as a new version.
- The dashboard is a client of the public API. It adds no endpoint, resolves the
  demo growbox by stable code, and renders only persisted state.
- Reuse constraints from `core/types.py`. Enum columns use `VARCHAR` plus a
  `CHECK` through `enum_column`, never native PostgreSQL enums.
- Domain resources are archived rather than physically deleted.
- Use type hints throughout and Google-style docstrings for Python modules,
  classes, and functions. Use modern Python syntax.
- Ruff is the formatter and linter. Keep routes, services, and repositories
  within their documented boundaries.
- Integration behavior that depends on PostgreSQL is tested on PostgreSQL, not
  SQLite.
- Follow [`tests/README.md`](../tests/README.md): assert a rule at the lowest
  layer that can fail, and add another layer only when the layers can disagree.
- Create an ADR under `docs/decisions/` only for a durable decision with real
  alternatives that changes a cross-module or long-term architectural
  boundary. Local implementation details do not need ADRs.

For work tied to Jira, use the Atlassian integration rather than scraping its
web UI. Move the Story to `In Progress` when implementation starts and to
`In Review` after its PR is ready, then add the PR link to the Story. If the
required transition is unavailable, report that instead of choosing a
different status. Use one ticket branch and one PR per Story; keep commit
subjects short, lowercase, and imperative.

## Working context

For a normal task, read only:

1. [`AGENTS.md`](../AGENTS.md);
2. this file;
3. one current Jira Story;
4. relevant code and tests.

Open a linked architecture page or an ADR only to answer a specific question.
Do not automatically read the entire Confluence space, a parent Epic, closed
stories, completed scope documents, the context audit, or historical snapshot
diagrams.

## Canonical references

- [Context workflow](https://twinkling-rain.atlassian.net/wiki/spaces/AIGH/pages/4292610/AI-):
  context levels, ownership, and conflict handling.
- [Architecture overview](https://twinkling-rain.atlassian.net/wiki/spaces/AIGH/pages/1277954):
  target system boundaries; it is not proof of current implementation.
- [Domain model](https://twinkling-rain.atlassian.net/wiki/spaces/AIGH/pages/1376257):
  target terms and invariants; future entities are not implemented by default.
- [Roadmap](https://twinkling-rain.atlassian.net/wiki/spaces/AIGH/pages/1409026/roadmap):
  delivery sequence only.
- Current scope: none. Milestone 4 closed with the `0.1` release and its scope
  page is archived; the next milestone has no approved scope yet. Until one
  exists, the roadmap names the next product step and this file plus the code
  describe what is implemented.

When sources conflict, code, migrations, and executable tests describe current
reality. A current scope page, while one is active, wins for scope boundaries. A
Jira Story defines only its delta, and derived or historical documents never
override a primary source. Report the conflict and fix its canonical owner
separately instead of duplicating the disputed rule.
