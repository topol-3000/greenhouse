/**
 * The point-state half of the page, which Unit 4 must leave alone.
 *
 * The readings, the chart and the activity list were rendered from the same
 * three resources before the agronomic section existed. These assertions are the
 * regression the new section is measured against: they read the delivered
 * `dashboard.js` through the same stub document and touch no agronomy at all.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { API, ok, render } from "./harness.mjs";
import { LOOP_ID, POINTS, cycle, routes } from "./fixtures.mjs";

const TEMPERATURE_POINT = POINTS.find((point) => point.code === "air_temperature");
const FAN_POINT = POINTS.find((point) => point.code === "fan_running");

const COMMANDS_URL = `${API}/commands?control_loop_id=${LOOP_ID}&limit=20`;
const HISTORY_URL = `${API}/points/${TEMPERATURE_POINT.id}/telemetry?limit=60`;

test("the three readings are rendered with the unit their point carries", async () => {
  const loaded = await render(routes([cycle()]));

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
