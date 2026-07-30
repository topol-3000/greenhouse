/**
 * The Basil Growbox dashboard.
 *
 * This file is the whole client. It has no framework, no build step and no
 * dependency: it reads the same public endpoints any other client would use,
 * under the same origin that served the page, and writes what it read into the
 * markup already in index.html.
 *
 * Three rules shape it:
 *
 * 1. The server owns the truth. Nothing is remembered across a reload, the demo
 *    growbox is found by its stable codes rather than by an identifier compiled
 *    into the page, and every rendered value comes from a persisted resource.
 *    Recent activity is composed here from the run, its commands and the latest
 *    samples; it is a view of those three resources and not a fourth one.
 * 2. A failed read is a banner, not a blank page. The last values that were
 *    valid stay on screen, and the reader is offered a retry instead of an
 *    exception.
 * 3. One cycle at a time. A refresh is scheduled only once the previous one has
 *    finished, and only while the run is actually active, so polling cannot
 *    overlap itself and stops on its own when the run reaches a terminal state.
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

/**
 * The documented parameters of the demonstration run.
 *
 * The model version is not here: it is server-owned, and the create request has
 * no field for it.
 */
const DEMO_RUN = {
  speed_multiplier: 600,
  initial_temperature: 22.0,
  initial_humidity: 65.0,
  ambient_temperature: 30.0,
  ambient_humidity: 50.0,
};

/** Samples plotted by the chart. One minute of ticks at the demo's one-per-second rate. */
const HISTORY_LIMIT = 60;

/** Commands read for the activity list. Well above what one demo run produces. */
const COMMAND_LIMIT = 20;

/** How many of the newest samples reach the activity list, and how long it gets. */
const ACTIVITY_SAMPLE_LIMIT = 5;
const ACTIVITY_LIMIT = 12;

/** Gap between refreshes while a run is active. */
const POLL_INTERVAL_MS = 1000;

/** Fixed viewBox of the inline SVG, in its own user units. */
const CHART = { width: 320, height: 120, padding: 8 };

const dashboard = document.querySelector(".dashboard");
const errorRegion = document.querySelector('[data-region="error"]');
const startButton = document.querySelector('[data-action="start"]');
const stopButton = document.querySelector('[data-action="stop"]');

/**
 * Cached result of the topology discovery.
 *
 * The growbox is found once and then reused: the codes resolve to the same
 * resources on every read, and re-listing the topology once a second would ask
 * four questions whose answers cannot have changed.
 */
let growbox = null;

/** The run the last successful read saw, which is what Stop addresses. */
let latestRun = null;

/**
 * Tail of the serialised cycle queue, and how much is waiting on it.
 *
 * Every read and every action goes through it, so two cycles never overlap. A
 * queue rather than a rejected-while-busy flag, because dropping a Stop that
 * happened to land during a poll would look to the reader like a button that
 * does nothing.
 */
let queue = Promise.resolve();
let queued = 0;

/** Whether Start or Stop is waiting for its response. */
let actionInFlight = false;

/** Handle of the pending scheduled refresh, when one is pending. */
let pollTimer = null;

/** A read or action that failed for a reason the reader can be told about safely. */
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
 * Send one request to the API and decode its body.
 *
 * @param {string} path Path under the API root, starting with a slash.
 * @param {object} [options] Method and JSON body, when they are not a plain GET.
 * @returns {Promise<object>} The decoded body.
 * @throws {DashboardError} If the request fails or the response is not success.
 *     The message names the status and never the response body: a failure is
 *     reported to the reader, and internal detail is not part of the report.
 */
async function request(path, options = {}) {
  const method = options.method ?? "GET";
  const init = {
    method,
    headers: { Accept: "application/json" },
    cache: "no-store",
  };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(`${API}${path}`, init);
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
  const page = await request(path);
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
  const page = await request(`/points?facility_id=${facility.id}`);
  const points = {};
  for (const point of page.items) {
    points[point.code] = point;
  }
  growbox = { site, facility, controlZone, points, controlLoop: null };
  return growbox;
}

/**
 * Resolve the zone's control loop, which the command history is addressed by.
 *
 * Unlike the topology, this is re-read until it is found: a loop configured
 * after the page was opened has to become visible without a reload.
 *
 * @param {object} context The discovered growbox.
 * @returns {Promise<object|null>} The loop, or `null` while the zone has none.
 */
