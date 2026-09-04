// SPDX-License-Identifier: AGPL-3.0-or-later

"use strict";

(() => {
  const API_TIMEOUT_MS = 15_000;
  const MAX_SESSION_BYTES = 32 * 1024;
  const MAX_TOKEN_LIST_BYTES = 256 * 1024;
  const MAX_CREATE_BYTES = 64 * 1024;
  const MAX_ERROR_BYTES = 32 * 1024;
  const MAX_TOKEN_CHARS = 16_384;
  const MAX_CREDENTIALS = 200;

  const elements = {
    startupScreen: document.getElementById("startupScreen"),
    zooShell: document.getElementById("zooShell"),
    zooMain: document.getElementById("zooMain"),
    signedInAs: document.getElementById("signedInAs"),
    httpsWarning: document.getElementById("httpsWarning"),
    httpsWarningText: document.getElementById("httpsWarningText"),
    baseUrlValue: document.getElementById("baseUrlValue"),
    modelValue: document.getElementById("modelValue"),
    contextWindowValue: document.getElementById("contextWindowValue"),
    maxOutputValue: document.getElementById("maxOutputValue"),
    copyStatus: document.getElementById("copyStatus"),
    credentialForm: document.getElementById("credentialForm"),
    credentialName: document.getElementById("credentialName"),
    currentPassword: document.getElementById("currentPassword"),
    createMessage: document.getElementById("createMessage"),
    createButton: document.getElementById("createButton"),
    createButtonLabel: document.querySelector("#createButton .button-label"),
    expiryHint: document.getElementById("expiryHint"),
    oneTimeCard: document.getElementById("oneTimeCard"),
    oneTimeToken: document.getElementById("oneTimeToken"),
    copyToken: document.getElementById("copyToken"),
    dismissToken: document.getElementById("dismissToken"),
    oneTimeHelp: document.getElementById("oneTimeHelp"),
    refreshTokens: document.getElementById("refreshTokens"),
    tokenList: document.getElementById("tokenList"),
    tokenListMessage: document.getElementById("tokenListMessage"),
  };

  const state = {
    csrfToken: "",
    credentials: [],
    setup: null,
    createBusy: false,
    refreshBusy: false,
    revokingIds: new Set(),
  };

  function boundedText(value, maxLength = 500) {
    return typeof value === "string" ? value.slice(0, maxLength) : "";
  }

  function createElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function redirectToLogin() {
    window.location.replace("./login.html");
  }

  function showFatalStartup(text) {
    elements.startupScreen.replaceChildren();
    const mark = document.createElement("img");
    mark.src = "mark.svg";
    mark.width = 52;
    mark.height = 52;
    mark.alt = "";
    const message = createElement("p", "startup-error", boundedText(text, 500));
    const retry = createElement("button", "primary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => window.location.reload());
    elements.startupScreen.append(mark, message, retry);
  }

  async function fetchWithTimeout(url, options, timeoutMs = API_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function readTextBounded(response, byteLimit) {
    const declaredLength = Number(response.headers.get("Content-Length"));
    if (Number.isFinite(declaredLength) && declaredLength > byteLimit) {
      throw new Error("The server returned an unexpectedly large response.");
    }

    if (!response.body) {
      const fallbackText = await response.text();
      if (new Blob([fallbackText]).size > byteLimit) {
        throw new Error("The server returned an unexpectedly large response.");
      }
      return fallbackText;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let totalBytes = 0;
    let output = "";
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
        output += decoder.decode(value, { stream: true });
      }
      output += decoder.decode();
      return output;
    } finally {
      reader.releaseLock();
    }
  }

  async function readJsonBounded(response, byteLimit) {
    const text = await readTextBounded(response, byteLimit);
    if (!text) {
      return {};
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("The server returned an unreadable response.");
    }
  }

  function apiError(status, data, fallback) {
    if (status === 401) {
      return "Your session has expired. Please sign in again.";
    }
    if (status === 403) {
      return "This request was not authorized. Refresh the page and sign in again.";
    }
    if (status === 429) {
      return "Too many requests were received. Wait a moment and try again.";
    }
    const serverMessage = data && typeof data === "object"
      ? (typeof data.message === "string"
        ? data.message
        : (typeof data.detail === "string" ? data.detail : ""))
      : "";
    return boundedText(serverMessage, 500) || fallback;
  }

  function friendlyError(error, fallback) {
    if (error && error.name === "AbortError") {
      return "The request timed out. Check the active credential list before trying again.";
    }
    if (error instanceof Error && error.message) {
      return boundedText(error.message, 500);
    }
    return fallback;
  }

  function showCreateMessage(text) {
    elements.createMessage.textContent = boundedText(text, 500);
    elements.createMessage.hidden = false;
  }

  function clearCreateMessage() {
    elements.createMessage.textContent = "";
    elements.createMessage.hidden = true;
    elements.credentialName.removeAttribute("aria-invalid");
    elements.currentPassword.removeAttribute("aria-invalid");
  }

  function setCreateBusy(isBusy) {
    state.createBusy = isBusy;
    elements.credentialName.disabled = isBusy;
    elements.currentPassword.disabled = isBusy;
    elements.createButton.disabled = isBusy;
    elements.createButton.classList.toggle("is-loading", isBusy);
    elements.createButtonLabel.textContent = isBusy ? "Creating…" : "Create credential";
  }

  function setRefreshBusy(isBusy) {
    state.refreshBusy = isBusy;
    elements.refreshTokens.disabled = isBusy;
    elements.refreshTokens.textContent = isBusy ? "Refreshing…" : "Refresh";
  }

  function normalizePositiveInteger(value, maximum) {
    return Number.isSafeInteger(value) && value > 0 && value <= maximum ? value : null;
  }

  function normalizeTimestamp(value) {
    if (Number.isSafeInteger(value) && value >= 0) {
      return value;
    }
    return boundedText(value, 100).trim() || null;
  }

  function normalizeSetup(rawSetup) {
    const source = rawSetup && typeof rawSetup === "object" ? rawSetup : {};
    return {
      baseUrl: boundedText(source.base_url, 2048).trim(),
      model: boundedText(source.model, 300).trim(),
      contextWindow: normalizePositiveInteger(source.context_window, 100_000_000),
      maxOutputTokens: normalizePositiveInteger(source.max_output_tokens, 10_000_000),
      tokenTtlDays: normalizePositiveInteger(source.token_ttl_days, 10_000),
    };
  }

  function normalizeCredential(rawCredential) {
    if (!rawCredential || typeof rawCredential !== "object") {
      return null;
    }
    const rawId = typeof rawCredential.id === "number"
      ? String(rawCredential.id)
      : boundedText(rawCredential.id, 200);
    const id = rawId.trim();
    if (!id) {
      return null;
    }
    return {
      id,
      name: boundedText(rawCredential.name, 120).trim() || "Unnamed Zoo Code client",
      createdAt: normalizeTimestamp(rawCredential.created_at),
      expiresAt: normalizeTimestamp(rawCredential.expires_at),
      lastUsedAt: normalizeTimestamp(rawCredential.last_used_at),
    };
  }

  function normalizeCredentials(rawCredentials) {
    if (!Array.isArray(rawCredentials)) {
      return [];
    }
    const seen = new Set();
    const credentials = [];
    for (const rawCredential of rawCredentials.slice(0, MAX_CREDENTIALS)) {
      const credential = normalizeCredential(rawCredential);
      if (credential && !seen.has(credential.id)) {
        seen.add(credential.id);
        credentials.push(credential);
      }
    }
    return credentials;
  }

  function setSettingValue(element, value) {
    const usable = value !== null && value !== undefined && String(value).length > 0;
    element.textContent = usable ? String(value) : "Unavailable";
    const copyButton = document.querySelector(`[data-copy-target="${element.id}"]`);
    if (copyButton) {
      copyButton.disabled = !usable;
    }
  }

  function isLoopbackHostname(hostname) {
    const normalized = hostname.toLocaleLowerCase();
    return normalized === "localhost"
      || normalized === "127.0.0.1"
      || normalized === "[::1]"
      || normalized === "::1";
  }

  function updateHttpsWarning(baseUrl) {
    elements.httpsWarning.dataset.severity = "warning";
    let parsedUrl = null;
    try {
      parsedUrl = new URL(baseUrl);
    } catch {
      parsedUrl = null;
    }

    if (parsedUrl && parsedUrl.protocol === "https:") {
      elements.httpsWarning.dataset.severity = "secure";
      elements.httpsWarningText.textContent =
        "This Base URL uses HTTPS. For remote VS Code access, keep its certificate valid and restrict network access to intended users.";
      return;
    }

    if (parsedUrl && parsedUrl.protocol === "http:" && isLoopbackHostname(parsedUrl.hostname)) {
      elements.httpsWarningText.textContent =
        "This loopback HTTP Base URL is suitable only when VS Code runs on this same computer. For VS Code on another computer, publish KevinBeLLM behind a trusted HTTPS endpoint first.";
      return;
    }

    elements.httpsWarning.dataset.severity = "danger";
    elements.httpsWarningText.textContent =
      "Do not use this Base URL from another computer: plain HTTP can expose the API key. Put KevinBeLLM behind a trusted HTTPS endpoint before enabling remote access.";
  }

  function applySetup(rawSetup) {
    const setup = normalizeSetup(rawSetup);
    state.setup = setup;
    setSettingValue(elements.baseUrlValue, setup.baseUrl);
    setSettingValue(elements.modelValue, setup.model);
    setSettingValue(
      elements.contextWindowValue,
      setup.contextWindow === null ? null : String(setup.contextWindow),
    );
    setSettingValue(
      elements.maxOutputValue,
      setup.maxOutputTokens === null ? null : String(setup.maxOutputTokens),
    );
    elements.expiryHint.textContent = setup.tokenTtlDays === null
      ? "The server controls credential expiration."
      : `New credentials expire after ${setup.tokenTtlDays.toLocaleString("en-US")} days.`;
    updateHttpsWarning(setup.baseUrl);
  }

  function formatTimestamp(value, emptyLabel) {
    if (value === null || value === "") {
      return emptyLabel;
    }
    const parsed = new Date(typeof value === "number" ? value * 1000 : value);
    if (Number.isNaN(parsed.getTime())) {
      return "Unknown";
    }
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(parsed);
  }

  const RELATIVE_TIME_UNITS = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];

  function describeRelativeTime(value, nowSeconds) {
    if (typeof value !== "number") {
      return "";
    }
    const delta = value - nowSeconds;
    const magnitude = Math.abs(delta);
    const formatter = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
    for (const [unit, seconds] of RELATIVE_TIME_UNITS) {
      if (magnitude >= seconds) {
        return formatter.format(Math.round(delta / seconds), unit);
      }
    }
    return delta >= 0 ? "moments from now" : "moments ago";
  }

  function appendCredentialDetail(container, label, value, note) {
    // Each label shares one grid cell with its value, so the pairs stay
    // together no matter how many columns the list uses.
    const group = createElement("div", "zoo-token-detail");
    const term = createElement("dt", "", label);
    const description = createElement("dd", "", value);
    if (note) {
      description.append(createElement("small", "zoo-token-detail-note", note));
    }
    group.append(term, description);
    container.append(group);
  }

  function renderCredentials() {
    elements.tokenList.replaceChildren();
    if (state.credentials.length === 0) {
      elements.tokenListMessage.textContent = "No active Zoo Code credentials.";
      return;
    }

    elements.tokenListMessage.textContent = `${state.credentials.length.toLocaleString("en-US")} active credential${state.credentials.length === 1 ? "" : "s"}.`;
    const nowSeconds = Math.floor(Date.now() / 1000);
    for (const credential of state.credentials) {
      const item = createElement("li", "zoo-token-item");
      const summary = createElement("div", "zoo-token-summary");
      const name = createElement("h3", "", credential.name);
      const status = createElement("span", "zoo-active-badge", "Active");
      summary.append(name, status);

      const details = createElement("dl", "zoo-token-details");
      const lifetimeDays =
        typeof credential.createdAt === "number" && typeof credential.expiresAt === "number"
          ? Math.round((credential.expiresAt - credential.createdAt) / 86_400)
          : null;
      const expiresRelative = describeRelativeTime(credential.expiresAt, nowSeconds);
      appendCredentialDetail(
        details,
        "Created",
        formatTimestamp(credential.createdAt, "Unknown"),
        describeRelativeTime(credential.createdAt, nowSeconds),
      );
      appendCredentialDetail(
        details,
        "Expires",
        formatTimestamp(credential.expiresAt, "No expiry reported"),
        lifetimeDays === null
          ? expiresRelative
          : `${expiresRelative}, ${lifetimeDays.toLocaleString("en-US")} days after creation`,
      );
      appendCredentialDetail(
        details,
        "Last used",
        formatTimestamp(credential.lastUsedAt, "Never"),
        describeRelativeTime(credential.lastUsedAt, nowSeconds),
      );

      const revoke = createElement("button", "zoo-revoke-button", "Revoke");
      revoke.type = "button";
      revoke.setAttribute("aria-label", `Revoke ${credential.name}`);
      revoke.disabled = state.revokingIds.has(credential.id);
      revoke.addEventListener("click", () => void requestRevoke(credential, revoke));

      item.append(summary, details, revoke);
      elements.tokenList.append(item);
    }
  }

  function populateSignedInUser(rawUser) {
    const user = rawUser && typeof rawUser === "object" ? rawUser : {};
    const name = boundedText(user.name, 120).trim();
    const email = boundedText(user.email, 254).trim();
    const display = name || email || "Authorized user";
    elements.signedInAs.textContent = `Signed in as ${display}`;
  }

  async function loadSession() {
    const response = await fetchWithTimeout("/api/auth/session", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
    if (response.status === 401) {
      redirectToLogin();
      return false;
    }
    const data = await readJsonBounded(response, MAX_SESSION_BYTES);
    if (!response.ok) {
      throw new Error(apiError(response.status, data, "The authenticated session could not be checked."));
    }
    if (data.authenticated === false) {
      redirectToLogin();
      return false;
    }
    if (typeof data.csrf_token !== "string" || data.csrf_token.length < 8 || data.csrf_token.length > 1024) {
      throw new Error("The authenticated session did not include a valid request token.");
    }
    state.csrfToken = data.csrf_token;
    populateSignedInUser(data.user);
    return true;
  }

  async function requestCredentialPayload() {
    const response = await fetchWithTimeout("/api/auth/api-tokens", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" },
    });
    if (response.status === 401) {
      redirectToLogin();
      return null;
    }
    const data = await readJsonBounded(response, MAX_TOKEN_LIST_BYTES);
    if (!response.ok) {
      throw new Error(apiError(response.status, data, "Active credentials could not be loaded."));
    }
    return data;
  }

  function applyCredentialPayload(data) {
    const payload = data && typeof data === "object" ? data : {};
    state.credentials = normalizeCredentials(payload.tokens);
    applySetup(payload.setup);
    renderCredentials();
  }

  async function refreshCredentialList() {
    if (state.refreshBusy) {
      return;
    }
    setRefreshBusy(true);
    elements.tokenListMessage.textContent = "Refreshing active credentials…";
    try {
      const data = await requestCredentialPayload();
      if (data === null) {
        return;
      }
      applyCredentialPayload(data);
    } catch (error) {
      elements.tokenListMessage.textContent = friendlyError(error, "Active credentials could not be refreshed.");
    } finally {
      setRefreshBusy(false);
    }
  }

  async function copyText(text) {
    const bounded = boundedText(text, MAX_TOKEN_CHARS);
    if (!bounded) {
      throw new Error("There is no value to copy.");
    }
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(bounded);
      return;
    }

    const temporaryInput = document.createElement("textarea");
    temporaryInput.value = bounded;
    temporaryInput.readOnly = true;
    temporaryInput.setAttribute("aria-hidden", "true");
    temporaryInput.className = "zoo-copy-fallback";
    document.body.append(temporaryInput);
    let copied = false;
    try {
      temporaryInput.select();
      copied = document.execCommand("copy");
    } finally {
      temporaryInput.value = "";
      temporaryInput.remove();
    }
    if (!copied) {
      throw new Error("The browser did not allow copying.");
    }
  }

  async function copySetting(button) {
    const targetId = boundedText(button.dataset.copyTarget, 100);
    const target = targetId ? document.getElementById(targetId) : null;
    if (!target) {
      return;
    }
    try {
      await copyText(target.textContent || "");
      elements.copyStatus.textContent = `${button.getAttribute("aria-label") || "Value"} copied.`;
      button.textContent = "Copied";
      window.setTimeout(() => {
        if (button.isConnected) {
          button.textContent = "Copy";
        }
      }, 1800);
    } catch (error) {
      elements.copyStatus.textContent = friendlyError(error, "Copy failed. Select and copy the value manually.");
    }
  }

  function clearOneTimeToken() {
    elements.oneTimeToken.textContent = "";
    elements.oneTimeCard.hidden = true;
    elements.copyToken.textContent = "Copy API key";
    elements.oneTimeHelp.textContent =
      "Keep this credential private. Do not put it in source control, chat, logs, or a URL.";
  }

  function showOneTimeToken(token) {
    clearOneTimeToken();
    elements.oneTimeToken.textContent = token;
    elements.oneTimeCard.hidden = false;
    elements.oneTimeCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    elements.oneTimeToken.focus({ preventScroll: true });
  }

  async function copyOneTimeToken() {
    const token = boundedText(elements.oneTimeToken.textContent, MAX_TOKEN_CHARS);
    if (!token) {
      return;
    }
    try {
      await copyText(token);
      elements.copyToken.textContent = "Copied";
      elements.oneTimeHelp.textContent =
        "Copied. Paste it into Zoo Code now, then dismiss and clear it from this page.";
    } catch (error) {
      elements.oneTimeHelp.textContent =
        `${friendlyError(error, "Copy failed.")} Select the API key and copy it manually.`;
      elements.oneTimeToken.focus();
    }
  }

  async function createCredential(event) {
    event.preventDefault();
    if (state.createBusy) {
      return;
    }
    clearCreateMessage();

    if (elements.oneTimeToken.textContent) {
      showCreateMessage("Copy or dismiss the current shown-once API key before creating another.");
      elements.oneTimeCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }

    const name = elements.credentialName.value.trim();
    const currentPassword = elements.currentPassword.value;
    if (!name || name.length > 80) {
      elements.credentialName.setAttribute("aria-invalid", "true");
      showCreateMessage("Enter a credential name between 1 and 80 characters.");
      elements.credentialName.focus();
      return;
    }
    if (!currentPassword || currentPassword.length > 1024) {
      elements.currentPassword.setAttribute("aria-invalid", "true");
      showCreateMessage("Enter your current account password.");
      elements.currentPassword.focus();
      return;
    }

    setCreateBusy(true);
    try {
      const response = await fetchWithTimeout("/api/auth/api-tokens", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": state.csrfToken,
        },
        body: JSON.stringify({ name, current_password: currentPassword }),
      });
      elements.currentPassword.value = "";
      const data = await readJsonBounded(response, MAX_CREATE_BYTES);
      if (response.status === 401) {
        const sessionStillValid = await loadSession();
        if (!sessionStillValid) {
          return;
        }
        const passwordMessage = data && typeof data === "object" && typeof data.detail === "string"
          ? boundedText(data.detail, 500)
          : "The current password was not accepted.";
        throw new Error(passwordMessage);
      }
      if (!response.ok) {
        throw new Error(apiError(response.status, data, "The credential could not be created."));
      }

      const payload = data && typeof data === "object" ? data : {};
      const token = typeof payload.token === "string" ? payload.token : "";
      payload.token = "";
      if (!token || token.length > MAX_TOKEN_CHARS) {
        void refreshCredentialList();
        throw new Error("A credential was created, but its shown-once API key was not returned. Refresh the list and revoke it before trying again.");
      }

      if (payload.setup && typeof payload.setup === "object") {
        applySetup(payload.setup);
      }
      const credential = normalizeCredential(payload.credential);
      if (credential) {
        state.credentials = [credential, ...state.credentials.filter((item) => item.id !== credential.id)];
        renderCredentials();
      }
      elements.credentialName.value = "";
      showOneTimeToken(token);

      if (!credential) {
        void refreshCredentialList();
      }
    } catch (error) {
      elements.currentPassword.value = "";
      showCreateMessage(friendlyError(error, "The credential could not be created."));
    } finally {
      setCreateBusy(false);
    }
  }

  async function requestRevoke(credential, button) {
    if (state.revokingIds.has(credential.id)) {
      return;
    }
    if (button.dataset.confirming !== "true") {
      button.dataset.confirming = "true";
      button.classList.add("is-confirming");
      button.textContent = "Confirm revoke";
      elements.tokenListMessage.textContent = `Confirm revocation for ${credential.name}.`;
      window.setTimeout(() => {
        if (button.isConnected && button.dataset.confirming === "true") {
          delete button.dataset.confirming;
          button.classList.remove("is-confirming");
          button.textContent = "Revoke";
          elements.tokenListMessage.textContent = `${state.credentials.length.toLocaleString("en-US")} active credential${state.credentials.length === 1 ? "" : "s"}.`;
        }
      }, 6000);
      return;
    }

    state.revokingIds.add(credential.id);
    button.disabled = true;
    button.textContent = "Revoking…";
    try {
      const response = await fetchWithTimeout(`/api/auth/api-tokens/${encodeURIComponent(credential.id)}`, {
        method: "DELETE",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json",
          "X-CSRF-Token": state.csrfToken,
        },
      });
      if (response.status === 401) {
        redirectToLogin();
        return;
      }
      if (!response.ok) {
        const data = await readJsonBounded(response, MAX_ERROR_BYTES);
        throw new Error(apiError(response.status, data, "The credential could not be revoked."));
      }
      if (response.status !== 204) {
        await readTextBounded(response, MAX_ERROR_BYTES);
      }
      state.credentials = state.credentials.filter((item) => item.id !== credential.id);
      renderCredentials();
      elements.tokenListMessage.textContent = `${credential.name} was revoked.`;
    } catch (error) {
      elements.tokenListMessage.textContent = friendlyError(error, "The credential could not be revoked.");
      delete button.dataset.confirming;
      button.classList.remove("is-confirming");
      button.textContent = "Revoke";
      button.disabled = false;
    } finally {
      state.revokingIds.delete(credential.id);
    }
  }

  function bindEvents() {
    elements.credentialForm.addEventListener("submit", (event) => void createCredential(event));
    elements.copyToken.addEventListener("click", () => void copyOneTimeToken());
    elements.dismissToken.addEventListener("click", () => {
      clearOneTimeToken();
      elements.credentialName.focus();
    });
    elements.refreshTokens.addEventListener("click", () => void refreshCredentialList());
    for (const button of document.querySelectorAll("[data-copy-target]")) {
      button.addEventListener("click", () => void copySetting(button));
    }
    window.addEventListener("pagehide", () => {
      clearOneTimeToken();
      elements.currentPassword.value = "";
      state.csrfToken = "";
    });
  }

  async function bootstrap() {
    bindEvents();
    try {
      const authenticated = await loadSession();
      if (!authenticated) {
        return;
      }
      const data = await requestCredentialPayload();
      if (data === null) {
        return;
      }
      applyCredentialPayload(data);
      elements.startupScreen.hidden = true;
      elements.zooShell.hidden = false;
    } catch (error) {
      showFatalStartup(friendlyError(error, "Zoo Code access could not be opened."));
    }
  }

  void bootstrap();
})();
