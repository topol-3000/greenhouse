/**
 * The public API responses the dashboard tests answer with.
 *
 * These are exactly the bodies the read endpoints the page uses return — the
 * same shapes `tests/integration` asserts against a real database, written out
 * here so the client can be exercised without one.
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
export const LOOP_ID = "44444444-4444-4444-8444-444444444444";

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

/**
 * Build the routing table of a facility that has measured something.
 *
 * Every entry is a resource the page actually reads. A request to anything else
 * is left unrouted, which `render` fails on — so a section the page should no
 * longer have cannot come back unnoticed.
 *
 * @returns {Record<string, object>} Answers by request URL.
 */
export function routes() {
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
  };
}
