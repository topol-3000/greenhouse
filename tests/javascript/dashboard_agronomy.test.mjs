/**
 * What the agronomic section of the dashboard shows, and what it refuses to.
 *
 * These are the client-side half of Unit 4. The endpoints themselves are
 * asserted against a real PostgreSQL in
 * `tests/integration/test_dashboard_read_model.py`; what is asserted here is the
 * mapping and the state machine between those responses and the page — which
 * cycle is selected, what is rendered from it, and what happens when the
 * responses cannot be trusted.
 *
 * Every value the section renders is written by a fixture and read back out of
 * the stub document. Nothing about Basil, the recipe, the stage or the numbers
 * is compiled into `dashboard.js`, and the test that proves it renames all of
 * them.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { API, deferred, drain, fails, loadDashboard, ok, render, settle } from "./harness.mjs";
import {
  ACTIVE_CYCLES_URL,
  ACTIVE_TARGET,
  CYCLE_ID,
  FACILITY_ID,
  OTHER_ZONE_ID,
  RECIPE,
  RECIPE_URL,
  STAGE,
  VERSION,
  VERSION_URL,
  cycle,
  page,
  routes,
} from "./fixtures.mjs";

/** The five values one complete agronomic view renders. */
function view(loaded) {
  return {
    name: loaded.text("cycle-name"),
    status: loaded.text("cycle-status"),
    recipe: loaded.text("cycle-recipe"),
    stage: loaded.text("cycle-stage"),
    temperature: loaded.text("cycle-temperature"),
    source: loaded.text("cycle-temperature-source"),
    humidity: loaded.text("cycle-humidity"),
    photoperiod: loaded.text("cycle-photoperiod"),
  };
}

/** Assert that the readings beside the section were rendered and kept. */
function assertReadingsIntact(loaded) {
  assert.equal(loaded.text("facility-name"), "Basil Growbox");
  assert.equal(loaded.text("temperature"), "23.4 °C");
  assert.equal(loaded.text("humidity"), "61.0 %");
  assert.equal(loaded.text("fan"), "Off");
  assert.equal(loaded.text("chart-summary"), "1 sample, 23.4 °C");
}

test("the complete active view renders every value the public reads carry", async () => {
  const loaded = await render(routes());

  assert.equal(loaded.state(), "active");
  assert.deepEqual(view(loaded), {
    name: "Basil Grow Cycle",
    status: "Active",
    recipe: "Default basil recipe v1",
    stage: "Vegetative",
    temperature: "22–26 °C",
    source: "Grow Cycle",
    humidity: "55–70 %",
    photoperiod: "16 h/day",
  });
  assert.equal(loaded.region("cycle-detail").hidden, false);
  assert.equal(loaded.field("cycle-message").hidden, true);
  assertReadingsIntact(loaded);
});

test("the active cycles of the displayed facility are read from the public list API", async () => {
  const loaded = await render(routes());
  const reads = loaded.requested.map((request) => request.url);

  assert.ok(reads.includes(ACTIVE_CYCLES_URL));
  assert.ok(reads.includes(VERSION_URL));
  assert.ok(reads.includes(RECIPE_URL));
  assert.deepEqual([...new Set(loaded.requested.map((request) => request.method))], ["GET"]);
  // One chain per refresh: nothing is read twice in a cycle.
  assert.equal(new Set(reads).size, reads.length);
});

test("a cycle assigned to another zone of the same facility is not displayed", async () => {
  const loaded = await render(routes([cycle({ climate_zone_id: OTHER_ZONE_ID })]));

  assert.equal(loaded.state(), "none");
  assert.equal(loaded.text("cycle-message"), "No active grow cycle");
  assert.equal(loaded.text("cycle-name"), "Grow cycle");
  assert.equal(loaded.text("cycle-temperature"), "");
  assertReadingsIntact(loaded);
});

test("the zone's own cycle is selected out of a facility running several", async () => {
  const mine = cycle();
  const other = cycle({
    id: "5b5b5b5b-5555-4555-8555-555555555555",
    name: "Other Zone Cycle",
    climate_zone_id: OTHER_ZONE_ID,
  });
  // Listed first, so a page that took `items[0]` would render the wrong one.
  const loaded = await render(routes([other, mine]));

  assert.equal(loaded.state(), "active");
  assert.equal(loaded.text("cycle-name"), "Basil Grow Cycle");
});

test("no active cycle at all renders the no-cycle state", async () => {
  const loaded = await render(routes([]));

  assert.equal(loaded.state(), "none");
  assert.equal(loaded.text("cycle-message"), "No active grow cycle");
  assertReadingsIntact(loaded);
});

for (const status of ["planned", "completed", "aborted"]) {
  test(`a ${status} cycle of this zone is never displayed as active`, async () => {
    const loaded = await render(
      routes([cycle({ status, active_runtime_target: null, ended_at: null })]),
    );

    assert.equal(loaded.state(), "none");
    assert.equal(loaded.text("cycle-message"), "No active grow cycle");
    assert.equal(loaded.text("cycle-status"), "");
    assert.equal(loaded.text("cycle-temperature"), "");
  });
}

