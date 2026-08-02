# Dashboard JavaScript tests

The dashboard is plain HTML, CSS and JavaScript served by the API, and these are
the tests that execute it. They cover what no Python test can reach: which grow
cycle the page selects, what it writes into the markup, and how it behaves while
a read is in flight, has failed or has returned something inconsistent.

```bash
node --test tests/javascript/*.test.mjs   # directly
docker compose run --rm api pytest        # skips: the API image carries no Node
```

`test_dashboard_javascript.py` runs the same suites from `pytest` and skips when
Node is not on the path. The API image is a Python image, so the canonical
Compose command reports these as skipped rather than as run; run them with Node
before opening a pull request that touches the page.

## What is here

| File | Contents |
| --- | --- |
| `harness.mjs` | The stub document, the routing `fetch` and the captured poll. |
| `fixtures.mjs` | The public API bodies the tests answer with. |
| `dashboard_agronomy.test.mjs` | Cycle selection, the agronomic view and its states. |
| `dashboard_readings.test.mjs` | The readings, chart and activity list, unchanged by Unit 4. |

## Rules

1. **No dependencies.** `node:test`, `node:assert` and `node:vm` only. Adding a
   `package.json`, a lockfile, jsdom or a test framework is out of scope for the
   whole product, not just for these tests.
2. **The delivered file is what runs.** `dashboard.js` is evaluated unmodified in
   a fresh `node:vm` context, and the elements come from `index.html` itself, so
   a field the script writes to but the markup does not declare fails here.
3. **The stub is only as large as the page needs.** It implements the four
   selector shapes and the handful of element operations `dashboard.js` uses. A
   new DOM feature on the page is a deliberate addition to `harness.mjs`, which
   is the point: the page stays small enough to test without a browser.
4. **Fixtures are the API's shapes.** They mirror what
   `tests/integration/factories.py` provisions over HTTP. When a response schema
   changes, the integration tests fail first; these fixtures follow.
5. **Values live in the tests, never in the page.** Crop, recipe, stage and
   target values are written by a fixture and read back out of the document, and
   one test renames all of them to prove the page hard-codes none.
