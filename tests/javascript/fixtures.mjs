/**
 * The public API responses the dashboard tests answer with.
 *
 * These are exactly the bodies the accepted Unit 1 and Unit 2 read endpoints
 * return — the same shapes `tests/integration` asserts against a real database,
 * written out here so the client can be exercised without one.
 *
 * The names and numbers are the same Basil ones `tests/integration/factories.py`
 * provisions over HTTP. They live in the *tests* on purpose: the point of most
 * of these assertions is that the page renders whatever the API said, and a
 * fixture that shared a constant with the page would prove nothing.
 */

import { API } from "./harness.mjs";

export const SITE_ID = "11111111-1111-4111-8111-111111111111";
export const FACILITY_ID = "22222222-2222-4222-8222-222222222222";
export const ZONE_ID = "33333333-3333-4333-8333-333333333333";
export const OTHER_ZONE_ID = "3b3b3b3b-3333-4333-8333-333333333333";
export const LOOP_ID = "44444444-4444-4444-8444-444444444444";
export const CYCLE_ID = "55555555-5555-4555-8555-555555555555";
export const RECIPE_ID = "66666666-6666-4666-8666-666666666666";
export const VERSION_ID = "77777777-7777-4777-8777-777777777777";
export const STAGE_ID = "88888888-8888-4888-8888-888888888888";
export const TARGET_ID = "99999999-9999-4999-8999-999999999999";
export const TEMPERATURE_REQUIREMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const HUMIDITY_REQUIREMENT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const PHOTOPERIOD_REQUIREMENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const POINT_IDS = {
  air_temperature: "d1111111-dddd-4ddd-8ddd-dddddddddddd",
  air_humidity: "d2222222-dddd-4ddd-8ddd-dddddddddddd",
  fan_power: "d3333333-dddd-4ddd-8ddd-dddddddddddd",
  fan_running: "d4444444-dddd-4ddd-8ddd-dddddddddddd",
};

const NOW = "2026-08-01T09:00:00Z";

/**
 * Wrap items in the paginated envelope every collection endpoint answers with.
 *
 * @param {Array<object>} items The page's items.
 * @returns {object} The envelope.
 */
export function page(items) {
  return { items, total: items.length, limit: 50, offset: 0 };
}

export const SITE = { id: SITE_ID, code: "home", name: "Home", status: "active" };

export const FACILITY = {
  id: FACILITY_ID,
  site_id: SITE_ID,
  code: "basil-growbox",
  name: "Basil Growbox",
  facility_type: "growbox",
  status: "active",
};

export const ZONE = {
  id: ZONE_ID,
  facility_id: FACILITY_ID,
  code: "main-climate",
  name: "Main Climate",
  zone_type: "climate",
  status: "active",
};

export const POINTS = [
  { code: "air_temperature", name: "Air Temperature", unit: "°C", data_type: "float" },
  { code: "air_humidity", name: "Air Humidity", unit: "%", data_type: "float" },
  { code: "fan_power", name: "Fan Power", unit: null, data_type: "boolean" },
  { code: "fan_running", name: "Fan Running", unit: null, data_type: "boolean" },
].map((point) => ({ ...point, id: POINT_IDS[point.code], facility_id: FACILITY_ID }));

export const LOOP = {
  id: LOOP_ID,
  control_zone_id: ZONE_ID,
  lower_threshold: 24.0,
  upper_threshold: 26.0,
};

export const REQUIREMENTS = [
  {
    id: TEMPERATURE_REQUIREMENT_ID,
    recipe_stage_id: STAGE_ID,
    metric_type: "air_temperature",
    requirement_kind: "range",
    unit: "°C",
    min_value: 22,
    max_value: 26,
    target_value: null,
  },
  {
    id: HUMIDITY_REQUIREMENT_ID,
    recipe_stage_id: STAGE_ID,
    metric_type: "air_humidity",
    requirement_kind: "range",
    unit: "%",
    min_value: 55,
    max_value: 70,
    target_value: null,
  },
  {
    id: PHOTOPERIOD_REQUIREMENT_ID,
    recipe_stage_id: STAGE_ID,
    metric_type: "photoperiod",
    requirement_kind: "duration_per_day",
    unit: "h/day",
    min_value: null,
    max_value: null,
    target_value: 16,
  },
];

export const STAGE = {
  id: STAGE_ID,
  recipe_version_id: VERSION_ID,
  code: "vegetative",
  name: "Vegetative",
  sequence_number: 1,
  requirements: REQUIREMENTS,
};

