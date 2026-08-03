/**
 * The smallest thing that can run `dashboard.js` outside a browser.
 *
 * It is not a DOM implementation and does not try to be one. The page uses four
 * selector shapes, seven element operations and one global each for fetching and
 * for scheduling, so that is what is here — about a hundred lines of stub, no
 * package.json, no lockfile, no jsdom and no test framework beyond the `node:`
 * modules Node already ships.
 *
 * Two properties make it worth having:
 *
 * 1. The elements come from `index.html` itself. A field the script writes to
 *    but the markup does not carry fails here rather than in a browser, because
 *    `querySelector` answers only what the delivered page really declares.
 * 2. The script under test is the delivered file, unmodified and unbundled. What
 *    these tests exercise is what the API serves.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";

const STATIC_DIRECTORY = new URL("../../src/ai_greenhouse/api/static/", import.meta.url);

export const INDEX_HTML = new URL("index.html", STATIC_DIRECTORY);
export const DASHBOARD_JS = new URL("dashboard.js", STATIC_DIRECTORY);

/** Root the page reads every resource under. */
export const API = "/api/v1";

/** One element of the stub document. */
class StubElement {
  /**
   * @param {string} tagName Lowercase tag name.
   * @param {Record<string, string>} attributes Attributes as written in the markup.
   */
  constructor(tagName, attributes = {}) {
    this.tagName = tagName;
    this.className = attributes.class ?? "";
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.children = [];
    this.hidden = "hidden" in attributes;
    this.text = "";
    for (const [name, value] of Object.entries(attributes)) {
      if (name.startsWith("data-")) {
        this.dataset[dataKey(name)] = value;
      } else {
        this.attributes[name] = value;
      }
    }
  }

  /** @returns {string} The element's text, including that of its children. */
  get textContent() {
    return this.children.length > 0
      ? this.children.map((child) => child.textContent).join("")
      : this.text;
  }

  /** @param {unknown} value Replacement text, which drops any children. */
  set textContent(value) {
    this.children = [];
    this.text = String(value);
  }

  /** @param {...StubElement} nodes Nodes to append. */
  append(...nodes) {
    this.children.push(...nodes);
  }

  /** @param {...StubElement} nodes Nodes replacing every current child. */
  replaceChildren(...nodes) {
    this.children = nodes;
    this.text = "";
  }

  /**
   * @param {string} name Attribute name.
   * @param {unknown} value Attribute value.
   */
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  /**
   * @param {string} name Attribute name.
   * @returns {string|null} The value, or `null`.
   */
  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  /**
   * @param {string} type Event name.
   * @param {Function} handler Listener to register.
   */
  addEventListener(type, handler) {
    (this.listeners[type] ??= []).push(handler);
  }

  /** Invoke every registered click listener, as a reader pressing it would. */
  click() {
    for (const handler of this.listeners.click ?? []) {
      handler();
    }
  }
}

/**
 * Turn `data-foo-bar` into the `dataset` key `fooBar`.
 *
 * @param {string} name The attribute name.
 * @returns {string} Its dataset key.
 */
function dataKey(name) {
  return name
    .slice("data-".length)
    .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

/**
 * Build the stub document from the markup the API serves.
 *
 * Elements are flat: the page selects by attribute and never by structure, so
 * the tree is not needed and pretending to have one would be a second thing to
 * keep true.
 *
 * @returns {object} A document exposing `querySelector` and `createElement`.
 */
function createDocument() {
  const markup = readFileSync(INDEX_HTML, "utf8");
  const elements = [];
  for (const [, tagName, rawAttributes] of markup.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/g)) {
    const attributes = {};
    for (const [, name, quoted] of rawAttributes.matchAll(/([a-zA-Z-]+)(?:="([^"]*)")?/g)) {
      attributes[name] = quoted ?? "";
    }
    elements.push(new StubElement(tagName, attributes));
  }

  /**
   * Resolve the four selector shapes the page uses.
   *
   * @param {string} selector A class selector or an attribute-equals selector.
   * @returns {StubElement|null} The first match.
   */
  function querySelector(selector) {
    const attribute = selector.match(/^\[([a-z-]+)="([^"]*)"\]$/);
    if (attribute !== null) {
      const [, name, value] = attribute;
      return elements.find((element) => element.dataset[dataKey(name)] === value) ?? null;
    }
    const className = selector.match(/^\.([\w-]+)$/);
    assert.ok(className !== null, `unsupported selector ${selector}`);
    return (
      elements.find((element) => element.className.split(/\s+/).includes(className[1])) ?? null
    );
  }

  return {
    querySelector,
    createElement: (tagName) => new StubElement(tagName),
  };
}

