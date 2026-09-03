// SPDX-License-Identifier: AGPL-3.0-or-later

"use strict";

/*
 * Color theme preference: "light", "dark", or "auto" (follow the operating
 * system). The choice is kept only in this browser's localStorage and is never
 * sent anywhere. Load this file synchronously in <head>, after the theme-color
 * meta tags, so a stored preference applies before the first paint.
 *
 * The stylesheet does the actual theming with light-dark() tokens: no
 * data-theme attribute on <html> means "follow the system", while
 * data-theme="light" or data-theme="dark" pins the color scheme.
 */
(() => {
  const STORAGE_KEY = "kevinbellm-theme";
  const CONTROL_SELECTOR = 'input[type="radio"][name="theme"]';
  const META_SELECTOR = 'meta[name="theme-color"][data-light][data-dark]';
  const root = document.documentElement;
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "auto";

  function normalize(value) {
    return value === "light" || value === "dark" ? value : "auto";
  }

  function readStoredPreference() {
    try {
      return normalize(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      return "auto";
    }
  }

  function storePreference(value) {
    try {
      if (value === "auto") {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, value);
      }
    } catch {
      // Storage can be blocked or full; the choice still applies to this page view.
    }
  }

  function systemScheme() {
    return darkQuery.matches ? "dark" : "light";
  }

  function metaScheme(meta) {
    if (preference !== "auto") {
      return preference;
    }
    // In auto mode a media-scoped tag keeps its own color so the browser can
    // pick the matching one; an unscoped tag follows the system setting.
    const media = meta.getAttribute("media") || "";
    if (media.includes("dark")) {
      return "dark";
    }
    if (media.includes("light")) {
      return "light";
    }
    return systemScheme();
  }

  function updateThemeColorMeta() {
    for (const meta of document.querySelectorAll(META_SELECTOR)) {
      const scheme = metaScheme(meta);
      meta.setAttribute("content", meta.getAttribute(scheme === "dark" ? "data-dark" : "data-light"));
    }
  }

  function applyPreference() {
    if (preference === "auto") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", preference);
    }
    updateThemeColorMeta();
  }

  function syncControls() {
    for (const input of document.querySelectorAll(CONTROL_SELECTOR)) {
      input.checked = input.value === preference;
    }
  }

  function setPreference(value, persist) {
    preference = normalize(value);
    if (persist) {
      storePreference(preference);
    }
    applyPreference();
    syncControls();
  }

  function bindControls() {
    for (const input of document.querySelectorAll(CONTROL_SELECTOR)) {
      input.addEventListener("change", () => {
        if (input.checked) {
          setPreference(input.value, true);
        }
      });
    }
    syncControls();
  }

  preference = readStoredPreference();
  applyPreference();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  } else {
    bindControls();
  }

  darkQuery.addEventListener("change", () => {
    if (preference === "auto") {
      updateThemeColorMeta();
    }
  });

  // Keep every open tab in step when the preference changes in one of them.
  window.addEventListener("storage", (event) => {
    if (event.key === null || event.key === STORAGE_KEY) {
      setPreference(readStoredPreference(), false);
    }
  });
})();
