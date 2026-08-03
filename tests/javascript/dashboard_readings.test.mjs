/**
 * Everything the page renders: the readings, the chart and the activity list.
 *
 * All three come from the point states, the temperature history and the command
 * history — the only resources the page reads. These assertions execute the
 * delivered `dashboard.js` through the stub document, so a field the script
 * writes to but the markup does not carry fails here.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { API, DASHBOARD_JS, INDEX_HTML, ok, render } from "./harness.mjs";
import { LOOP_ID, POINTS, routes } from "./fixtures.mjs";

const TEMPERATURE_POINT = POINTS.find((point) => point.code === "air_temperature");
const FAN_POINT = POINTS.find((point) => point.code === "fan_running");

const COMMANDS_URL = `${API}/commands?control_loop_id=${LOOP_ID}&limit=20`;
const HISTORY_URL = `${API}/points/${TEMPERATURE_POINT.id}/telemetry?limit=60`;

test("the three readings are rendered with the unit their point carries", async () => {
  const loaded = await render(routes());

  assert.equal(loaded.text("temperature"), "23.4 °C");
  assert.equal(loaded.text("humidity"), "61.0 %");
  assert.equal(loaded.text("fan"), "Off");
  assert.equal(loaded.field("temperature").dataset.quality, "good");
});

test("a point nothing has measured yet reads as no data", async () => {
  const unmeasured = {
    ...routes(),
    [`${API}/points/${FAN_POINT.id}/state`]: ok({
      point_id: FAN_POINT.id,
      value: null,
      quality: "no_data",
      observed_at: null,
      received_at: null,
      revision: 0,
    }),
  };
  const loaded = await render(unmeasured);

  assert.equal(loaded.text("fan"), "No data");
  assert.equal(loaded.field("fan").dataset.quality, "no_data");
});

test("the chart summarises the samples it plotted", async () => {
  const history = [24.0, 23.0, 22.0].map((value) => ({
    value,
    observed_at: "2026-08-01T09:00:00Z",
    received_at: "2026-08-01T09:00:00Z",
  }));
  const loaded = await render({ ...routes(), [HISTORY_URL]: ok({ items: history }) });

  assert.equal(loaded.text("chart-summary"), "3 samples, 22.0–24.0 °C");
  assert.equal(loaded.field("chart-line").getAttribute("points").split(" ").length, 3);
});

test("recent activity merges commands and samples newest first", async () => {
  const commands = [
    {
      id: "c1",
      desired_value: true,
      state: "applied",
      issued_at: "2026-08-01T09:00:00Z",
      executed_at: "2026-08-01T09:00:05Z",
      acknowledged_at: null,
    },
    {
      id: "c2",
      desired_value: false,
      state: "pending",
      issued_at: "2026-08-01T09:00:20Z",
      executed_at: null,
      acknowledged_at: null,
    },
  ];
  const history = [
    { value: 26.5, observed_at: "2026-08-01T09:00:10Z", received_at: "2026-08-01T09:00:10Z" },
  ];
  const loaded = await render({
    ...routes(),
    [COMMANDS_URL]: ok({ items: commands }),
    [HISTORY_URL]: ok({ items: history }),
  });

  const entries = loaded.field("activity").children.map((item) => item.children[1].textContent);
  assert.deepEqual(entries, [
    "Fan switched off — pending delivery",
    "Temperature 26.5 °C",
    "Fan switched on",
  ]);
});

test("a facility nothing has done anything in says so", async () => {
  const loaded = await render({ ...routes(), [HISTORY_URL]: ok({ items: [] }) });

  assert.equal(loaded.text("chart-summary"), "No temperature history yet.");
  assert.equal(loaded.text("activity"), "Nothing has happened yet.");
});

/**
 * The rolled-back agronomic section, asserted where it would come back.
 *
 * Two halves, because either could return without the other: the delivered
 * assets no longer name a grow cycle, a recipe or a runtime target, and the
 * page reads no endpoint that served one. The second half is the stronger of
 * the two — a section rebuilt under different words would still have to fetch
 * something to fill itself.
 */
test("the page carries no grow cycle section and reads no agronomy endpoint", async () => {
  const delivered = [readFileSync(INDEX_HTML, "utf8"), readFileSync(DASHBOARD_JS, "utf8")];
  const removed = /grow[- _]?cycle|recipe|runtime[- _]?target|photoperiod|crop|agronom/i;
  for (const asset of delivered) {
    assert.equal(removed.test(asset), false);
  }

  const loaded = await render(routes());
  const paths = loaded.requested.map((request) => request.url);

  assert.deepEqual(loaded.unrouted, []);
  assert.equal(
    paths.some((path) => removed.test(path)),
    false,
  );
});
