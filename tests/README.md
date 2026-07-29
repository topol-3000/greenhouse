# Testing guidelines

Written after the topology test review (KAN-35). The suite went from 545 to
321 tests without losing coverage of a single public behaviour or domain
invariant, because most of what was removed asserted the same rule a second and
third time at a different layer.

The rule these guidelines exist to enforce is short:

> **Every rule is asserted at exactly one layer — the lowest one that can fail
> when the rule breaks. A second layer is added only when the layers can
> disagree.**

## Layout

```text
tests/
├── conftest.py                 settings and logging fixtures
├── unit/                       no database, no HTTP
└── integration/
    ├── conftest.py             migrated database, rolled-back transaction, ASGI client
    ├── factories.py            shared request builders — the setup of every test
    ├── api/                    cross-cutting HTTP behaviour, mostly without a database
    ├── topology/               sites, facilities, zones, assignments, configuration
    ├── points/                 points and their state projection
    └── telemetry/              the write boundary that fills that projection
```

Integration tests run against a real PostgreSQL instance and skip when
`DATABASE_URL` is unset. SQLite is not a substitute: the schema comes from the
migrations, and several tests exist precisely to check what PostgreSQL enforces.

## Which layer asserts what

| Layer | Asserts | Does **not** assert |
| --- | --- | --- |
| `unit/test_types.py` | Every accepted and rejected case of a shared value type — the slug pattern, the name bounds, the timezone lookup. | Anything about a particular entity. |
| `unit/test_*_schemas.py` | That a schema is *wired* to those types, which fields are required, optional or nullable, what a partial update records, and which fields must reach the service unvalidated. | The value tables again. One representative rejected value per field is enough. |
| `unit/test_pagination.py`, `unit/test_database_base.py` | Documented infrastructure contracts: the ordered and windowed statement, application-generated UUIDs, `timestamptz`, `VARCHAR` + `CHECK` enums, the naming convention. | Column definitions that no document names. |
| `integration/api/` | The HTTP envelope itself — error shape, status mapping, pagination window, session commit and rollback — against a probe route, once. | Per-entity repetitions of those. |
| `integration/<module>/` | Domain invariants through the real endpoints, each by its **refusal**; endpoint-specific filters; what PostgreSQL enforces and the service cannot. | Field validation, the pagination envelope or the archive lifecycle a second time. |
| `integration/telemetry/` | The one module with no endpoint of its own: its tests call the service on the `session` fixture, commit or roll back as `get_session` would, and assert what reached the database. | The state response shape or the point rules a second time. |
| `integration/test_migrations.py` | `upgrade head` on an empty database, `alembic check` clean, and a `downgrade base` that really empties it. | Table shapes that a domain test already asserts. |
| `integration/test_demo_seed.py`, `test_topology_demo.py`, `test_simulation_demo.py` | The seed is idempotent; each documented walkthrough works end to end in the documented order. | Any rule not visible from the scenario. |

## Rules of thumb

1. **Shared mechanics get one host entity.** The pagination envelope,
   deterministic paging, `updated_at` refresh, persistence of an update and the
   archive lifecycle are asserted on sites. Other modules assert only what is
   theirs: their code scope, their parents, their filters, their immutable
   fields.
2. **Enumerations get a membership test, not a case per member.** One test
   asserting `{member.value for member in X} == {...}` catches an added *and* a
   removed member; a loop over the members catches neither.
3. **Every invariant needs a refusal test.** A happy path that would also pass
   with the check deleted is not coverage. Where a refusal could still have
   written a row, assert the row count too.
4. **Parametrise to the boundaries.** Ends that meet, an end alone, one past the
   limit. A fourth equivalent value in the list adds runtime, not confidence.
5. **Do not assert the ORM back to itself.** Testing that a column is nullable
   because the model says it is nullable proves nothing. The exceptions are the
   contracts a document names — `points` carrying no address and no value,
   `control_zones` carrying no site — and those are written as *the whole
   column set*, so a field nobody thought to forbid still fails.
6. **Build fixtures with `factories.py`.** A test that needs a growbox calls
   `create_growbox`; it does not paste four `POST`\ s. When a request body
   changes, one file changes.
7. **Name the test after the behaviour.** `test_a_point_of_another_site_is_refused`,
   not `test_assign_409_case_3`. If a name needs the word "and", it is probably
   two tests — or two assertions that belong in one.

## Adding tests for new behaviour

Ask, in this order:

1. Can this break in a schema? → a case in `unit/`.
2. Is it a rule the service enforces? → one refusal test through the endpoint.
3. Does PostgreSQL enforce it independently of the service? → one test that
   attempts it against the constraint directly.
4. Does it change the schema? → the migration tests already cover upgrade,
   `check` and downgrade; add nothing unless the migration carries data.
5. Is it visible in a documented demo walkthrough? → extend that one test rather
   than writing a parallel one.

Anything that answers "no" four times over is probably not worth a test.
