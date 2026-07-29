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
- `api` owns HTTP routing, dependencies, pagination, and translation of domain
  errors into the common response envelope.
- `topology` owns `Site`, `Facility`, `ControlZone`, and zone-point
  assignments.
- `points` owns stable logical `Point` identities and `PointCurrentState`.
- `telemetry` owns append-only `TelemetrySample` persistence, the idempotent
  write boundary, current-state projection updates, and read-only history.
- `simulation` owns `SimulationRun` persistence, the pure `simple-climate-v1`
  model, and the single-process in-application runtime that drives runs.
- `seed` creates the idempotent basil-growbox demo through domain services.
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
telemetry, the current-state projection, telemetry history, simulation runs, the
deterministic climate model, and the in-application runtime.

Out of scope: commands, control loops, devices, Edge/MQTT, authentication, UI,
distributed workers, and a generic public ingestion endpoint.

## Invariants and development rules

- A logical `Point` is stable and separate from a physical sensor or channel.
  Do not add device bindings.
- Telemetry samples are append-only. Reusing a sample ID is idempotent and must
  not increment the state revision.
- An out-of-order sample stays in history but cannot replace a newer current
  state. `observed_at` and `received_at` have different meanings.
- Telemetry values match `Point.data_type`; `bool` is not an integer. The unit
  is copied from the point as a snapshot.
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
- [Current scope](https://twinkling-rain.atlassian.net/wiki/spaces/AIGH/pages/2850819/):
  the single canonical technical scope of the work in progress.

When sources conflict, code, migrations, and executable tests describe current
reality. The current scope page wins for scope boundaries. A Jira Story defines
only its delta, and derived or historical documents never override a primary
source. Report the conflict and fix its canonical owner separately instead of
duplicating the disputed rule.