test("a closed historical target is never presented as the current one", async () => {
  const closed = { ...ACTIVE_TARGET, effective_to: "2026-08-01T18:00:00Z" };
  const loaded = await render(routes([cycle({ active_runtime_target: closed })]));

  assert.equal(loaded.state(), "error");
  assert.equal(loaded.text("cycle-temperature"), "");
  assert.equal(loaded.text("cycle-temperature-source"), "");
  assertReadingsIntact(loaded);
});

test("an active cycle without an active target fails safely instead of borrowing the recipe", async () => {
  const loaded = await render(routes([cycle({ active_runtime_target: null })]));

  assert.equal(loaded.state(), "error");
  assert.equal(loaded.text("cycle-message"), "The grow cycle could not be read.");
  // The recipe states 22–26 too. Rendering it here would claim an executable
  // target that no control loop is running on.
  assert.equal(loaded.text("cycle-temperature"), "");
  assert.equal(loaded.text("cycle-temperature-source"), "");
  assertReadingsIntact(loaded);
});

test("two active cycles in one zone fail safely instead of being picked between", async () => {
  const second = cycle({ id: "5c5c5c5c-5555-4555-8555-555555555555", name: "Second Cycle" });
  const loaded = await render(routes([cycle(), second]));

  assert.equal(loaded.state(), "error");
  assert.equal(loaded.text("cycle-name"), "Grow cycle");
  assertReadingsIntact(loaded);
});

test("a stage belonging to another version is refused rather than combined", async () => {
  const foreign = { ...cycle().current_stage, id: "8b8b8b8b-8888-4888-8888-888888888888" };
  const loaded = await render(routes([cycle({ current_stage: foreign })]));

  assert.equal(loaded.state(), "error");
  assert.equal(loaded.text("cycle-stage"), "");
});

test("a failed recipe read leaves the point-state cards standing", async () => {
  const failing = { ...routes(), [VERSION_URL]: fails(500) };
  const loaded = await render(failing);

  assert.equal(loaded.state(), "error");
  assert.equal(loaded.text("cycle-message"), "The grow cycle could not be read.");
  assertReadingsIntact(loaded);
  // Nothing of the failure's internals reaches the reader.
  const banner = loaded.text("error-message");
  assert.match(banner, /^The API answered with HTTP 500\.$/);
  assert.equal(loaded.region("error").hidden, false);
});

test("retry reloads the section through the page's one retry control", async () => {
  const table = { ...routes(), [VERSION_URL]: fails(503) };
  const loaded = await render(table);
  assert.equal(loaded.state(), "error");

  loaded.routes[VERSION_URL] = ok(VERSION);
  loaded.retry();
  await settle(loaded);

  assert.equal(loaded.state(), "active");
  assert.equal(loaded.text("cycle-recipe"), "Default basil recipe v1");
  assert.equal(loaded.region("error").hidden, true);
});

test("a cycle that ended is replaced by the no-cycle state on the next refresh", async () => {
  const loaded = await render(routes());
  assert.equal(loaded.text("cycle-temperature"), "22–26 °C");

  // The cycle is completed externally: it leaves the active list and its target
  // is closed. Nothing tells the page — the next poll is what finds out.
  loaded.routes[ACTIVE_CYCLES_URL] = ok(page([]));
  loaded.poll();
  await settle(loaded);

  assert.equal(loaded.state(), "none");
  assert.equal(loaded.text("cycle-message"), "No active grow cycle");
  assert.equal(loaded.text("cycle-temperature"), "");
  assert.equal(loaded.text("cycle-status"), "");
  assertReadingsIntact(loaded);
});