/**
 * Build a promise a test resolves when it decides the response has arrived.
 *
 * @returns {{promise: Promise<unknown>, resolve: Function}} The pair.
 */
export function deferred() {
  let resolve;
  const promise = new Promise((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/**
 * Answer with a decoded body, as a successful read.
 *
 * @param {object} body The JSON body.
 * @returns {object} A route answer.
 */
export function ok(body) {
  return { status: 200, body };
}

/**
 * Answer with a failing status and no usable body.
 *
 * @param {number} status The HTTP status to answer with.
 * @returns {object} A route answer.
 */
export function fails(status = 500) {
  return { status, body: null };
}

/**
 * Run one fresh instance of the dashboard against a routing table.
 *
 * The script is evaluated in its own context rather than imported, for two
 * reasons: a module is evaluated once per process and every scenario needs a
 * page that has just been opened, and the globals it is given stay inside that
 * context instead of being installed over Node's own.
 *
 * @param {Record<string, unknown>} routes Answers by full request URL. A value
 *     may be a route answer, a promise of one, or a function returning either.
 * @returns {object} The loaded page: its document, the URLs it read, and the
 *     poll the refresh cycle scheduled.
 */
export function loadDashboard(routes) {
  const document = createDocument();
  const requested = [];
  const unrouted = [];
  const page = {
    document,
    routes,
    requested,
    unrouted,
    poll: null,
    field: (name) => document.querySelector(`[data-field="${name}"]`),
    region: (name) => document.querySelector(`[data-region="${name}"]`),
    text: (name) => document.querySelector(`[data-field="${name}"]`).textContent,
    retry: () => document.querySelector('[data-action="retry"]').click(),
  };

  const sandbox = {
    document,
    fetch: async (url, init) => {
      requested.push({ url, method: init.method });
      let answer = page.routes[url];
      if (answer === undefined) {
        unrouted.push(url);
        answer = fails(404);
      }
      if (typeof answer === "function") {
        answer = answer();
      }
      answer = await answer;
      return {
        ok: answer.status >= 200 && answer.status < 300,
        status: answer.status,
        json: async () => answer.body,
      };
    },
    // The page's own poll is captured rather than scheduled, so a test runs the
    // next refresh when it decides to instead of waiting five seconds for it.
    setTimeout: (callback) => {
      page.poll = callback;
      return { poll: true };
    },
    clearTimeout: () => {
      page.poll = null;
    },
  };

  runInNewContext(readFileSync(DASHBOARD_JS, "utf8"), sandbox, {
    filename: fileURLToPath(DASHBOARD_JS),
  });
  return page;
}

/**
 * Run the event loop until the page has finished the cycle it is in.
 *
 * @param {object} page The loaded page.
 * @param {number} ticks How many turns of the loop to allow.
 */
export async function settle(page, ticks = 60) {
  page.document.querySelector(".dashboard").dataset.phase = "loading";
  for (let tick = 0; tick < ticks; tick += 1) {
    await new Promise((resolve) => setImmediate(resolve));
    if (page.document.querySelector(".dashboard").dataset.phase === "ready") {
      // One more turn, so the poll scheduled by the finally block is captured.
      await new Promise((resolve) => setImmediate(resolve));
      return;
    }
  }
  assert.fail("the dashboard never finished a refresh cycle");
}

/**
 * Turn the event loop without requiring the cycle to have finished.
 *
 * What a mid-flight page is showing is only observable while it is still
 * mid-flight, which is what this is for.
 *
 * @param {number} ticks How many turns of the loop to allow.
 */
export async function drain(ticks = 20) {
  for (let tick = 0; tick < ticks; tick += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/**
 * Load a page and wait for its first cycle.
 *
 * @param {Record<string, unknown>} routes Answers by full request URL.
 * @returns {Promise<object>} The settled page.
 */
export async function render(routes) {
  const page = loadDashboard(routes);
  await settle(page);
  assert.deepEqual(page.unrouted, [], "the page read a URL the test did not route");
  return page;
}
