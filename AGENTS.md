# AI Greenhouse agent guide

This repository contains the modular-monolith backend for AI Greenhouse.

## Required reading order

Before changing code:

1. Read this file.
2. Read [`docs/agent-context.md`](docs/agent-context.md).
3. Read the one Jira Story being implemented.
4. Inspect only the relevant code and tests.
5. Open one additional architecture document or ADR only when a concrete
   unanswered question requires it.

Do not automatically read all of Confluence, the parent Epic, completed
milestones or stories, audit records, or historical diagrams.

## Commands

```bash
docker compose up --build
docker compose run --rm api pytest
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic check
uv run ruff check .
uv run ruff format --check .
node --test tests/javascript/*.test.mjs
```

Integration tests require the real PostgreSQL service; SQLite is not a
substitute. The dashboard's JavaScript tests require Node, which the API image
does not carry, so `pytest` skips them inside Compose and they are run with the
command above. Read [`tests/README.md`](tests/README.md) before adding tests.

## Source ownership

- The repository is authoritative for implemented behavior, module structure,
  migrations, development rules, verification commands, and ADRs.
- Confluence is authoritative for target architecture, the domain model,
  roadmap order, and milestone scope.
- The current Jira Story describes only the task-specific delta.
- The pull request records what changed, why, and how it was verified.

If sources conflict, follow the resolution rules in
[`docs/agent-context.md`](docs/agent-context.md) and report the conflict rather
than copying it into another source.