test("a later refresh replaces the whole view rather than mixing two recipes", async () => {
  const loaded = await render(routes());

  const nextVersion = {
    ...VERSION,
    id: "7b7b7b7b-7777-4777-8777-777777777777",
    version_number: 2,
    stage: {
      ...STAGE,
      id: "8c8c8c8c-8888-4888-8888-888888888888",
      recipe_version_id: "7b7b7b7b-7777-4777-8777-777777777777",
      name: "Flowering",
      requirements: STAGE.requirements.map((requirement) => ({
        ...requirement,
        recipe_stage_id: "8c8c8c8c-8888-4888-8888-888888888888",
        ...(requirement.metric_type === "air_humidity"
          ? { min_value: 45, max_value: 60 }
          : {}),
        ...(requirement.metric_type === "photoperiod" ? { target_value: 12 } : {}),
      })),
    },
  };
  const next = cycle({
    id: "5d5d5d5d-5555-4555-8555-555555555555",
    name: "Second Basil Cycle",
    recipe_version_id: nextVersion.id,
    current_stage_id: nextVersion.stage.id,
    current_stage: { ...nextVersion.stage, requirements: undefined },
    active_runtime_target: {
      ...ACTIVE_TARGET,
      grow_cycle_id: "5d5d5d5d-5555-4555-8555-555555555555",
      target_requirement_id: nextVersion.stage.requirements[0].id,
      lower_value: 20,
      upper_value: 24,
    },
  });
  loaded.routes[ACTIVE_CYCLES_URL] = ok(page([next]));
  loaded.routes[`${API}/recipe-versions/${nextVersion.id}`] = ok(nextVersion);
  loaded.routes[RECIPE_URL] = ok({ ...RECIPE, version: nextVersion });
  loaded.poll();
  await settle(loaded);

  assert.deepEqual(view(loaded), {
    name: "Second Basil Cycle",
    status: "Active",
    recipe: "Default basil recipe v2",
    stage: "Flowering",
    temperature: "20–24 °C",
    source: "Grow Cycle",
    humidity: "45–60 %",
    photoperiod: "12 h/day",
  });
});

test("the section says it is loading while the cycle is still unresolved", async () => {
  const pending = deferred();
  const loaded = loadDashboard({ ...routes(), [ACTIVE_CYCLES_URL]: () => pending.promise });
  await drain();

  // The readings resolved; the cycle has not.
  assert.equal(loaded.state(), "loading");
  assert.equal(loaded.text("cycle-message"), "Reading the grow cycle…");
  assert.equal(loaded.region("cycle-detail").hidden, true);
  assertReadingsIntact(loaded);

  pending.resolve(ok(page([cycle()])));
  await settle(loaded);

  assert.equal(loaded.state(), "active");
  assert.equal(loaded.text("cycle-name"), "Basil Grow Cycle");
});

test("whole numbers keep no invented precision and real fractions survive", async () => {
  const halves = {
    ...ACTIVE_TARGET,
    lower_value: 22.5,
    upper_value: 26.0,
  };
  const loaded = await render(routes([cycle({ active_runtime_target: halves })]));

  assert.equal(loaded.text("cycle-temperature"), "22.5–26 °C");
  assert.equal(loaded.text("cycle-humidity"), "55–70 %");
  assert.equal(loaded.text("cycle-photoperiod"), "16 h/day");
});

test("the rendered names and numbers are the API's and not the page's", async () => {
  const renamed = {
    ...routes([
      cycle({
        name: "Lettuce Trial",
        current_stage: { ...cycle().current_stage, name: "Seedling" },
        active_runtime_target: { ...ACTIVE_TARGET, lower_value: 15, upper_value: 19, unit: "K" },
      }),
    ]),
    [VERSION_URL]: ok({
      ...VERSION,
      version_number: 7,
      stage: {
        ...STAGE,
        name: "Seedling",
        requirements: [
          { ...STAGE.requirements[0] },
          { ...STAGE.requirements[1], min_value: 40, max_value: 50, unit: "percent" },
          { ...STAGE.requirements[2], target_value: 18, unit: "hours/day" },
        ],
      },
    }),
    [RECIPE_URL]: ok({ ...RECIPE, name: "Winter lettuce" }),
  };
  const loaded = await render(renamed);

  assert.deepEqual(view(loaded), {
    name: "Lettuce Trial",
    status: "Active",
    recipe: "Winter lettuce v7",
    stage: "Seedling",
    temperature: "15–19 K",
    source: "Grow Cycle",
    humidity: "40–50 percent",
    photoperiod: "18 hours/day",
  });
});

test("an empty cloud renders the no-cycle state without reading a cycle at all", async () => {
  const loaded = await render({ [`${API}/sites`]: ok(page([])) });

  assert.equal(loaded.state(), "none");
  assert.equal(loaded.text("cycle-message"), "No active grow cycle");
  assert.equal(loaded.text("facility-name"), "No facility configured");
  assert.equal(loaded.region("empty").hidden, false);
  assert.equal(loaded.region("error").hidden, true);
  assert.equal(loaded.requested.length, 1);
});

test("the page mutates nothing and addresses no lifecycle action", async () => {
  const loaded = await render(routes());
  const reads = loaded.requested.map((request) => request.url);

  for (const request of loaded.requested) {
    assert.equal(request.method, "GET");
  }
  for (const forbidden of ["/activate", "/complete", "/abort", "/crops", "/simulation-runs"]) {
    assert.ok(
      reads.every((url) => !url.includes(forbidden)),
      `the page addressed ${forbidden}`,
    );
  }
  assert.ok(reads.every((url) => url.startsWith(API)));
  // The cycle it does read is addressed by the facility it shows, not by a
  // compiled-in identifier.
  assert.ok(reads.some((url) => url.includes(`facility_id=${FACILITY_ID}`)));
  assert.ok(reads.every((url) => !url.includes(CYCLE_ID)));
});
