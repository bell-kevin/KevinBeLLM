// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Exercises static/theme.js against a minimal window shim: a root element that
// records attributes, theme-color meta tags, the theme radio buttons, a
// matchMedia query, and a localStorage map. The shim implements only what the
// script touches, so any new DOM dependency shows up here as a failure.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { runInNewContext } from "node:vm";
import test from "node:test";

const STORAGE_KEY = "kevinbellm-theme";
const LIGHT = "#f3f7f5";
const DARK = "#071310";

const source = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "static", "theme.js"),
  "utf8",
);

class FakeElement {
  constructor(attributes = {}) {
    this.attributes = new Map(Object.entries(attributes));
    this.listeners = new Map();
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, []);
    }
    this.listeners.get(type).push(listener);
  }

  dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

class FakeRadio extends FakeElement {
  constructor(value) {
    super({ type: "radio", name: "theme" });
    this.value = value;
    this.checked = false;
  }

  select() {
    this.checked = true;
    this.dispatch("change");
  }
}

function themeMeta(media) {
  const attributes = { name: "theme-color", "data-light": LIGHT, "data-dark": DARK };
  if (media) {
    attributes.media = media;
    attributes.content = media.includes("dark") ? DARK : LIGHT;
  } else {
    attributes.content = DARK;
  }
  return new FakeElement(attributes);
}

function createEnvironment({
  stored = null,
  storageThrows = false,
  prefersDark = false,
  readyState = "complete",
  metas = [themeMeta("(prefers-color-scheme: light)"), themeMeta("(prefers-color-scheme: dark)")],
} = {}) {
  const storage = new Map();
  if (stored !== null) {
    storage.set(STORAGE_KEY, stored);
  }
  const localStorage = {
    getItem(key) {
      if (storageThrows) {
        throw new Error("storage blocked");
      }
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      if (storageThrows) {
        throw new Error("storage blocked");
      }
      storage.set(key, String(value));
    },
    removeItem(key) {
      if (storageThrows) {
        throw new Error("storage blocked");
      }
      storage.delete(key);
    },
  };

  const radios = [new FakeRadio("auto"), new FakeRadio("light"), new FakeRadio("dark")];
  const documentTarget = new FakeElement();
  const document = {
    documentElement: new FakeElement(),
    readyState,
    querySelectorAll(selector) {
      if (selector.startsWith("input")) {
        return radios;
      }
      if (selector.startsWith("meta")) {
        return metas;
      }
      return [];
    },
    addEventListener: (type, listener) => documentTarget.addEventListener(type, listener),
  };
  const darkQuery = new FakeElement();
  darkQuery.matches = prefersDark;
  const windowTarget = new FakeElement();
  const sandbox = {
    document,
    localStorage,
    matchMedia: () => darkQuery,
    addEventListener: (type, listener) => windowTarget.addEventListener(type, listener),
  };
  sandbox.window = sandbox;
  runInNewContext(source, sandbox);

  return {
    root: document.documentElement,
    radios,
    metas,
    storage,
    checked: () => radios.filter((radio) => radio.checked).map((radio) => radio.value),
    domReady: () => documentTarget.dispatch("DOMContentLoaded"),
    systemChange: (matches) => {
      darkQuery.matches = matches;
      darkQuery.dispatch("change");
    },
    storageEvent: (key) => windowTarget.dispatch("storage", { key }),
  };
}

test("a stored light preference pins the theme before any control exists", () => {
  const env = createEnvironment({ stored: "light", prefersDark: true });
  assert.equal(env.root.getAttribute("data-theme"), "light");
  assert.deepEqual(env.checked(), ["light"]);
  for (const meta of env.metas) {
    assert.equal(meta.getAttribute("content"), LIGHT);
  }
});

test("no stored preference follows the system and leaves media-scoped metas alone", () => {
  const env = createEnvironment({ prefersDark: true });
  assert.equal(env.root.getAttribute("data-theme"), null);
  assert.deepEqual(env.checked(), ["auto"]);
  assert.equal(env.metas[0].getAttribute("content"), LIGHT);
  assert.equal(env.metas[1].getAttribute("content"), DARK);
});

test("an unscoped theme-color meta tracks the system scheme while in auto mode", () => {
  const env = createEnvironment({ prefersDark: true, metas: [themeMeta(null)] });
  assert.equal(env.metas[0].getAttribute("content"), DARK);
  env.systemChange(false);
  assert.equal(env.metas[0].getAttribute("content"), LIGHT);
});

test("unexpected stored values fall back to auto", () => {
  const env = createEnvironment({ stored: "purple" });
  assert.equal(env.root.getAttribute("data-theme"), null);
  assert.deepEqual(env.checked(), ["auto"]);
});

test("blocked storage never breaks the page", () => {
  const env = createEnvironment({ storageThrows: true });
  assert.equal(env.root.getAttribute("data-theme"), null);
  env.radios[2].select();
  assert.equal(env.root.getAttribute("data-theme"), "dark");
});

test("choosing a theme persists it and choosing auto clears the stored value", () => {
  const env = createEnvironment();
  env.radios[2].select();
  assert.equal(env.root.getAttribute("data-theme"), "dark");
  assert.equal(env.storage.get(STORAGE_KEY), "dark");
  for (const meta of env.metas) {
    assert.equal(meta.getAttribute("content"), DARK);
  }

  env.radios[0].select();
  assert.equal(env.root.getAttribute("data-theme"), null);
  assert.equal(env.storage.has(STORAGE_KEY), false);
  assert.equal(env.metas[0].getAttribute("content"), LIGHT);
  assert.equal(env.metas[1].getAttribute("content"), DARK);
});

test("a change made in another tab is mirrored here", () => {
  const env = createEnvironment();
  env.storage.set(STORAGE_KEY, "light");
  env.storageEvent(STORAGE_KEY);
  assert.equal(env.root.getAttribute("data-theme"), "light");
  assert.deepEqual(env.checked(), ["light"]);

  env.storage.clear();
  env.storageEvent(null);
  assert.equal(env.root.getAttribute("data-theme"), null);
  assert.deepEqual(env.checked(), ["auto"]);
});

test("controls are bound once the document finishes parsing", () => {
  const env = createEnvironment({ stored: "dark", readyState: "loading" });
  assert.equal(env.root.getAttribute("data-theme"), "dark");
  assert.deepEqual(env.checked(), []);
  env.domReady();
  assert.deepEqual(env.checked(), ["dark"]);
  env.radios[1].select();
  assert.equal(env.root.getAttribute("data-theme"), "light");
});