export const VERSION = {
  id: VERSION_ID,
  recipe_id: RECIPE_ID,
  version_number: 1,
  status: "published",
  published_at: NOW,
  created_at: NOW,
  stage: STAGE,
};

export const RECIPE = {
  id: RECIPE_ID,
  crop_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  code: "basil-default",
  name: "Default basil recipe",
  status: "active",
  created_at: NOW,
  version: VERSION,
};

export const ACTIVE_TARGET = {
  id: TARGET_ID,
  control_loop_id: LOOP_ID,
  grow_cycle_id: CYCLE_ID,
  target_requirement_id: TEMPERATURE_REQUIREMENT_ID,
  metric_type: "air_temperature",
  lower_value: 22,
  upper_value: 26,
  unit: "°C",
  effective_from: NOW,
  effective_to: null,
  created_at: NOW,
};

/**
 * Build one grow cycle representation, as `GET /api/v1/grow-cycles` returns it.
 *
 * @param {object} overrides Fields replacing the active-cycle defaults.
 * @returns {object} The cycle body.
 */
export function cycle(overrides = {}) {
  return {
    id: CYCLE_ID,
    code: "basil-demo-cycle",
    name: "Basil Grow Cycle",
    facility_id: FACILITY_ID,
    climate_zone_id: ZONE_ID,
    recipe_version_id: VERSION_ID,
    current_stage_id: STAGE_ID,
    status: "active",
    planned_start_at: null,
    started_at: NOW,
    ended_at: null,
    created_at: NOW,
    current_stage: {
      id: STAGE_ID,
      recipe_version_id: VERSION_ID,
      code: "vegetative",
      name: "Vegetative",
      sequence_number: 1,
    },
    active_runtime_target: ACTIVE_TARGET,
    ...overrides,
  };
}

/** The URL the page reads the displayed facility's active cycles from. */
export const ACTIVE_CYCLES_URL =
  `${API}/grow-cycles?facility_id=${FACILITY_ID}&status=active&limit=50`;

export const VERSION_URL = `${API}/recipe-versions/${VERSION_ID}`;
export const RECIPE_URL = `${API}/growing-recipes/${RECIPE_ID}`;

/**
 * Build the routing table of a facility that has measured something.
 *
 * The point-state half is the frame the dashboard already rendered before this
 * unit: it is here so every agronomic assertion also says what happened to the
 * readings beside it.
 *
 * @param {Array<object>} cycles Active cycles the facility answers with.
 * @returns {Record<string, object>} Answers by request URL.
 */
export function routes(cycles = [cycle()]) {
  const state = (code, value, quality) => ({
    point_id: POINT_IDS[code],
    value,
    quality,
    observed_at: NOW,
    received_at: NOW,
    revision: 1,
  });
  return {
    [`${API}/sites`]: { status: 200, body: page([SITE]) },
    [`${API}/facilities?site_id=${SITE_ID}`]: { status: 200, body: page([FACILITY]) },
    [`${API}/points?facility_id=${FACILITY_ID}`]: { status: 200, body: page(POINTS) },
    [`${API}/control-zones?facility_id=${FACILITY_ID}`]: { status: 200, body: page([ZONE]) },
    [`${API}/control-loops?control_zone_id=${ZONE_ID}`]: { status: 200, body: page([LOOP]) },
    [`${API}/points/${POINT_IDS.air_temperature}/state`]: {
      status: 200,
      body: state("air_temperature", 23.4, "good"),
    },
    [`${API}/points/${POINT_IDS.air_humidity}/state`]: {
      status: 200,
      body: state("air_humidity", 61.0, "good"),
    },
    [`${API}/points/${POINT_IDS.fan_running}/state`]: {
      status: 200,
      body: state("fan_running", false, "good"),
    },
    [`${API}/points/${POINT_IDS.air_temperature}/telemetry?limit=60`]: {
      status: 200,
      body: { items: [{ value: 23.4, observed_at: NOW, received_at: NOW }] },
    },
    [`${API}/commands?control_loop_id=${LOOP_ID}&limit=20`]: {
      status: 200,
      body: { items: [] },
    },
    [ACTIVE_CYCLES_URL]: { status: 200, body: page(cycles) },
    [VERSION_URL]: { status: 200, body: VERSION },
    [RECIPE_URL]: { status: 200, body: RECIPE },
  };
}
