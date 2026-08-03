# ADR 0003: An API-only backend and a separately maintained owner dashboard

- Status: Accepted
- Date: 2026-08-03

## Context

The owner interface was one framework-less page — `index.html`, `dashboard.css`
and `dashboard.js` — delivered by three routes of this application from
`api/static/`. That was deliberate while the page was small: served from the API
origin, it needed no CORS configuration, no build pipeline and no second
deployable, and the whole frontend fitted in a directory that could be read in
one sitting.

The owner monitoring dashboard replaces it with a real client application, and
that client is maintained in its own repository. Keeping the old page as well
would leave two owner-facing UIs in the product, one of which nobody intends to
develop; embedding the new client's build output here instead would put a
frontend toolchain, a bundle and a release cadence into a Python backend that
has neither.

Three ways to end up with one dashboard were available:

1. **Embed the new client's built assets in this repository** and keep serving
   the UI from the API origin. Same-origin for free, at the cost of a bundle
   checked into a Python distribution, a coupled release and a build step in the
   API image.
2. **Redirect or reverse-proxy** from this application to wherever the dashboard
   is hosted. No assets here, but the backend acquires knowledge of a frontend
   deployment, a URL to configure, and a failure mode that is neither the API's
   nor the dashboard's.
3. **Remove the frontend entirely** and let the dashboard repository own its own
   delivery.

## Decision

- `greenhouse` is an **API-only cloud backend**. The legacy page, its stylesheet,
  its script, the three routes that served them and the `api/static/` directory
  are removed. Nothing here serves HTML, CSS, JavaScript or a bundle, and
  nothing mounts a static directory.
- `GET /` is not replaced. It is an unrouted path answering the ordinary 404, not
  a redirect, a proxy or a placeholder page. The backend stays independently
  deployable as an API service, and starting it exposes no owner-facing UI.
- `greenhouse-dashboard` is the **sole owner-facing UI**, maintained separately.
  It is an ordinary client of the public `/api/v1` endpoints.
- **Same-origin delivery is the dashboard repository's responsibility.** If its
  deployment wants the API on its own origin, it proxies; this backend adds no
  CORS configuration, no proxy and no frontend-specific route for it.
- The **public API is unchanged** by the move. No aggregate dashboard endpoint,
  no dashboard-shaped response, no authentication, no WebSocket or SSE. The
  endpoints the dashboard reads — facilities, facility configuration, point
  metadata and state, telemetry history, commands — keep their paths, schemas,
  field names, status codes, limits and ordering.
- Simulation tooling (`greenhouse-simulation-lab`) and future real edge producers
  stay separate from the cloud backend and reach it only through the public
  Cloud ↔ Edge v1 contract.

## Consequences

The repository boundary is now the public HTTP API in both directions: a client
change cannot require a backend change unless the API itself is wrong, and a
backend change is judged against the published contract rather than against a
page in the same checkout. Neither repository can be released to fix the other's
rendering, which is the point.

Same-origin is no longer free. A browser reading this API from a different origin
needs the dashboard's proxy, because the backend sends no CORS headers and
gaining them is not a backend change made on a frontend's behalf.

Anyone who used to open `http://localhost:8000/` gets a 404. The equivalent is
`/docs` for the API itself, and the separate dashboard for an owner view.

The frontend tests left with the frontend: the executed-page suite under
`tests/javascript/` and the page-delivery tests are removed, and the backend
keeps the half of that boundary it can still see — that no page or asset is
served, that nothing mounts a static directory, that no frontend asset is
checked into the package, and that the API one dashboard frame reads still
answers, in `tests/integration/test_dashboard_read_model.py`.
