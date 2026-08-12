// SPDX-License-Identifier: AGPL-3.0-or-later

"use strict";

(() => {
  const REQUEST_TIMEOUT_MS = 15_000;
  const MAX_JSON_BYTES = 16_384;

  const form = document.getElementById("loginForm");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const passwordToggle = document.getElementById("passwordToggle");
  const submitButton = document.getElementById("loginSubmit");
  const submitLabel = submitButton.querySelector(".button-label");
  const message = document.getElementById("loginMessage");

  function isLoopbackHost(hostname) {
    return hostname === "localhost"
      || hostname === "127.0.0.1"
      || hostname === "[::1]"
      || hostname === "::1";
  }

  function showMessage(text) {
    message.textContent = String(text).slice(0, 500);
    message.hidden = false;
  }

  function clearMessage() {
    message.textContent = "";
    message.hidden = true;
    emailInput.removeAttribute("aria-invalid");
    passwordInput.removeAttribute("aria-invalid");
  }

  function setBusy(isBusy) {
    emailInput.disabled = isBusy;
    passwordInput.disabled = isBusy;
    passwordToggle.disabled = isBusy;
    submitButton.disabled = isBusy;
    submitButton.classList.toggle("is-loading", isBusy);
    submitButton.setAttribute("aria-busy", String(isBusy));
    submitLabel.textContent = isBusy ? "Signing in…" : "Sign in";
  }

  async function fetchWithTimeout(url, options, timeoutMs = REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function readJsonBounded(response, byteLimit = MAX_JSON_BYTES) {
    const declaredLength = Number(response.headers.get("Content-Length"));
    if (Number.isFinite(declaredLength) && declaredLength > byteLimit) {
      throw new Error("The server returned an unexpectedly large response.");
    }
    if (!response.body) {
      const fallbackText = await response.text();
      if (new Blob([fallbackText]).size > byteLimit) {
        throw new Error("The server returned an unexpectedly large response.");
      }
      if (!fallbackText) {
        return {};
      }
      try {
        return JSON.parse(fallbackText);
      } catch {
        throw new Error("The server returned an unreadable response.");
      }
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    let totalBytes = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        totalBytes += value.byteLength;
        if (totalBytes > byteLimit) {
          await reader.cancel();
          throw new Error("The server returned an unexpectedly large response.");
        }
        text += decoder.decode(value, { stream: true });
      }
      text += decoder.decode();
    } finally {
      reader.releaseLock();
    }
    if (!text) {
      return {};
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("The server returned an unreadable response.");
    }
  }

  function errorForResponse(status, data) {
    if (status === 401 || status === 403) {
      return "That email and password were not accepted.";
    }
    if (status === 429) {
      return "Too many sign-in attempts. Please wait before trying again.";
    }

    const serverMessage = typeof data.message === "string"
      ? data.message
      : (typeof data.detail === "string" ? data.detail : "");
    return serverMessage.slice(0, 300) || "Sign-in failed. Please try again.";
  }

  async function checkExistingSession() {
    try {
      const response = await fetchWithTimeout("/api/auth/session", {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        return;
      }
      const data = await readJsonBounded(response);
      if (data.authenticated === true) {
        window.location.replace("./");
      }
    } catch {
      // The form remains usable when the optional session check is unavailable.
    }
  }

  passwordToggle.addEventListener("click", () => {
    const willShow = passwordInput.type === "password";
    passwordInput.type = willShow ? "text" : "password";
    passwordToggle.textContent = willShow ? "Hide" : "Show";
    passwordToggle.setAttribute("aria-pressed", String(willShow));
    passwordInput.focus();
  });

  for (const input of [emailInput, passwordInput]) {
    input.addEventListener("input", clearMessage);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearMessage();

    if (!form.reportValidity()) {
      return;
    }

    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || email.length > 254 || !password || password.length > 1024) {
      emailInput.setAttribute("aria-invalid", String(!email || email.length > 254));
      passwordInput.setAttribute("aria-invalid", String(!password || password.length > 1024));
      showMessage("Enter a valid email address and password.");
      return;
    }

    setBusy(true);
    try {
      const response = await fetchWithTimeout("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });

      passwordInput.value = "";
      const data = await readJsonBounded(response);
      if (!response.ok) {
        throw new Error(errorForResponse(response.status, data));
      }

      window.location.replace("./");
    } catch (error) {
      passwordInput.value = "";
      passwordInput.setAttribute("aria-invalid", "true");
      const text = error instanceof DOMException && error.name === "AbortError"
        ? "The sign-in request timed out. Check that the private host is online."
        : (error instanceof Error ? error.message : "Sign-in failed. Please try again.");
      showMessage(text);
      setBusy(false);
      passwordInput.focus();
    }
  });

  if (window.location.protocol !== "https:" && !isLoopbackHost(window.location.hostname)) {
    submitButton.disabled = true;
    showMessage("This remote connection is not protected by HTTPS. Do not enter a password here.");
  } else {
    void checkExistingSession();
  }
})();
