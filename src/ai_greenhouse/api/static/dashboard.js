/**
 * The AI Greenhouse facility dashboard.
 *
 * This file is the whole client. It has no framework, no build step and no
 * dependency: it reads the same public endpoints any other client would use,
 * under the same origin that served the page, and writes what it read into the
 * markup already in index.html.
 *
 * Four rules shape it:
 *
 * 1. The server owns the truth. Nothing is remembered across a reload and every
 *    rendered value comes from a persisted resource. Recent activity is composed
 *    here from the applied commands and the latest samples; it is a view of those
 *    two resources and not a third one.
 * 2. The page is producer-independent. It knows nothing about who wrote the
 *    telemetry it renders — an external gateway, a virtual installation or real
 *    equipment — and it starts, stops and controls nothing.
 * 3. A failed read is a banner, not a blank page. The last values that were valid
 *    stay on screen, and the reader is offered a retry instead of an exception.
 * 4. One cycle at a time, forever. A refresh is scheduled only once the previous
 *    one has finished, so polling cannot overlap itself, and it keeps going
 *    whatever the state of the data: there is no lifecycle signal to gate it on.
 */

const API = "/api/v1";

/**
 * The point codes one climate reading each is rendered from.
 *
 * These are the shared metric codes the domain documents, not the identity of a
 * particular facility: a point a facility does not define is simply not shown.
 */
const POINT_CODES = {
  temperature: "air_temperature",
  humidity: "air_humidity",
  fanRunning: "fan_running",
};

const NO_DATA = "No data";

/**
 * What a command's delivery state adds to its activity entry.
 *
 * An applied command needs no suffix — the fan really changed — while a command
 * still on its way to a gateway, or one the gateway refused, is a different
 * fact and says so.
 */
const COMMAND_STATE_SUFFIXES = {
  applied: "",
  pending: " — pending delivery",
  rejected: " — rejected by the gateway",
};

/** Samples plotted by the chart. */
const HISTORY_LIMIT = 60;

/** Commands read for the activity list. */
const COMMAND_LIMIT = 20;

/** How many of the newest samples reach the activity list, and how long it gets. */
const ACTIVITY_SAMPLE_LIMIT = 5;
const ACTIVITY_LIMIT = 12;

/**
 * Gap between refreshes.
 *
 * A bounded periodic poll of the read APIs, and deliberately nothing more: no
 * WebSocket, no SSE and no subscription. Five seconds is slow enough to be
 * unremarkable for the API and fast enough that an external producer's
 * measurement appears while the reader is still looking at the page.
 */
const POLL_INTERVAL_MS = 5000;

/** Fixed viewBox of the inline SVG, in its own user units. */
const CHART = { width: 320, height: 120, padding: 8 };

const dashboard = document.querySelector(".dashboard");
const errorRegion = document.querySelector('[data-region="error"]');
const emptyRegion = document.querySelector('[data-region="empty"]');

/**
 * The facility the last successful cycle resolved, while there is one.
 *
 * Only the identity is cached: which facility this page shows does not change
 * while it is open, but what is configured inside it can, because an external
 * client is free to provision the rest of the topology after the page was
 * opened. It is dropped again whenever a cycle fails, so a cloud that was empty
 * a moment ago is looked at again on the next refresh without a reload.
 */
let facility = null;

/**
 * Tail of the serialised cycle queue, and how much is waiting on it.
 *
 * Every read goes through it, so two cycles never overlap.
 */
let queue = Promise.resolve();
let queued = 0;

/** Handle of the pending scheduled refresh, when one is pending. */
let pollTimer = null;

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
 * Read one resource from the API and decode its body.
 *
 * Every request this page makes is a `GET`: the dashboard is a read model and
 * writes nothing back.
 *
 * @param {string} path Path under the API root, starting with a slash.
 * @returns {Promise<object>} The decoded body.
 * @throws {DashboardError} If the request fails or the response is not success.
 *     The message names the status and never the response body: a failure is
 *     reported to the reader, and internal detail is not part of the report.
 */
async function read(path) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method: "GET",
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
 * Return the first member of a collection, or `null` when it is empty.
 *
 * The page shows one facility, and which one is a question the cloud answers by
 * what is configured in it. No code is compiled into the page, so any facility a
 * client provisions — Basil Growbox or anything else — is rendered the same way.
 *
 * @param {string} path Collection path under the API root.
 * @returns {Promise<object|null>} The first item, or `null`.
 */
async function firstOf(path) {
  const page = await read(path);
  return page.items.length > 0 ? page.items[0] : null;
}

/**
 * Resolve the facility the page renders, or nothing while none is configured.
 *
 * @returns {Promise<object|null>} The facility, or `null` while the cloud holds
 *     no configured facility yet.
 */
async function discoverFacility() {
  if (facility !== null) {
    return facility;
  }
  const site = await firstOf("/sites");
  if (site === null) {
    return null;
  }
  facility = await firstOf(`/facilities?site_id=${site.id}`);
  return facility;
}

/**
 * Resolve what is currently configured inside the facility.
 *
 * Re-read on every cycle rather than cached: a point, a zone or a loop
 * provisioned after the page was opened has to become visible without a reload,
 * and the page cannot know when its producer finished provisioning.
 *
 * @param {object} found The facility this page renders.
 * @returns {Promise<object>} The facility, its points by code and its loop.
 */
