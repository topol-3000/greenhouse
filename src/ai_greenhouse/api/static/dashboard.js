/**
 * The Basil Growbox dashboard.
 *
 * This file is the whole client. It has no framework, no build step and no
 * dependency: it reads the same public endpoints any other client would use,
 * under the same origin that served the page, and writes what it read into the
 * markup already in index.html.
 *
 * Two rules shape it:
 *
 * 1. The server owns the truth. Nothing is remembered across a reload, the demo
 *    growbox is found by its stable codes rather than by an identifier compiled
 *    into the page, and every rendered value comes from a persisted resource.
 * 2. A failed read is a banner, not a blank page. The last values that were
 *    valid stay on screen, and the reader is offered a retry instead of an
 *    exception.
 */

const API = "/api/v1";

const DEMO_CODES = {
  site: "home",
  facility: "basil-growbox",
  controlZone: "main-climate",
};

const POINT_CODES = {
  temperature: "air_temperature",
  humidity: "air_humidity",
  fanRunning: "fan_running",
};

const RUN_STATUS_LABELS = {
  created: "Created",
  running: "Running",
  stopped: "Stopped",
  failed: "Failed",
};

const NOT_STARTED = "Not started";
const NO_DATA = "No data";

/** Samples plotted by the chart. One minute of ticks at the demo's one-per-second rate. */
const HISTORY_LIMIT = 60;

/** Fixed viewBox of the inline SVG, in its own user units. */
const CHART = { width: 320, height: 120, padding: 8 };

const dashboard = document.querySelector(".dashboard");
const errorRegion = document.querySelector('[data-region="error"]');

/**
 * Cached result of the topology discovery.
 *
 * The growbox is found once and then reused: the codes resolve to the same
 * resources on every read, and re-listing the topology once a second would ask
 * four questions whose answers cannot have changed.
 */
let growbox = null;

/** A read that failed for a reason the reader can be told about safely. */
class DashboardError extends Error {}

/**
 * Return the element carrying one named field.
 *
 * @param {string} name Value of the element's `data-field` attribute.
 * @returns {Element} The matching element.
 */
function field(name) {
  return document.querySelector(`[data-field="${name}"]`);
}

/**
 * Read one JSON resource of the API.
 *
 * @param {string} path Path under the API root, starting with a slash.
 * @returns {Promise<object>} The decoded body.
 * @throws {DashboardError} If the request fails or the response is not success.
 *     The message names the status and never the response body: a failure is
 *     reported to the reader, and internal detail is not part of the report.
 */
async function getJson(path) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    throw new DashboardError("The API could not be reached.");
  }
  if (!response.ok) {
    throw new DashboardError(`The API answered with HTTP ${response.status}.`);
  }
  return response.json();
}

/**
 * Find one resource of a collection by its stable code.
 *
 * @param {string} path Collection path under the API root.
 * @param {string} code Stable code to look for.
 * @param {string} label Human-readable name used if nothing matches.
 * @returns {Promise<object>} The matching resource.
 * @throws {DashboardError} If the collection holds no resource with that code.
 */
async function findByCode(path, code, label) {
  const page = await getJson(path);
  const match = page.items.find((item) => item.code === code);
  if (match === undefined) {
    throw new DashboardError(`The demo ${label} "${code}" does not exist yet.`);
  }
  return match;
}

/**
 * Resolve the demo growbox and the points the dashboard reads.
 *
 * @returns {Promise<object>} The facility, its climate zone and its points by code.
 * @throws {DashboardError} If any part of the demo topology is missing.
 */
async function discover() {
  if (growbox !== null) {
    return growbox;
  }
  const site = await findByCode("/sites", DEMO_CODES.site, "site");
  const facility = await findByCode(
    `/facilities?site_id=${site.id}`,
    DEMO_CODES.facility,
    "facility",
  );
  const controlZone = await findByCode(
    `/control-zones?facility_id=${facility.id}`,
    DEMO_CODES.controlZone,
    "control zone",
  );
  const page = await getJson(`/points?facility_id=${facility.id}`);
  const points = {};
  for (const point of page.items) {
    points[point.code] = point;
  }
  growbox = { site, facility, controlZone, points };
  return growbox;
}

/**
 * Read the current state of one point, or nothing when it is not configured.
 *
 * @param {object|undefined} point The point to read.
 * @returns {Promise<object|null>} The state projection, or `null`.
 */
async function readState(point) {
  if (point === undefined) {
    return null;
  }
  return getJson(`/points/${point.id}/state`);
}

/**
 * Read everything one rendering needs.
 *
 * @param {object} context The discovered growbox.
 * @returns {Promise<object>} The snapshot the renderer consumes.
 */
async function readSnapshot(context) {
  const temperaturePoint = context.points[POINT_CODES.temperature];
  const [temperature, humidity, fanRunning] = await Promise.all([
    readState(temperaturePoint),
    readState(context.points[POINT_CODES.humidity]),
    readState(context.points[POINT_CODES.fanRunning]),
  ]);
  const history =
    temperaturePoint === undefined
      ? { items: [] }
      : await getJson(`/points/${temperaturePoint.id}/telemetry?limit=${HISTORY_LIMIT}`);
  const runs = await getJson(
    `/simulation-runs?control_zone_id=${context.controlZone.id}&limit=1`,
  );
  return {
    facility: context.facility,
    points: context.points,
    temperature,
    humidity,
    fanRunning,
    history: history.items,
    run: runs.items.length > 0 ? runs.items[0] : null,
  };
}

