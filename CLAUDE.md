# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

AI Greenhouse is a modular-monolith FastAPI backend for greenhouse automation.
Python 3.14, PostgreSQL 18, SQLAlchemy 2 async, Alembic, Pydantic v2,
structlog. Dependencies are managed with `uv`; the app runs under Docker
Compose.

Work is delivered milestone by milestone (M0, M1, …). Only what the current
milestone calls for exists — do not add auth, telemetry, simulation, caching,
or a frontend on your own initiative.

## Jira workflow (required)

Planning lives in Jira project `KAN` at `twinkling-rain.atlassian.net`, reached
through the Atlassian MCP server. Plain `WebFetch` on a `/browse/KAN-N` URL
returns only the SPA shell — always use the MCP tools.

Before writing code:

1. `getJiraIssue` for the ticket, and read the Confluence page it links in
   space `AIGH`. The story is a summary; the scope page carries the binding
   detail (data model, invariants, API contract, repo layout) and is written in
   Russian. Read both.
2. **Move the ticket to `In Progress`** as soon as you start work on it. Use
   `getTransitionsForJiraIssue` to find the transition id, then
   `transitionJiraIssue`. Do this once, at the start — not per commit.

When the branch is pushed and the PR is open:

3. **Move the ticket to `In Review`** once the PR is ready for review. Same
   two-step: look up the transition, then apply it. Post the PR link as a
   comment on the issue with `addCommentToJiraIssue`.

If a transition is not available (the workflow has no such step from the
current status), say so rather than picking an unrelated status.

## Git and PRs

- Branch from `main` as `claude/kan-<number>-<short-slug>`, matching the ticket.
- Commit subjects are lowercase and imperative, no ticket prefix:
  `add shared value types and pagination helper`.
- One PR per story branch. The `gh` CLI is pre-allowed for PR operations.

## Python style (required)

- **Type hints everywhere, including variables** — not just function
  signatures. Module constants, instance attributes assigned in `__init__`, and
  locals whose type is not immediately obvious all carry annotations:

  ```python
  IMMUTABLE_FIELDS: frozenset[str] = frozenset({"code"})
  self._repository: SiteRepository = SiteRepository(session)
  site: Site = await service.create_site(payload)
  ```

- **Google-style docstrings** on every module, class, and function, with
  `Args:`, `Returns:`, and `Raises:` sections where they apply. Module
  docstrings explain the layer's responsibility and what it must *not* do.
  Never reST or NumPy style.
- Modern syntax throughout: `X | None` over `Optional[X]`, builtin generics,
  `StrEnum`, `Annotated` types for reusable constraints.
- Ruff is the linter and formatter (line length 100, rules `B,E,F,I,UP`). Run
  it before finishing.

## Architecture

```text
src/ai_greenhouse/
├── api/             # HTTP routes, dependencies, error handlers, pagination
├── core/            # Settings, logging, shared value types, base exceptions
├── infrastructure/
│   └── database/    # Async engine, metadata, declarative base, health probes
└── topology/        # Domain module: models, schemas, repository, service,
                     # exceptions
```

Layering is strict, and each layer's module docstring states its boundary:

- **Routes** parse the request, choose the status code, serialise the result.
  No SQLAlchemy statements, no business rules.
- **Service** holds invariants and raises domain exceptions from the module's
  `exceptions.py`. It must not import FastAPI. It flushes so constraint
  violations can still be translated; it does not commit.
- **Repository** holds SQLAlchemy statements and nothing else.
- The `get_session` dependency owns commit and rollback for the request.
- `api/errors.py` translates domain exceptions into HTTP responses. Responses
  never leak connection strings, credentials, or stack traces; technical detail
  goes to the secret-redacted JSON logs.

Further conventions to preserve:

- Shared constraints (`CodeStr`, `NameStr`, `TimezoneStr`) live in
  `core/types.py` — reuse them rather than redeclaring regexes in schemas.
- Enum columns use the `enum_column` helper: `VARCHAR` plus a `CHECK`
  constraint, storing enum *values*. Never native PostgreSQL enum types.
- Entities are retired with `PATCH {"status": "archived"}`; domain resources do
  not expose `DELETE`.
- Alembic is the only schema-change mechanism.

## Commands

```bash
docker compose up --build                                   # run the stack
docker compose run --rm api pytest                          # tests (real Postgres)
docker compose run --rm api alembic upgrade head            # apply migrations
docker compose run --rm api alembic revision --autogenerate -m "description"
uv run ruff check . && uv run ruff format --check .         # lint locally
```

GitHub Actions (`.github/workflows/ci.yml`) runs the same lint and test
commands on every push to `main` and every pull request.

Tests use `pytest-asyncio` in `auto` mode. Integration tests run against the
real PostgreSQL service and skip when no `DATABASE_URL` is set — SQLite is not
a supported substitute. Unit tests live in `tests/unit/`, integration tests in
`tests/integration/`.