async function readConfiguration(found) {
  const page = await read(`/points?facility_id=${found.id}`);
  const points = {};
  for (const point of page.items) {
    points[point.code] = point;
  }
  const controlZone = await firstOf(`/control-zones?facility_id=${found.id}`);
  const controlLoop =
    controlZone === null
      ? null
      : await firstOf(`/control-loops?control_zone_id=${controlZone.id}`);
  return { facility: found, points, controlLoop };
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
  return read(`/points/${point.id}/state`);
}

/**
 * Read everything one rendering needs.
 *
 * @param {object} context The facility and its current configuration.
 * @returns {Promise<object>} The snapshot the renderer consumes.
 */
async function readSnapshot(context) {
  const temperaturePoint = context.points[POINT_CODES.temperature];
  const controlLoop = context.controlLoop;
  const [temperature, humidity, fanRunning] = await Promise.all([
    readState(temperaturePoint),
    readState(context.points[POINT_CODES.humidity]),
    readState(context.points[POINT_CODES.fanRunning]),
  ]);
  const history =
    temperaturePoint === undefined
      ? { items: [] }
      : await read(`/points/${temperaturePoint.id}/telemetry?limit=${HISTORY_LIMIT}`);
  const commands =
    controlLoop === null
      ? { items: [] }
      : await read(`/commands?control_loop_id=${controlLoop.id}&limit=${COMMAND_LIMIT}`);
  return {
    facility: context.facility,
    points: context.points,
    temperature,
    humidity,
    fanRunning,
    history: history.items,
    commands: commands.items,
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
 * The value comes from the persisted `fan_running` projection reported by
 * whatever is behind the gateway. The fan is switched by the control loop, and
 * the dashboard offers no way to change it — manual override is out of scope.
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
 * Return the instant one command is placed in the activity list by.
 *
 * A command is timed by what actually happened to it. `executed_at` is written
 * by an in-process actuator and stays empty for a command an external gateway
 * applied, so the acknowledgement is what dates those; a command nobody has
 * answered yet is dated by the decision that issued it, which is the only
 * instant it has.
 *
 * @param {object} command The command as the API returned it.
 * @returns {string} An ISO-8601 instant.
 */
function commandInstant(command) {
  return command.executed_at ?? command.acknowledged_at ?? command.issued_at;
}

/**
 * Compose the recent-activity list from the two resources that record it.
 *
 * Samples are placed by `received_at` rather than by `observed_at`: a producer
 * states its own observation time, and a batch that arrived together would
 * otherwise be scattered around the commands it actually happened between.
 *
 * A command exists only where the loop decided the fan should change, so
 * evaluations that decided nothing are absent here because they are absent from
 * the record.
 *
 * @param {object} snapshot The values read from the API.
 * @returns {Array<{at: string, text: string}>} Newest-first activity entries.
 */
function buildActivity(snapshot) {
  const entries = [];
  for (const command of snapshot.commands) {
    const action = command.desired_value === true ? "Fan switched on" : "Fan switched off";
    entries.push({
      at: commandInstant(command),
      text: `${action}${COMMAND_STATE_SUFFIXES[command.state] ?? ` — ${command.state}`}`,
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

  const geometry = chartGeometry(snapshot.history);
  field("chart-line").setAttribute("points", geometry.points);
  field("chart-summary").textContent = geometry.summary;

  renderActivity(buildActivity(snapshot));
}

/**
 * Show that the cloud holds nothing to render yet.
 *
 * An empty cloud is a normal state and not a failure: the page says so plainly
 * and keeps refreshing, because the facility it is waiting for is provisioned by
 * a client it knows nothing about.
 */
function showEmpty() {
  field("facility-name").textContent = "No facility configured";
  emptyRegion.hidden = false;
}

/** Hide the empty-cloud notice once a facility exists. */
function clearEmpty() {
  emptyRegion.hidden = true;
}

/**
 * Show a failed read without discarding what is already on screen.
 *
 * @param {unknown} error The failure to report.
 */
function showError(error) {
  const message =
    error instanceof DashboardError ? error.message : "The facility could not be read.";
  field("error-message").textContent = message;
  errorRegion.hidden = false;
}

/** Hide the error banner after a read that succeeded. */
function clearError() {
  errorRegion.hidden = true;
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
 * Read the facility once and render what came back.
 *
 * The next refresh is scheduled however this one ended. There is no producer
 * state to consult and no run to be finished, so the only reason to stop
 * refreshing would be a signal the cloud does not have.
 *
 * @returns {Promise<void>}
 */
async function readAndRender() {
  clearPoll();
  try {
    const found = await discoverFacility();
    if (found === null) {
      showEmpty();
    } else {
      render(await readSnapshot(await readConfiguration(found)));
      clearEmpty();
    }
    clearError();
  } catch (error) {
    // Discovery is retried from scratch: what could not be read now may be
    // readable by the time the next cycle runs.
    facility = null;
    showError(error);
  } finally {
    dashboard.dataset.phase = "ready";
    schedulePoll();
  }
}

/**
 * Read the facility as soon as the queue allows.
 *
 * @returns {Promise<void>}
 */
async function refresh() {
  return enqueue(readAndRender);
}

document.querySelector('[data-action="retry"]').addEventListener("click", () => {
  void refresh();
});

void refresh();