/**
 * Format one measured value with the unit its point is measured in.
 *
 * @param {object|null} state The point's current state.
 * @param {object|undefined} point The point itself, which owns the unit.
 * @returns {string} The value and its unit, or the no-data text.
 */
function formatReading(state, point) {
  if (state === null || state.value === null || state.value === undefined) {
    return NO_DATA;
  }
  const unit = point?.unit ? ` ${point.unit}` : "";
  return `${Number(state.value).toFixed(1)}${unit}`;
}

/**
 * Format the logical fan state.
 *
 * The value comes from the persisted `fan_running` projection. The dashboard
 * offers no way to change it: the fan is switched by the control loop, and a
 * manual override is out of scope for this milestone.
 *
 * @param {object|null} state The `fan_running` current state.
 * @returns {string} `On`, `Off` or the no-data text.
 */
function formatFan(state) {
  if (state === null || state.value === null || state.value === undefined) {
    return NO_DATA;
  }
  return state.value === true ? "On" : "Off";
}

/**
 * Turn newest-first samples into a chronological polyline.
 *
 * The history endpoint answers newest first, which is the order a reader of a
 * list wants and the reverse of the order a line is drawn in.
 *
 * @param {Array<object>} samples Samples as the API returned them.
 * @returns {{points: string, summary: string}} The polyline and its caption.
 */
function chartGeometry(samples) {
  const values = samples
    .map((sample) => Number(sample.value))
    .filter((value) => Number.isFinite(value))
    .reverse();
  if (values.length === 0) {
    return { points: "", summary: "No temperature history yet." };
  }

  const lowest = Math.min(...values);
  const highest = Math.max(...values);
  const span = highest - lowest;
  const usableWidth = CHART.width - 2 * CHART.padding;
  const usableHeight = CHART.height - 2 * CHART.padding;
  // A single sample is drawn as a flat line rather than as an invisible vertex.
  const columns = values.length === 1 ? [values[0], values[0]] : values;
  const step = usableWidth / (columns.length - 1);
  const points = columns
    .map((value, index) => {
      const x = CHART.padding + index * step;
      const y =
        span === 0
          ? CHART.height / 2
          : CHART.padding + usableHeight * (1 - (value - lowest) / span);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const range =
    span === 0
      ? `${lowest.toFixed(1)} °C`
      : `${lowest.toFixed(1)}–${highest.toFixed(1)} °C`;
  const noun = values.length === 1 ? "sample" : "samples";
  return { points, summary: `${values.length} ${noun}, ${range}` };
}

/**
 * Return the label and machine name of a run's lifecycle state.
 *
 * @param {object|null} run The zone's latest persisted run.
 * @returns {{label: string, status: string, reason: string|null}} What to show.
 */
function formatRun(run) {
  if (run === null) {
    return { label: NOT_STARTED, status: "not_started", reason: null };
  }
  return {
    label: RUN_STATUS_LABELS[run.status] ?? run.status,
    status: run.status,
    reason: run.status === "failed" ? run.failure_reason : null,
  };
}

/**
 * Write one snapshot into the page.
 *
 * @param {object} snapshot The values read from the API.
 */
function render(snapshot) {
  field("facility-name").textContent = snapshot.facility.name;

  const temperature = field("temperature");
  temperature.textContent = formatReading(
    snapshot.temperature,
    snapshot.points[POINT_CODES.temperature],
  );
  temperature.dataset.quality = snapshot.temperature?.quality ?? "no_data";

  const humidity = field("humidity");
  humidity.textContent = formatReading(
    snapshot.humidity,
    snapshot.points[POINT_CODES.humidity],
  );
  humidity.dataset.quality = snapshot.humidity?.quality ?? "no_data";

  const fan = field("fan");
  fan.textContent = formatFan(snapshot.fanRunning);
  fan.dataset.quality = snapshot.fanRunning?.quality ?? "no_data";

  const run = formatRun(snapshot.run);
  const badge = field("run-status");
  badge.textContent = run.reason === null ? run.label : `${run.label} — ${run.reason}`;
  badge.dataset.status = run.status;

  const geometry = chartGeometry(snapshot.history);
  field("chart-line").setAttribute("points", geometry.points);
  field("chart-summary").textContent = geometry.summary;
}

/**
 * Show a failed read without discarding what is already on screen.
 *
 * @param {unknown} error The failure to report.
 */
function showError(error) {
  const message =
    error instanceof DashboardError ? error.message : "The growbox could not be read.";
  field("error-message").textContent = message;
  errorRegion.hidden = false;
}

/** Hide the error banner after a read that succeeded. */
function clearError() {
  errorRegion.hidden = true;
}

/**
 * Read the growbox once and render what came back.
 *
 * @returns {Promise<void>}
 */
async function refresh() {
  try {
    render(await readSnapshot(await discover()));
    clearError();
  } catch (error) {
    // Discovery is retried from scratch: a growbox that did not exist yet may
    // exist by the time the reader presses Retry.
    growbox = null;
    showError(error);
  } finally {
    dashboard.dataset.phase = "ready";
  }
}

document.querySelector('[data-action="retry"]').addEventListener("click", () => {
  void refresh();
});

void refresh();
