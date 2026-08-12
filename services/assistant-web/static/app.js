// SPDX-License-Identifier: AGPL-3.0-or-later

"use strict";

(() => {
  const MAX_PROMPT_CHARS = 8_000;
  const MAX_RESPONSE_CHARS = 96_000;
  const MAX_REQUEST_MESSAGE_CHARS = 12_000;
  const MAX_REQUEST_MESSAGES = 32;
  const MAX_REQUEST_CHARS = 48_000;
  const MAX_REQUEST_BYTES = 120 * 1024;
  const MAX_STORED_MESSAGES = 64;
  const MAX_JSON_BYTES = 256 * 1024;
  const MAX_SSE_BYTES = 5 * 1024 * 1024;
  const MAX_SSE_LINE_CHARS = 256 * 1024;
  const MAX_SSE_EVENT_CHARS = 256 * 1024;
  const API_TIMEOUT_MS = 15_000;
  const CHAT_TIMEOUT_MS = 20 * 60 * 1000;

  class StreamEventError extends Error {
    constructor(message) {
      super(message);
      this.name = "StreamEventError";
    }
  }

  const elements = {
    startupScreen: document.getElementById("startupScreen"),
    appShell: document.getElementById("appShell"),
    sidebar: document.getElementById("sidebar"),
    sidebarToggle: document.getElementById("sidebarToggle"),
    sidebarClose: document.getElementById("sidebarClose"),
    sidebarScrim: document.getElementById("sidebarScrim"),
    newChatButton: document.getElementById("newChatButton"),
    accountName: document.getElementById("accountName"),
    accountEmail: document.getElementById("accountEmail"),
    accountAvatar: document.getElementById("accountAvatar"),
    changePasswordButton: document.getElementById("changePasswordButton"),
    logoutButton: document.getElementById("logoutButton"),
    modelSelect: document.getElementById("modelSelect"),
    modelMeta: document.getElementById("modelMeta"),
    recommendedBadge: document.getElementById("recommendedBadge"),
    modelsRetry: document.getElementById("modelsRetry"),
    chatMain: document.getElementById("chatMain"),
    chatScroll: document.getElementById("chatScroll"),
    emptyState: document.getElementById("emptyState"),
    messages: document.getElementById("messages"),
    streamAnnouncement: document.getElementById("streamAnnouncement"),
    appNotice: document.getElementById("appNotice"),
    composerForm: document.getElementById("composerForm"),
    promptInput: document.getElementById("promptInput"),
    sendButton: document.getElementById("sendButton"),
    stopButton: document.getElementById("stopButton"),
    passwordDialog: document.getElementById("passwordDialog"),
    passwordDialogClose: document.getElementById("passwordDialogClose"),
    passwordForm: document.getElementById("passwordForm"),
    currentPassword: document.getElementById("currentPassword"),
    newPassword: document.getElementById("newPassword"),
    confirmPassword: document.getElementById("confirmPassword"),
    passwordMessage: document.getElementById("passwordMessage"),
    passwordCancel: document.getElementById("passwordCancel"),
    passwordSubmit: document.getElementById("passwordSubmit"),
  };

  const state = {
    csrfToken: "",
    user: null,
    models: [],
    defaultModel: "",
    messages: [],
    isGenerating: false,
    activeController: null,
    stopRequested: false,
    timeoutFired: false,
    passwordBusy: false,
  };

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

  function boundedText(value, maxLength = 500) {
    return typeof value === "string" ? value.slice(0, maxLength) : "";
  }

  function redirectToLogin() {
    window.location.replace("./login.html");
  }

  function showNotice(text) {
    elements.appNotice.textContent = boundedText(text, 700);
    elements.appNotice.hidden = false;
  }

  function clearNotice() {
    elements.appNotice.textContent = "";
    elements.appNotice.hidden = true;
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

  async function readJsonBounded(response, byteLimit = MAX_JSON_BYTES) {
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
      return "The service is receiving too many requests. Please wait and try again.";
    }
    const serverMessage = typeof data.message === "string"
      ? data.message
      : (typeof data.detail === "string" ? data.detail : "");
    return boundedText(serverMessage, 500) || fallback;
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
    const data = await readJsonBounded(response, 32 * 1024);
    if (!response.ok) {
      throw new Error(apiError(response.status, data, "The authenticated session could not be checked."));
    }
    if (data.authenticated !== true) {
      redirectToLogin();
      return false;
    }
    if (typeof data.csrf_token !== "string" || data.csrf_token.length < 8 || data.csrf_token.length > 1024) {
      throw new Error("The authenticated session did not include a valid request token.");
    }

    state.csrfToken = data.csrf_token;
    state.user = data.user && typeof data.user === "object" ? data.user : {};
    populateAccount(state.user);
    return true;
  }

  function populateAccount(user) {
    const name = boundedText(user.name, 120).trim();
    const email = boundedText(user.email, 254).trim();
    const displayName = name || email || "Authorized user";
    elements.accountName.textContent = displayName;
    elements.accountEmail.textContent = name ? email : "Private account";
    elements.accountAvatar.textContent = Array.from(displayName)[0]?.toLocaleUpperCase() || "U";
  }

  function normalizeModel(rawModel) {
    if (!rawModel || typeof rawModel !== "object") {
      return null;
    }
    const id = boundedText(rawModel.id, 300).trim();
    if (!id) {
      return null;
    }
    return {
      id,
      name: boundedText(rawModel.name, 300).trim() || id,
      size: typeof rawModel.size === "number" && rawModel.size >= 0 ? rawModel.size : null,
      parameterSize: boundedText(rawModel.parameter_size, 100).trim(),
      quantization: boundedText(rawModel.quantization, 100).trim(),
      recommended: rawModel.recommended === true,
    };
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "";
    }
    const units = ["B", "KB", "MB", "GB", "TB"];
    const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / (1024 ** unitIndex);
    return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
  }

  function selectedModel() {
    return state.models.find((model) => model.id === elements.modelSelect.value) || null;
  }

  function updateModelDetails() {
    const model = selectedModel();
    elements.recommendedBadge.hidden = !model?.recommended;
    if (!model) {
      updateSendButton();
      return;
    }
    const details = [model.parameterSize, model.quantization, formatBytes(model.size)].filter(Boolean);
    elements.modelMeta.textContent = details.length > 0
      ? details.join(" · ")
      : "Installed on the private host";
    updateSendButton();
  }

  async function loadModels() {
    const priorSelection = elements.modelSelect.value;
    elements.modelsRetry.hidden = true;
    elements.modelSelect.disabled = true;
    elements.modelMeta.textContent = "Checking the local model service…";

    try {
      const response = await fetchWithTimeout("/api/models", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" },
      });
      if (response.status === 401) {
        redirectToLogin();
        return;
      }
      const data = await readJsonBounded(response);
      if (!response.ok) {
        throw new Error(apiError(response.status, data, "Installed models could not be loaded."));
      }

      const rawModels = Array.isArray(data.models) ? data.models.slice(0, 200) : [];
      const uniqueIds = new Set();
      state.models = rawModels
        .map(normalizeModel)
        .filter((model) => model && !uniqueIds.has(model.id) && uniqueIds.add(model.id));
      state.defaultModel = boundedText(data.default_model, 300).trim();
      elements.modelSelect.replaceChildren();

      if (state.models.length === 0) {
        const option = createElement("option", "", "No installed models found");
        option.value = "";
        elements.modelSelect.append(option);
        elements.modelMeta.textContent = "Install a model on the host, then retry.";
        elements.modelsRetry.hidden = false;
        updateSendButton();
        return;
      }

      for (const model of state.models) {
        const label = model.recommended ? `${model.name} — Recommended` : model.name;
        const option = createElement("option", "", label);
        option.value = model.id;
        elements.modelSelect.append(option);
      }

      const preferred = state.models.find((model) => model.id === priorSelection)
        || state.models.find((model) => model.id === state.defaultModel)
        || state.models.find((model) => model.recommended)
        || state.models[0];
      elements.modelSelect.value = preferred.id;
      elements.modelSelect.disabled = state.isGenerating;
      updateModelDetails();
    } catch (error) {
      state.models = [];
      elements.modelSelect.replaceChildren();
      const option = createElement("option", "", "Models unavailable");
      option.value = "";
      elements.modelSelect.append(option);
      elements.modelMeta.textContent = error instanceof DOMException && error.name === "AbortError"
        ? "Model service timed out."
        : boundedText(error instanceof Error ? error.message : "Models could not be loaded.", 250);
      elements.modelsRetry.hidden = false;
      updateSendButton();
    }
  }

  function resizePrompt() {
    elements.promptInput.style.height = "auto";
    elements.promptInput.style.height = `${Math.min(elements.promptInput.scrollHeight, 190)}px`;
  }

  function updateSendButton() {
    const hasPrompt = elements.promptInput.value.trim().length > 0;
    const hasModel = Boolean(selectedModel());
    elements.sendButton.disabled = state.isGenerating || !hasPrompt || !hasModel;
  }

  function setGenerating(isGenerating) {
    state.isGenerating = isGenerating;
    elements.messages.setAttribute("aria-busy", String(isGenerating));
    elements.modelSelect.disabled = isGenerating || state.models.length === 0;
    elements.sendButton.hidden = isGenerating;
    elements.stopButton.hidden = !isGenerating;
    elements.stopButton.disabled = false;
    updateSendButton();
  }

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    elements.sidebarToggle.setAttribute("aria-expanded", "true");
    elements.sidebarScrim.tabIndex = 0;
    elements.sidebarClose.focus();
  }

  function closeSidebar(restoreFocus = false) {
    document.body.classList.remove("sidebar-open");
    elements.sidebarToggle.setAttribute("aria-expanded", "false");
    elements.sidebarScrim.tabIndex = -1;
    if (restoreFocus) {
      elements.sidebarToggle.focus();
    }
  }

  function scrollConversation(force = false) {
    const distanceFromBottom = elements.chatScroll.scrollHeight
      - elements.chatScroll.scrollTop
      - elements.chatScroll.clientHeight;
    if (force || distanceFromBottom < 150) {
      window.requestAnimationFrame(() => {
        elements.chatScroll.scrollTop = elements.chatScroll.scrollHeight;
      });
    }
  }

  function revealMessages() {
    elements.emptyState.hidden = true;
    elements.messages.hidden = false;
  }

  function renderUserMessage(content) {
    revealMessages();
    const article = createElement("article", "message message-user");
    article.setAttribute("aria-label", "You said");
    const avatar = createElement("span", "message-avatar", "You");
    avatar.setAttribute("aria-hidden", "true");
    const body = createElement("div", "message-body");
    const label = createElement("div", "message-label", "You");
    const text = createElement("p", "message-text", content);
    body.append(label, text);
    article.append(avatar, body);
    elements.messages.append(article);
    scrollConversation(true);
  }

  function renderAssistantMessage(model) {
    const article = createElement("article", "message message-assistant is-streaming");
    article.setAttribute("aria-label", "Assistant response");
    const avatar = createElement("span", "message-avatar");
    avatar.setAttribute("aria-hidden", "true");
    const mark = document.createElement("img");
    mark.src = "mark.svg";
    mark.width = 23;
    mark.height = 23;
    mark.alt = "";
    avatar.append(mark);

    const body = createElement("div", "message-body");
    const label = createElement("div", "message-label");
    label.append(createElement("span", "", "Assistant"));
    const modelLabel = createElement("span", "message-model", model?.name || model?.id || "Local model");
    label.append(modelLabel);
    const activityList = createElement("div", "activity-list");
    activityList.setAttribute("aria-live", "polite");
    const text = createElement("p", "message-text");
    text.setAttribute("aria-live", "off");
    const textNode = document.createTextNode("");
    text.append(textNode);
    body.append(label, activityList, text);
    article.append(avatar, body);
    elements.messages.append(article);
    scrollConversation(true);

    return {
      article,
      body,
      modelLabel,
      activityList,
      statusItem: null,
      textNode,
      content: "",
    };
  }

  function setAssistantStatus(view, message) {
    const safeMessage = boundedText(message, 350).trim();
    if (!safeMessage) {
      return;
    }
    if (!view.statusItem) {
      view.statusItem = createElement("div", "activity-item is-status");
      view.statusItem.append(createElement("span", "activity-icon"), createElement("span", ""));
      view.activityList.append(view.statusItem);
    }
    view.statusItem.lastElementChild.textContent = safeMessage;
    scrollConversation();
  }

  function addToolActivity(view, payload) {
    const name = boundedText(payload.name, 120).trim() || "live tool";
    const query = boundedText(payload.query, 500).trim();
    const item = createElement("div", "activity-item");
    const icon = createElement("span", "activity-icon", "◇");
    icon.setAttribute("aria-hidden", "true");
    const description = query ? `Using ${name} — ${query}` : `Using ${name}`;
    item.append(icon, createElement("span", "", description));
    if (view.statusItem) {
      view.activityList.insertBefore(item, view.statusItem);
    } else {
      view.activityList.append(item);
    }
    scrollConversation();
  }

  function finishAssistantActivity(view) {
    if (view.statusItem) {
      view.statusItem.remove();
      view.statusItem = null;
    }
    if (!view.activityList.hasChildNodes()) {
      view.activityList.remove();
    }
    view.article.classList.remove("is-streaming");
  }

  function addMessageError(view, message) {
    const errorNote = createElement("p", "message-error-note", boundedText(message, 700));
    view.body.append(errorNote);
    scrollConversation(true);
  }

  function addStoppedNote(view) {
    view.body.append(createElement("p", "message-stopped-note", "Generation stopped. Partial output was not added to the next request’s context."));
    scrollConversation(true);
  }

  function safeExternalUrl(rawUrl) {
    if (
      typeof rawUrl !== "string"
      || rawUrl.length > 4_096
      || !/^https?:\/\//i.test(rawUrl)
    ) {
      return null;
    }
    try {
      const parsed = new URL(rawUrl);
      if (
        (parsed.protocol !== "http:" && parsed.protocol !== "https:")
        || parsed.username
        || parsed.password
      ) {
        return null;
      }
      return parsed.href;
    } catch {
      return null;
    }
  }

  function renderSources(view, sources) {
    if (!Array.isArray(sources)) {
      return;
    }
    const safeSources = sources.slice(0, 20).map((source) => {
      if (!source || typeof source !== "object") {
        return null;
      }
      const url = safeExternalUrl(source.url);
      if (!url) {
        return null;
      }
      return {
        url,
        title: boundedText(source.title, 240).trim() || new URL(url).hostname,
      };
    }).filter(Boolean);
    if (safeSources.length === 0) {
      return;
    }

    const block = createElement("section", "sources-block");
    block.setAttribute("aria-label", "Sources");
    block.append(createElement("h3", "sources-title", "Sources"));
    const list = createElement("ul", "sources-list");
    for (const source of safeSources) {
      const item = document.createElement("li");
      const link = createElement("a", "source-link");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "external noopener noreferrer";
      link.append(createElement("span", "", source.title), createElement("span", "", "↗"));
      item.append(link);
      list.append(item);
    }
    block.append(list);
    view.body.append(block);
  }

  function buildBoundedRequest(allMessages, modelId) {
    const selected = [];
    let charCount = 0;
    let wasTrimmed = false;
    for (let index = allMessages.length - 1; index >= 0; index -= 1) {
      const message = allMessages[index];
      if (!message || (message.role !== "user" && message.role !== "assistant")) {
        continue;
      }
      const rawContent = typeof message.content === "string" ? message.content : "";
      const content = boundedText(rawContent, MAX_REQUEST_MESSAGE_CHARS);
      if (!content) {
        wasTrimmed = true;
        continue;
      }
      if (content.length < rawContent.length) {
        wasTrimmed = true;
      }
      if (selected.length >= MAX_REQUEST_MESSAGES || charCount + content.length > MAX_REQUEST_CHARS) {
        wasTrimmed = true;
        break;
      }
      selected.push({ role: message.role, content });
      charCount += content.length;
    }
    selected.reverse();
    if (selected.length < allMessages.length) {
      wasTrimmed = true;
    }

    let serialized = JSON.stringify({ model: modelId, messages: selected });
    while (new TextEncoder().encode(serialized).byteLength > MAX_REQUEST_BYTES && selected.length > 1) {
      selected.shift();
      wasTrimmed = true;
      serialized = JSON.stringify({ model: modelId, messages: selected });
    }
    if (new TextEncoder().encode(serialized).byteLength > MAX_REQUEST_BYTES) {
      throw new Error("The message is too large to send safely.");
    }
    return {
      serialized,
      wasTrimmed,
    };
  }

  async function consumeEventStream(response, onEvent) {
    if (!response.body) {
      throw new Error("This browser could not open the streaming response.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let totalBytes = 0;
    let buffer = "";
    let eventName = "message";
    let dataLines = [];

    const dispatch = () => {
      if (dataLines.length === 0) {
        eventName = "message";
        return;
      }
      const rawData = dataLines.join("\n");
      dataLines = [];
      const dispatchedName = eventName;
      eventName = "message";
      if (rawData.length > MAX_SSE_EVENT_CHARS) {
        throw new Error("A streaming event exceeded the safe response limit.");
      }
      let payload;
      try {
        payload = JSON.parse(rawData);
      } catch {
        throw new Error("The model service sent an unreadable streaming event.");
      }
      const effectiveName = dispatchedName === "message" && typeof payload.type === "string"
        ? payload.type
        : dispatchedName;
      onEvent(effectiveName, payload);
    };

    const processLine = (rawLine) => {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line === "") {
        dispatch();
        return;
      }
      if (line.startsWith(":")) {
        return;
      }
      const colonIndex = line.indexOf(":");
      const field = colonIndex === -1 ? line : line.slice(0, colonIndex);
      let value = colonIndex === -1 ? "" : line.slice(colonIndex + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
      if (field === "event") {
        eventName = value.slice(0, 80);
      } else if (field === "data") {
        dataLines.push(value);
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        totalBytes += value.byteLength;
        if (totalBytes > MAX_SSE_BYTES) {
          await reader.cancel();
          throw new Error("The streaming response exceeded the safe size limit.");
        }
        buffer += decoder.decode(value, { stream: true });
        if (buffer.length > MAX_SSE_LINE_CHARS && !buffer.includes("\n")) {
          await reader.cancel();
          throw new Error("A streaming line exceeded the safe response limit.");
        }

        let newlineIndex = buffer.indexOf("\n");
        while (newlineIndex !== -1) {
          processLine(buffer.slice(0, newlineIndex));
          buffer = buffer.slice(newlineIndex + 1);
          newlineIndex = buffer.indexOf("\n");
        }
      }
      buffer += decoder.decode();
      if (buffer) {
        processLine(buffer);
      }
      dispatch();
    } catch (error) {
      await reader.cancel().catch(() => undefined);
      throw error;
    } finally {
      reader.releaseLock();
    }
  }

  function handleStreamEvent(view, eventName, payload, streamState) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    switch (eventName) {
      case "status":
        setAssistantStatus(view, payload.message);
        break;
      case "tool":
        addToolActivity(view, payload);
        break;
      case "delta": {
        const content = typeof payload.content === "string" ? payload.content : "";
        if (view.content.length + content.length > MAX_RESPONSE_CHARS) {
          throw new Error("The model response exceeded the safe display limit.");
        }
        view.content += content;
        view.textNode.appendData(content);
        scrollConversation();
        break;
      }
      case "done":
        streamState.done = true;
        if (typeof payload.model === "string" && payload.model) {
          const matchingModel = state.models.find((model) => model.id === payload.model);
          view.modelLabel.textContent = matchingModel?.name || boundedText(payload.model, 300);
        }
        renderSources(view, payload.sources);
        break;
      case "error":
        throw new StreamEventError(boundedText(payload.message, 700) || "The model service reported an error.");
      default:
        break;
    }
  }

  function friendlyChatError(error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return "The model request was interrupted.";
    }
    if (error instanceof TypeError) {
      return "The private host could not be reached. Check the connection and try again.";
    }
    return boundedText(error instanceof Error ? error.message : "The model request failed.", 700);
  }

  async function sendMessage() {
    if (state.isGenerating) {
      return;
    }
    clearNotice();
    const content = elements.promptInput.value.trim();
    const model = selectedModel();
    if (!model) {
      showNotice("Choose an installed model before sending a message.");
      elements.modelSelect.focus();
      return;
    }
    if (!content) {
      return;
    }
    if (content.length > MAX_PROMPT_CHARS) {
      showNotice(`Messages are limited to ${MAX_PROMPT_CHARS.toLocaleString()} characters.`);
      elements.promptInput.focus();
      return;
    }

    const priorMessageCount = state.messages.length;
    state.messages.push({ role: "user", content });
    let boundedRequest;
    try {
      boundedRequest = buildBoundedRequest(state.messages, model.id);
    } catch (error) {
      state.messages.pop();
      showNotice(error instanceof Error ? error.message : "The message is too large to send safely.");
      elements.promptInput.focus();
      return;
    }
    renderUserMessage(content);
    elements.promptInput.value = "";
    resizePrompt();
    const assistantView = renderAssistantMessage(model);
    setAssistantStatus(
      assistantView,
      boundedRequest.wasTrimmed ? "Sending the most recent conversation context…" : "Connecting to the local model…",
    );

    const controller = new AbortController();
    state.activeController = controller;
    state.stopRequested = false;
    state.timeoutFired = false;
    setGenerating(true);
    const timeoutId = window.setTimeout(() => {
      state.timeoutFired = true;
      controller.abort();
    }, CHAT_TIMEOUT_MS);
    const streamState = { done: false };

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "text/event-stream",
          "Content-Type": "application/json",
          "X-CSRF-Token": state.csrfToken,
        },
        body: boundedRequest.serialized,
        signal: controller.signal,
      });

      if (response.status === 401) {
        redirectToLogin();
        return;
      }
      if (!response.ok) {
        const errorData = await readJsonBounded(response, 64 * 1024).catch(() => ({}));
        throw new Error(apiError(response.status, errorData, `The model request failed (${response.status}).`));
      }
      const contentType = response.headers.get("Content-Type") || "";
      if (!contentType.toLowerCase().includes("text/event-stream")) {
        throw new Error("The model service returned a non-streaming response.");
      }

      await consumeEventStream(response, (eventName, payload) => {
        handleStreamEvent(assistantView, eventName, payload, streamState);
      });

      if (!streamState.done) {
        throw new Error("The connection closed before the model finished responding.");
      }
      if (!assistantView.content) {
        throw new Error("The model completed without returning any text.");
      }

      state.messages.push({ role: "assistant", content: assistantView.content });
      if (state.messages.length > MAX_STORED_MESSAGES) {
        state.messages = state.messages.slice(-MAX_STORED_MESSAGES);
      }
      elements.streamAnnouncement.textContent = "Assistant response complete.";
    } catch (error) {
      state.messages = state.messages.slice(0, priorMessageCount + 1);
      if (state.messages.length > MAX_STORED_MESSAGES) {
        state.messages = state.messages.slice(-MAX_STORED_MESSAGES);
      }
      if (state.stopRequested) {
        addStoppedNote(assistantView);
        elements.streamAnnouncement.textContent = "Assistant generation stopped.";
      } else if (state.timeoutFired) {
        addMessageError(assistantView, "The request reached the 20-minute safety limit and was stopped.");
        elements.streamAnnouncement.textContent = "Assistant request timed out.";
      } else {
        addMessageError(assistantView, friendlyChatError(error));
        elements.streamAnnouncement.textContent = "Assistant request failed.";
      }
    } finally {
      window.clearTimeout(timeoutId);
      finishAssistantActivity(assistantView);
      if (state.activeController === controller) {
        state.activeController = null;
      }
      state.stopRequested = false;
      state.timeoutFired = false;
      setGenerating(false);
      elements.promptInput.focus();
    }
  }

  function stopGeneration() {
    if (!state.activeController) {
      return;
    }
    state.stopRequested = true;
    elements.stopButton.disabled = true;
    state.activeController.abort();
  }

  function newConversation() {
    if (state.activeController) {
      state.stopRequested = true;
      state.activeController.abort();
    }
    state.messages = [];
    elements.messages.replaceChildren();
    elements.messages.hidden = true;
    elements.emptyState.hidden = false;
    elements.streamAnnouncement.textContent = "New conversation ready.";
    elements.promptInput.value = "";
    resizePrompt();
    clearNotice();
    closeSidebar(false);
    elements.promptInput.focus();
    updateSendButton();
  }

  function clearPasswordForm() {
    elements.passwordForm.reset();
    for (const input of [elements.currentPassword, elements.newPassword, elements.confirmPassword]) {
      input.removeAttribute("aria-invalid");
      input.setCustomValidity("");
    }
    elements.passwordMessage.textContent = "";
    elements.passwordMessage.hidden = true;
  }

  function setPasswordBusy(isBusy) {
    state.passwordBusy = isBusy;
    for (const control of [
      elements.currentPassword,
      elements.newPassword,
      elements.confirmPassword,
      elements.passwordDialogClose,
      elements.passwordCancel,
      elements.passwordSubmit,
    ]) {
      control.disabled = isBusy;
    }
    elements.passwordSubmit.classList.toggle("is-loading", isBusy);
    elements.passwordSubmit.setAttribute("aria-busy", String(isBusy));
    elements.passwordSubmit.querySelector(".button-label").textContent = isBusy
      ? "Changing…"
      : "Change password";
  }

  function showPasswordMessage(text) {
    elements.passwordMessage.textContent = boundedText(text, 600);
    elements.passwordMessage.hidden = false;
  }

  function openPasswordDialog() {
    clearPasswordForm();
    closeSidebar(false);
    if (typeof elements.passwordDialog.showModal !== "function") {
      showNotice("This browser cannot open the password form. Update it before changing account credentials.");
      return;
    }
    elements.passwordDialog.showModal();
    window.requestAnimationFrame(() => elements.currentPassword.focus());
  }

  function closePasswordDialog() {
    if (state.passwordBusy) {
      return;
    }
    clearPasswordForm();
    if (elements.passwordDialog.open) {
      elements.passwordDialog.close();
    }
    elements.changePasswordButton.focus();
  }

  async function changePassword() {
    for (const input of [elements.currentPassword, elements.newPassword, elements.confirmPassword]) {
      input.removeAttribute("aria-invalid");
      input.setCustomValidity("");
    }
    elements.passwordMessage.hidden = true;

    if (!elements.passwordForm.reportValidity()) {
      return;
    }
    if (elements.newPassword.value !== elements.confirmPassword.value) {
      elements.confirmPassword.setAttribute("aria-invalid", "true");
      elements.confirmPassword.setCustomValidity("The new passwords do not match.");
      showPasswordMessage("The new passwords do not match.");
      elements.confirmPassword.focus();
      return;
    }

    setPasswordBusy(true);
    try {
      const response = await fetchWithTimeout("/api/auth/change-password", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": state.csrfToken,
        },
        body: JSON.stringify({
          current_password: elements.currentPassword.value,
          new_password: elements.newPassword.value,
        }),
      });

      if (!response.ok) {
        const data = await readJsonBounded(response, 32 * 1024).catch(() => ({}));
        if (response.status === 401 && data.detail !== "Current password is incorrect") {
          clearPasswordForm();
          redirectToLogin();
          return;
        }
        if (response.status === 401 && data.detail === "Current password is incorrect") {
          throw new Error("The current password is incorrect.");
        }
        throw new Error(apiError(response.status, data, "The password could not be changed."));
      }

      state.csrfToken = "";
      state.messages = [];
      clearPasswordForm();
      redirectToLogin();
    } catch (error) {
      clearPasswordForm();
      showPasswordMessage(error instanceof DOMException && error.name === "AbortError"
        ? "The password-change request timed out. No success was confirmed."
        : friendlyChatError(error));
      setPasswordBusy(false);
      elements.currentPassword.focus();
    }
  }

  async function logout() {
    if (state.activeController) {
      state.stopRequested = true;
      state.activeController.abort();
    }
    elements.logoutButton.disabled = true;
    elements.logoutButton.textContent = "Signing out…";
    try {
      const response = await fetchWithTimeout("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Accept": "application/json",
          "X-CSRF-Token": state.csrfToken,
        },
      });
      if (!response.ok && response.status !== 401) {
        const data = await readJsonBounded(response, 32 * 1024).catch(() => ({}));
        throw new Error(apiError(response.status, data, "Sign-out failed. Please try again."));
      }
      state.csrfToken = "";
      state.messages = [];
      redirectToLogin();
    } catch (error) {
      elements.logoutButton.disabled = false;
      elements.logoutButton.textContent = "Sign out";
      closeSidebar(false);
      showNotice(error instanceof DOMException && error.name === "AbortError"
        ? "The sign-out request timed out."
        : friendlyChatError(error));
    }
  }

  function bindEvents() {
    elements.modelSelect.addEventListener("change", updateModelDetails);
    elements.modelsRetry.addEventListener("click", () => void loadModels());
    elements.promptInput.addEventListener("input", () => {
      resizePrompt();
      updateSendButton();
      clearNotice();
    });
    elements.promptInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (!elements.sendButton.disabled && !state.isGenerating) {
          elements.composerForm.requestSubmit();
        }
      }
    });
    elements.composerForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void sendMessage();
    });
    elements.stopButton.addEventListener("click", stopGeneration);
    elements.newChatButton.addEventListener("click", newConversation);
    elements.sidebarToggle.addEventListener("click", openSidebar);
    elements.sidebarClose.addEventListener("click", () => closeSidebar(true));
    elements.sidebarScrim.addEventListener("click", () => closeSidebar(true));
    elements.changePasswordButton.addEventListener("click", openPasswordDialog);
    elements.logoutButton.addEventListener("click", () => void logout());
    elements.passwordDialogClose.addEventListener("click", closePasswordDialog);
    elements.passwordCancel.addEventListener("click", closePasswordDialog);
    elements.passwordDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closePasswordDialog();
    });
    elements.passwordForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void changePassword();
    });
    elements.confirmPassword.addEventListener("input", () => {
      elements.confirmPassword.removeAttribute("aria-invalid");
      elements.confirmPassword.setCustomValidity("");
      elements.passwordMessage.hidden = true;
    });

    for (const suggestion of document.querySelectorAll("[data-prompt]")) {
      suggestion.addEventListener("click", () => {
        elements.promptInput.value = boundedText(suggestion.dataset.prompt, MAX_PROMPT_CHARS);
        resizePrompt();
        updateSendButton();
        elements.promptInput.focus();
      });
    }

    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        if (elements.passwordDialog.open) {
          return;
        }
        event.preventDefault();
        newConversation();
        return;
      }
      if (event.key === "Escape") {
        if (elements.passwordDialog.open) {
          return;
        }
        if (document.body.classList.contains("sidebar-open")) {
          closeSidebar(true);
        } else if (state.isGenerating) {
          stopGeneration();
        }
      }
    });

    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 781px)").matches) {
        closeSidebar(false);
      }
    });
  }

  async function initialize() {
    bindEvents();
    resizePrompt();
    try {
      const authenticated = await loadSession();
      if (!authenticated) {
        return;
      }
      elements.startupScreen.hidden = true;
      elements.appShell.hidden = false;
      elements.promptInput.focus();
      await loadModels();
    } catch (error) {
      const message = error instanceof DOMException && error.name === "AbortError"
        ? "The private host did not respond in time."
        : (error instanceof Error ? error.message : "The private workspace could not be opened.");
      showFatalStartup(message);
    }
  }

  void initialize();
})();