async function resolveControlLoop(context) {
  if (context.controlLoop !== null) {
    return context.controlLoop;
  }
  const page = await request(`/control-loops?control_zone_id=${context.controlZone.id}`);
  context.controlLoop = page.items.length > 0 ? page.items[0] : null;
  return context.controlLoop;
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
  return request(`/points/${point.id}/state`);
}

/**
 * Read everything one rendering needs.
 *
 * @param {object} context The discovered growbox.
 * @returns {Promise<object>} The snapshot the renderer consumes.
 */
async function readSnapshot(context) {
  const temperaturePoint = context.points[POINT_CODES.temperature];
  const controlLoop = await resolveControlLoop(context);
  const [temperature, humidity, fanRunning] = await Promise.all([
    readState(temperaturePoint),
    readState(context.points[POINT_CODES.humidity]),
    readState(context.points[POINT_CODES.fanRunning]),
  ]);
  const history =
    temperaturePoint === undefined
      ? { items: [] }
      : await request(`/points/${temperaturePoint.id}/telemetry?limit=${HISTORY_LIMIT}`);
  const runs = await request(
    `/simulation-runs?control_zone_id=${context.controlZone.id}&limit=1`,
  );
  const commands =
    controlLoop === null
      ? { items: [] }
      : await request(`/commands?control_loop_id=${controlLoop.id}&limit=${COMMAND_LIMIT}`);
  return {
    facility: context.facility,
    points: context.points,
    temperature,
    humidity,
    fanRunning,
    history: history.items,
    commands: commands.items,
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
 * The value comes from the persisted `fan_running` projection, never from what
 * the page last asked for: the fan is switched by the control loop, and an
 * optimistic local guess would show a state nothing had reported. The dashboard
 * offers no way to change it either — manual override is out of scope.
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
 * Compose the recent-activity list from the three resources that record it.
 *
 * Samples are placed by `received_at` rather than by `observed_at`. A run at
 * speed 600 observes half an hour of virtual time per real second, so ordering
 * by the measured instant would push every reading above the lifecycle and
 * command entries it actually happened between.
 *
 * A command exists only where the fan changed state, so evaluations that
 * changed nothing are absent here because they are absent from the record.
 *
 * @param {object} snapshot The values read from the API.
 * @returns {Array<{at: string, text: string}>} Newest-first activity entries.
 */
function buildActivity(snapshot) {
  const entries = [];
  const run = snapshot.run;
  if (run !== null) {
    entries.push({ at: run.created_at, text: "Simulation run created" });
    if (run.started_at !== null) {
      entries.push({ at: run.started_at, text: "Simulation started" });
    }
    if (run.stopped_at !== null) {
      entries.push({
        at: run.stopped_at,
        text:
          run.status === "failed"
            ? `Simulation failed — ${run.failure_reason ?? "reason not recorded"}`
            : "Simulation stopped",
      });
    }
  }
  for (const command of snapshot.commands) {
    entries.push({
      at: command.executed_at,
      text: command.desired_value === true ? "Fan switched on" : "Fan switched off",
    });
  }
  const unit = snapshot.points[POINT_CODES.temperature]?.unit ?? "";
  for (const sample of snapshot.history.slice(0, ACTIVITY_SAMPLE_LIMIT)) {
    entries.push({
      at: sample.received_at,
      text: `Temperature ${Number(sample.value).toFixed(1)}${unit ? ` ${unit}` : ""}`,
    });
  }
  return entries
    .sort((first, second) => Date.parse(second.at) - Date.parse(first.at))
    .slice(0, ACTIVITY_LIMIT);
}

/**
 * Write the activity entries into the list.
 *
 * @param {Array<{at: string, text: string}>} entries Newest-first entries.
 */
function renderActivity(entries) {
  const list = field("activity");
  list.replaceChildren();
  if (entries.length === 0) {
    const empty = document.createElement("li");
    empty.className = "activity__empty";
    empty.textContent = "Nothing has happened yet.";
    list.append(empty);
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("li");
    const time = document.createElement("span");
    time.className = "activity__time";
    time.textContent = new Date(entry.at).toLocaleTimeString();
    const text = document.createElement("span");
    text.textContent = entry.text;
    item.append(time, text);
    list.append(item);
  }
}

/**
 * Enable exactly the actions the current state allows.
 *
 * @param {object|null} run The latest persisted run.
 */
function updateControls(run) {
  const status = run === null ? "not_started" : run.status;
  startButton.disabled = actionInFlight || status === "running";
  stopButton.disabled = actionInFlight || status !== "running";
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

  renderActivity(buildActivity(snapshot));
  updateControls(snapshot.run);
}

/**
 * Show a failed read or action without discarding what is already on screen.
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
 * Show what the page is currently waiting for, or nothing.
 *
 * @param {string} text The hint to display.
 */
function setHint(text) {
  field("action-hint").textContent = text;
}

/** Cancel the pending refresh, if one is pending. */
function clearPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

/**
 * Run one cycle after everything already queued has finished.
 *
 * @param {() => Promise<void>} task The cycle to run.
 * @returns {Promise<void>} Resolved once it has finished, however it ended.
 */
function enqueue(task) {
  queued += 1;
  queue = queue
    .then(task)
    .catch(() => {})
    .finally(() => {
      queued -= 1;
    });
  return queue;
}

/**
 * Schedule the next refresh.
 *
 * Scheduled after the previous cycle has finished rather than on an interval, so
 * a slow read delays the next one instead of overlapping with it. A tick that
 * finds work already queued waits another interval instead of piling on: that
 * work will render, and schedule the poll after it, on its own.
 */
function schedulePoll() {
  clearPoll();
  pollTimer = setTimeout(() => {
    pollTimer = null;
    if (queued > 0) {
      schedulePoll();
      return;
    }
    void refresh();
  }, POLL_INTERVAL_MS);
}

/**
 * Read the growbox once, render what came back, and keep polling while it runs.
 *
 * @returns {Promise<void>}
 */
async function readAndRender() {
  clearPoll();
  try {
    const snapshot = await readSnapshot(await discover());
    latestRun = snapshot.run;
    render(snapshot);
    clearError();
    if (snapshot.run !== null && snapshot.run.status === "running") {
      schedulePoll();
    }
  } catch (error) {
    // Discovery is retried from scratch: a growbox that did not exist yet may
    // exist by the time the next cycle runs.
    growbox = null;
    showError(error);
    // A failed read of a run that was running is transient until proven
    // otherwise, so the page keeps trying instead of freezing until Retry.
    if (latestRun !== null && latestRun.status === "running") {
      schedulePoll();
    }
  } finally {
    dashboard.dataset.phase = "ready";
  }
}

/**
 * Read the growbox as soon as the queue allows.
 *
 * @returns {Promise<void>}
 */
async function refresh() {
  return enqueue(readAndRender);
}

/**
 * Run one user action, then re-read the state it produced.
 *
 * The flag is what makes a second click a no-op rather than a second request;
 * the buttons are disabled for the duration as well, and neither is enough on
 * its own. The action itself waits for any read already in flight instead of
 * being dropped, and the poll is cancelled first so the reader is not shown a
 * frame from before their action.
 *
 * @param {string} hint What to tell the reader while the request is in flight.
 * @param {() => Promise<void>} action The requests to send.
 * @returns {Promise<void>}
 */
async function act(hint, action) {
  if (actionInFlight) {
    return;
  }
  actionInFlight = true;
  clearPoll();
  setHint(hint);
  updateControls(latestRun);
  await enqueue(async () => {
    try {
      await action();
      clearError();
    } catch (error) {
      showError(error);
    }
  });
  actionInFlight = false;
  setHint("");
  await refresh();
}

/**
 * Create and start one demonstration run.
 *
 * A terminal run is left exactly as it is: this creates a new run rather than
 * reviving the previous one, which is why the history of an earlier run survives
 * pressing Start again.
 *
 * @returns {Promise<void>}
 */
async function start() {
  await act("Starting…", async () => {
    const context = await discover();
    const created = await request("/simulation-runs", {
      method: "POST",
      body: { control_zone_id: context.controlZone.id, ...DEMO_RUN },
    });
    await request(`/simulation-runs/${created.id}/start`, { method: "POST" });
  });
}

/**
 * Stop the run the last read saw running.
 *
 * @returns {Promise<void>}
 */
async function stop() {
  const run = latestRun;
  if (run === null) {
    return;
  }
  await act("Stopping…", async () => {
    await request(`/simulation-runs/${run.id}/stop`, { method: "POST" });
  });
}

startButton.addEventListener("click", () => void start());
stopButton.addEventListener("click", () => void stop());
document.querySelector('[data-action="retry"]').addEventListener("click", () => {
  void refresh();
});

void refresh();
