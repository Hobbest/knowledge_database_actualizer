const {
  Plugin,
  Notice,
  ItemView,
  Setting,
  MarkdownView,
  PluginSettingTab,
  requestUrl,
  FileSystemAdapter,
  Modal,
} = require("obsidian");
const http = require("http");
const https = require("https");
const { URL } = require("url");

// Obsidian only loads main.js and cannot resolve require("./core"). Keep core.js for
// contract tests; mirror any changes here until a bundler step is added.
function parseAppendTarget(target) {
  if (!target) {
    return { path: "", heading: null };
  }
  const raw = String(target).trim();
  const hashIndex = raw.indexOf("#");
  if (hashIndex < 0) {
    return { path: raw, heading: null };
  }
  return {
    path: raw.slice(0, hashIndex).trim(),
    heading: raw.slice(hashIndex + 1).trim().replace(/^#+/, "").trim() || null,
  };
}

function buildApplyNote(item, overwrite = false) {
  const mode = item.write_mode === "append" ? "append" : "write";
  const rawTarget = mode === "append" && item.append_target ? item.append_target : item.note_path;
  const { path, heading } = parseAppendTarget(rawTarget);
  return {
    note_path: String(path || item.note_path || "").trim(),
    content: String(item.content || ""),
    mode,
    overwrite: Boolean(overwrite),
    append_heading: mode === "append" ? item.append_heading || heading || null : null,
  };
}

function buildSelectedApplyNotes(suggestions, selectedIndexes, overwrite = false) {
  return Array.from(selectedIndexes)
    .map((index) => suggestions[index])
    .filter(Boolean)
    .map((item) => buildApplyNote(item, overwrite));
}

function suggestionPageCount(total, pageSize) {
  if (pageSize === "all" || !pageSize || pageSize < 1) {
    return 1;
  }
  return Math.max(1, Math.ceil(Number(total) / pageSize));
}

function suggestionPageSlice(total, page, pageSize) {
  const count = Math.max(0, Number(total) || 0);
  if (pageSize === "all" || !pageSize || pageSize < 1) {
    return { start: 0, end: count, page: 1, pages: 1 };
  }
  const pages = suggestionPageCount(count, pageSize);
  const current = Math.min(Math.max(1, Number(page) || 1), pages);
  const start = (current - 1) * pageSize;
  const end = Math.min(count, start + pageSize);
  return { start, end, page: current, pages };
}

function isAnalyzeAbortError(error) {
  if (!error) {
    return false;
  }
  if (error.name === "AbortError") {
    return true;
  }
  return /analysis canceled|The operation was aborted|aborted/i.test(
    String(error.message || error)
  );
}

function makeAnalyzeAbortError() {
  const err = new Error("Analysis canceled");
  err.name = "AbortError";
  return err;
}

function editorMarkdown(view, selectionOnly = false) {
  const editor = view && view.editor;
  if (!editor) {
    return "";
  }
  if (selectionOnly) {
    return String(editor.getSelection ? editor.getSelection() : "");
  }
  return String(editor.getValue ? editor.getValue() : "");
}

function consumeNdjsonLines(buffer, onEvent) {
  let rest = buffer;
  let newlineIndex;
  while ((newlineIndex = rest.indexOf("\n")) >= 0) {
    const line = rest.slice(0, newlineIndex).trim();
    rest = rest.slice(newlineIndex + 1);
    if (line) {
      onEvent(JSON.parse(line));
    }
  }
  return rest;
}

function parseNdjsonStream(text, onEvent) {
  let result = null;
  let streamError = null;
  let partialSuggestions = [];
  const handleEvent = (event) => {
    if (onEvent) {
      onEvent(event);
    }
    if (event.type === "result") {
      result = event;
    } else if (event.type === "error") {
      streamError = new Error(event.message || "Analyze failed");
      partialSuggestions = event.partial_suggestions || [];
    }
  };

  let buffer = consumeNdjsonLines(String(text || ""), handleEvent);
  if (buffer.trim()) {
    consumeNdjsonLines(`${buffer}\n`, handleEvent);
  }
  if (streamError) {
    if (partialSuggestions.length) {
      streamError.partialSuggestions = partialSuggestions;
    }
    throw streamError;
  }
  if (!result) {
    throw new Error("No result from analyze stream");
  }
  return result;
}

const VIEW_TYPE = "actualizer-sidebar";
const DEFAULT_API = "http://127.0.0.1:8000";

const DEFAULT_SETTINGS = {
  apiBaseUrl: DEFAULT_API,
  apiToken: "",
  openNotesAfterWrite: true,
};

function computeProgress(event) {
  const total = event.total || 0;
  const current = event.current || 0;
  const ratio = total > 0 ? Math.min(1, current / total) : 0;
  switch (event.stage) {
    case "loading":
      return 5;
    case "novelty":
      return 15;
    case "scoring":
      return 15 + ratio * 25;
    case "drafting":
      return 40 + ratio * 60;
    default:
      return total > 0 ? ratio * 100 : 2;
  }
}

function enableNestedScroll(element) {
  if (!element || element.dataset.actualizerNestedScroll === "1") {
    return;
  }
  element.dataset.actualizerNestedScroll = "1";
  element.addEventListener(
    "wheel",
    (event) => {
      const { deltaY } = event;
      if (!deltaY) {
        return;
      }
      const { scrollTop, scrollHeight, clientHeight } = element;
      const canScrollUp = scrollTop > 0;
      const canScrollDown = scrollTop + clientHeight < scrollHeight - 1;
      if ((deltaY < 0 && canScrollUp) || (deltaY > 0 && canScrollDown)) {
        event.stopPropagation();
      }
    },
    { passive: true }
  );
}

function enableScrollContainer(element) {
  if (!element || element.dataset.actualizerScrollContainer === "1") {
    return;
  }
  element.dataset.actualizerScrollContainer = "1";
  element.addEventListener(
    "wheel",
    (event) => {
      const { deltaY } = event;
      if (!deltaY || element.scrollHeight <= element.clientHeight + 1) {
        return;
      }
      const maxScroll = element.scrollHeight - element.clientHeight;
      const nextScroll = Math.max(0, Math.min(maxScroll, element.scrollTop + deltaY));
      if (nextScroll !== element.scrollTop) {
        element.scrollTop = nextScroll;
        event.preventDefault();
        event.stopPropagation();
      }
    },
    { passive: false }
  );
}

function concatUint8Arrays(chunks) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out.buffer;
}

class ActualizerConfirmModal extends Modal {
  constructor(app, { title, message, confirmText = "Confirm" }, resolve) {
    super(app);
    this.details = { title, message, confirmText };
    this.resolve = resolve;
    this.settled = false;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl("h3", { text: this.details.title });
    contentEl.createEl("p", {
      cls: "actualizer-confirm-message",
      text: this.details.message,
    });
    const actions = contentEl.createEl("div", { cls: "modal-button-container" });
    actions.createEl("button", { text: "Cancel" }).onclick = () => this.finish(false);
    actions
      .createEl("button", { text: this.details.confirmText, cls: "mod-warning" })
      .onclick = () => this.finish(true);
  }

  onClose() {
    if (!this.settled) {
      this.settled = true;
      this.resolve(false);
    }
    this.contentEl.empty();
  }

  finish(value) {
    if (!this.settled) {
      this.settled = true;
      this.resolve(value);
    }
    this.close();
  }
}

async function buildMultipartBody({ url, fileBlob, fileName, resume, vaultNotePath }) {
  const boundary = `----Actualizer${Date.now().toString(16)}`;
  const chunks = [];
  const encoder = new TextEncoder();
  const addText = (text) => chunks.push(encoder.encode(text));

  const addField = (name, value) => {
    addText(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`);
  };

  if (url) {
    addField("url", url);
  }
  if (resume) {
    addField("resume", "true");
  }
  if (vaultNotePath) {
    addField("vault_note_path", vaultNotePath);
  }
  if (fileBlob) {
    const buffer = await fileBlob.arrayBuffer();
    const filename = fileName || "upload.md";
    const type = fileBlob.type || "application/octet-stream";
    addText(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: ${type}\r\n\r\n`
    );
    chunks.push(new Uint8Array(buffer));
    addText("\r\n");
  }
  addText(`--${boundary}--\r\n`);
  return {
    body: concatUint8Arrays(chunks),
    contentType: `multipart/form-data; boundary=${boundary}`,
  };
}

class ActualizerApi {
  constructor(plugin) {
    this.plugin = plugin;
  }

  apiBase() {
    return this.plugin.settings.apiBaseUrl.replace(/\/$/, "") || DEFAULT_API;
  }

  authHeaders() {
    const headers = {};
    const token = this.plugin.settings.apiToken.trim();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  }

  connectionError(error, url) {
    const hint =
      "Start the server with: uvicorn app.main:app --reload " +
      `(expected at ${this.apiBase()})`;
    const message = error?.message || String(error);
    if (/failed to fetch|network|ECONNREFUSED|ENOTFOUND|ETIMEDOUT/i.test(message)) {
      return new Error(`Cannot reach Actualizer API. ${hint}`);
    }
    return new Error(`${message} (${url})`);
  }

  async request(path, options = {}) {
    const url = `${this.apiBase()}${path}`;
    const method = options.method || "GET";
    const headers = { ...this.authHeaders(), ...(options.headers || {}) };
    const req = { url, method, headers, throw: false };

    if (options.body !== undefined) {
      req.body = options.body;
      if (options.contentType) {
        req.contentType = options.contentType;
      } else if (options.json) {
        req.contentType = "application/json";
      }
    }

    let response;
    try {
      response = await requestUrl(req);
    } catch (error) {
      throw this.connectionError(error, url);
    }

    if (response.status === 401) {
      throw new Error("API token required or invalid (401)");
    }
    if (response.status >= 400) {
      let detail = String(response.status);
      try {
        detail = response.json?.detail || response.text || detail;
      } catch (_) {
        /* ignore */
      }
      throw new Error(String(detail));
    }
    if (options.rawText) {
      return response.text;
    }
    return response.json;
  }

  status() {
    return this.request("/api/status");
  }

  getCheckpoint() {
    return this.request("/api/suggestions/checkpoint");
  }

  indexVault(vaultPath, ifStale = false) {
    return this.request("/api/vault/index", {
      method: "POST",
      json: true,
      body: JSON.stringify({ vault_path: vaultPath, if_stale: ifStale }),
    });
  }

  applyBatch(vaultPath, notes) {
    return this.request("/api/suggestions/apply-batch", {
      method: "POST",
      json: true,
      body: JSON.stringify({ vault_path: vaultPath, notes }),
    });
  }

  previewSuggestion(vaultPath, note) {
    return this.request("/api/suggestions/preview", {
      method: "POST",
      json: true,
      body: JSON.stringify({ vault_path: vaultPath, ...note }),
    });
  }

  setVaultWatch(enabled) {
    return this.request("/api/vault/watch", {
      method: "POST",
      json: true,
      body: JSON.stringify({ enabled }),
    });
  }

  calibrateThresholds() {
    return this.request("/api/vault/thresholds/calibrate");
  }

  async analyze({
    url,
    fileBlob,
    fileName,
    resume = false,
    vaultNotePath,
    onEvent,
    signal,
  }) {
    const multipart = await buildMultipartBody({ url, fileBlob, fileName, resume, vaultNotePath });
    const apiUrl = `${this.apiBase()}/api/sources/analyze`;
    const body = Buffer.from(multipart.body);
    const headers = {
      ...this.authHeaders(),
      "Content-Type": multipart.contentType,
      "Content-Length": String(body.length),
    };

    if (signal?.aborted) {
      throw makeAnalyzeAbortError();
    }

    try {
      return await this._analyzeStreamNode(apiUrl, headers, body, onEvent, signal);
    } catch (err) {
      if (isAnalyzeAbortError(err) || signal?.aborted) {
        throw isAnalyzeAbortError(err) ? err : makeAnalyzeAbortError();
      }
      // Fall back to buffered requestUrl (no live progress events).
      // Cancel stops waiting; the underlying requestUrl call may still finish.
      const requestPromise = this.request("/api/sources/analyze", {
        method: "POST",
        body: multipart.body,
        contentType: multipart.contentType,
        rawText: true,
      });
      let text;
      if (!signal) {
        text = await requestPromise;
      } else {
        text = await Promise.race([
          requestPromise,
          new Promise((_, reject) => {
            if (signal.aborted) {
              reject(makeAnalyzeAbortError());
              return;
            }
            signal.addEventListener(
              "abort",
              () => reject(makeAnalyzeAbortError()),
              { once: true }
            );
          }),
        ]);
      }
      if (signal?.aborted) {
        throw makeAnalyzeAbortError();
      }
      return parseNdjsonStream(text, onEvent);
    }
  }

  _analyzeStreamNode(apiUrl, headers, body, onEvent, signal) {
    return new Promise((resolve, reject) => {
      const parsed = new URL(apiUrl);
      const transport = parsed.protocol === "https:" ? https : http;
      let buffer = "";
      let result = null;
      let streamError = null;
      let partialSuggestions = [];
      let settled = false;
      let req = null;

      const settle = (fn, value) => {
        if (settled) {
          return;
        }
        settled = true;
        if (signal) {
          signal.removeEventListener("abort", onAbort);
        }
        fn(value);
      };

      const onAbort = () => {
        try {
          req?.destroy();
        } catch (_) {
          /* ignore */
        }
        settle(reject, makeAnalyzeAbortError());
      };

      if (signal?.aborted) {
        settle(reject, makeAnalyzeAbortError());
        return;
      }

      const finish = () => {
        if (streamError) {
          if (partialSuggestions.length) {
            streamError.partialSuggestions = partialSuggestions;
          }
          settle(reject, streamError);
          return;
        }
        if (!result) {
          settle(reject, new Error("No result from analyze stream"));
          return;
        }
        settle(resolve, result);
      };

      const handleEvent = (event) => {
        if (onEvent) {
          onEvent(event);
        }
        if (event.type === "result") {
          result = event;
        }
        if (event.type === "error") {
          streamError = new Error(event.message || "Analyze failed");
          partialSuggestions = event.partial_suggestions || [];
        }
      };

      req = transport.request(
        {
          hostname: parsed.hostname,
          port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
          path: `${parsed.pathname}${parsed.search}`,
          method: "POST",
          headers,
        },
        (res) => {
          if (signal?.aborted) {
            res.resume();
            settle(reject, makeAnalyzeAbortError());
            return;
          }
          if (res.statusCode === 401) {
            res.resume();
            settle(reject, new Error("API token required or invalid (401)"));
            return;
          }
          if (res.statusCode >= 400) {
            let raw = "";
            res.setEncoding("utf8");
            res.on("data", (chunk) => {
              raw += chunk;
            });
            res.on("end", () => {
              try {
                const payload = JSON.parse(raw);
                settle(reject, new Error(payload.detail || raw || String(res.statusCode)));
              } catch (_) {
                settle(reject, new Error(raw || String(res.statusCode)));
              }
            });
            return;
          }

          res.setEncoding("utf8");
          res.on("data", (chunk) => {
            if (signal?.aborted) {
              return;
            }
            buffer = consumeNdjsonLines(buffer + chunk, handleEvent);
          });
          res.on("end", () => {
            if (signal?.aborted) {
              settle(reject, makeAnalyzeAbortError());
              return;
            }
            if (buffer.trim()) {
              consumeNdjsonLines(`${buffer}\n`, handleEvent);
            }
            finish();
          });
        }
      );

      if (signal) {
        signal.addEventListener("abort", onAbort);
      }

      req.on("error", (err) => {
        if (signal?.aborted || isAnalyzeAbortError(err)) {
          settle(reject, makeAnalyzeAbortError());
          return;
        }
        settle(reject, this.connectionError(err, apiUrl));
      });
      req.write(body);
      req.end();
    });
  }
}

class ActualizerView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.result = null;
    this.suggestions = [];
    this.selected = new Set();
    this.expanded = new Set();
    this.progress = "";
    this.progressEvent = null;
    this.isAnalyzing = false;
    this.warnings = [];
    this.writeStatus = "";
    this.analyzeError = "";
    this.sourceUrl = "";
    this.pendingFile = null;
    this.serverStatus = null;
    this.calibration = null;
    this.checkpointState = null;
    this.liveDraftNotes = [];
    this.currentPage = 1;
    this.pageSize = 10;
    this._cancelPending = false;
    this._ui = {};
  }

  getViewType() {
    return VIEW_TYPE;
  }

  getDisplayText() {
    return "Actualizer";
  }

  getIcon() {
    return "search-check";
  }

  async onOpen() {
    this.containerEl.addClass("actualizer-view-root");
    await this.refreshCheckpointState();
    await this.refreshServerStatus();
    this.prefillSourceFromContext();
    this.render();
  }

  async onClose() {
    this.containerEl.removeClass("actualizer-view-root");
  }

  prefillSourceFromContext() {
    const ctx = this.plugin.lastAnalyzeContext;
    if (!ctx) {
      return;
    }
    if (ctx.kind === "url" && ctx.url) {
      this.sourceUrl = ctx.url;
    }
  }

  async refreshServerStatus() {
    try {
      this.serverStatus = await this.plugin.api.status();
    } catch (_) {
      this.serverStatus = null;
    }
  }

  setProgress(message, event = null) {
    this.progress = message || "";
    this.progressEvent = event;
    if (!this._ui.progressBar || !this._ui.progressBar.isConnected) {
      this.render();
      return;
    }
    this.updateProgressDisplay();
  }

  updateProgressDisplay() {
    if (!this._ui.progressBar) {
      return;
    }
    if (!this.isAnalyzing && !this.progress) {
      this._ui.progressBox?.remove();
      this._ui.progressBar = null;
      return;
    }
    this._ui.progressMessage?.setText(this.progress || "Working...");
    if (this._ui.progressCount) {
      if (this.progressEvent?.total > 0) {
        this._ui.progressCount.setText(`${this.progressEvent.current}/${this.progressEvent.total}`);
      } else {
        this._ui.progressCount.setText("");
      }
    }
    this._ui.progressBar.style.width = `${this.progressPercent()}%`;
  }

  clearResult() {
    this.result = null;
    this.suggestions = [];
    this.selected = new Set();
    this.liveDraftNotes = [];
    this.currentPage = 1;
  }

  applyLiveDrafts(rawSuggestions) {
    const mapped = (rawSuggestions || []).map((item) => ({
      ...item,
      write_mode: item.write_mode || "write",
    }));
    const prevKey = this.liveDraftNotes
      .map((note) => note.concept_title || note.note_path || "")
      .join("\0");
    const nextKey = mapped
      .map((note) => note.concept_title || note.note_path || "")
      .join("\0");
    if (prevKey === nextKey && mapped.length === this.liveDraftNotes.length) {
      return;
    }
    this.liveDraftNotes = mapped;
    if (!this._ui.liveNotesHost || !this._ui.liveNotesHost.isConnected) {
      this.render();
      return;
    }
    if (this._ui.liveDraftCount) {
      this._ui.liveDraftCount.setText(
        mapped.length
          ? `${mapped.length} note(s) drafted so far`
          : "Waiting for first note..."
      );
    }
    this.renderLiveDraftList(this._ui.liveNotesHost);
  }

  progressPercent() {
    if (this.progressEvent) {
      return computeProgress(this.progressEvent);
    }
    return this.isAnalyzing ? 2 : 0;
  }

  setResult(result) {
    this.result = result;
    this.isAnalyzing = false;
    this.progress = "";
    this.progressEvent = null;
    this.liveDraftNotes = [];
    if (!result) {
      this.suggestions = [];
      this.selected = new Set();
      this.render();
      return;
    }
    this.warnings = result.warnings || [];
    this.suggestions = (result.suggestions || []).map((item) => ({
      ...item,
      write_mode: item.write_mode || "write",
    }));
    this.selected = new Set(
      this.suggestions
        .map((item, index) =>
          item.is_moc || item.is_novel === false ? null : index
        )
        .filter((index) => index !== null)
    );
    this.expanded = new Set();
    this.currentPage = 1;
    this.render();
  }

  async refreshCheckpointState() {
    try {
      const data = await this.plugin.api.getCheckpoint();
      const saved = (data && data.suggestions) || [];
      const resumable =
        data && data.exists && !data.completed && saved.length > 0;
      this.checkpointState = {
        resumable,
        recoverable: Boolean(data && data.exists && saved.length),
        label: (data.source && data.source.title) || "last source",
        savedCount: saved.length,
        completed: Boolean(data && data.completed),
      };
    } catch (_) {
      this.checkpointState = null;
    }
  }

  verdictClass(verdict) {
    if (verdict === "Already known") {
      return "known";
    }
    if (verdict === "Partially new") {
      return "partial";
    }
    return "novel";
  }

  renderIdleActions(container) {
    // Kept for compatibility; analyze controls live in renderAnalyzeSource.
    this.renderAnalyzeSource(container);
  }

  renderAnalyzeSource(container) {
    const section = container.createEl("div", { cls: "actualizer-analyze-section" });
    section.createEl("h4", { text: "Analyze source" });

    if (this.serverStatus) {
      const stale = this.serverStatus.stale_note_count || 0;
      const chunks = this.serverStatus.indexed_chunks || 0;
      const statusText =
        stale > 0
          ? `${chunks} indexed chunks · ${stale} stale note(s) — re-index recommended`
          : `${chunks} indexed chunks · index fresh`;
      section.createEl("div", { cls: "actualizer-muted actualizer-status-line", text: statusText });
      const mismatch = this.plugin.vaultPathMismatch(this.serverStatus);
      if (mismatch) {
        section.createEl("div", { cls: "actualizer-warnings", text: mismatch });
      }
      const watch = this.serverStatus.vault_watch || {};
      section.createEl("div", {
        cls: "actualizer-muted actualizer-status-line",
        text: `Vault watch: ${watch.active ? "active" : watch.enabled ? "enabled, not active" : "off"}`,
      });
    }

    const urlField = section.createEl("div", { cls: "actualizer-field" });
    urlField.createEl("label", { text: "Source URL (YouTube or web article)" });
    const urlInput = urlField.createEl("input", {
      cls: "actualizer-input",
      type: "text",
      placeholder: "https://youtube.com/watch?v=... or https://example.com/article",
    });
    urlInput.value = this.sourceUrl || "";
    urlInput.oninput = () => {
      this.sourceUrl = urlInput.value;
      this.analyzeError = "";
    };

    const fileField = section.createEl("div", { cls: "actualizer-field" });
    fileField.createEl("label", { text: "Or upload file (txt, md, pdf, epub, docx)" });
    const fileRow = fileField.createEl("div", { cls: "actualizer-file-row" });
    const fileInput = fileRow.createEl("input", {
      type: "file",
      cls: "actualizer-file-input",
    });
    fileInput.setAttr("accept", ".txt,.md,.markdown,.pdf,.epub,.docx");
    fileInput.onchange = () => {
      const picked = fileInput.files?.[0];
      this.pendingFile = picked ? { name: picked.name, blob: picked } : null;
      this.analyzeError = "";
      fileNameEl.setText(
        this.pendingFile ? this.pendingFile.name : "No file chosen"
      );
    };
    const fileNameEl = fileRow.createEl("span", {
      cls: "actualizer-muted actualizer-file-name",
      text: this.pendingFile ? this.pendingFile.name : "No file chosen",
    });

    const quickRow = section.createEl("div", { cls: "actualizer-actions actualizer-quick-actions" });
    quickRow.createEl("button", { text: "Current note" }).onclick = () =>
      this.plugin.analyzeCurrentFile();
    quickRow.createEl("button", { text: "Selection" }).onclick = () =>
      this.plugin.analyzeSelection();
    quickRow.createEl("button", { text: "Clipboard URL" }).onclick = () =>
      this.plugin.analyzeClipboardUrl();

    const actions = section.createEl("div", { cls: "actualizer-actions" });
    const analyzeBtn = actions.createEl("button", {
      text: this.isAnalyzing ? "Analyzing..." : "Analyze",
      cls: "mod-cta",
    });
    analyzeBtn.disabled = this.isAnalyzing;
    analyzeBtn.onclick = () => this.startAnalyze({ resume: false });

    if (this.isAnalyzing) {
      const cancelBtn = actions.createEl("button", {
        text: this._cancelPending ? "Canceling…" : "Cancel analysis",
      });
      cancelBtn.disabled = this._cancelPending;
      cancelBtn.onclick = () => this.plugin.cancelAnalyze();
    }

    actions.createEl("button", { text: "Update index" }).onclick = () =>
      this.plugin.indexVault();
    const watchEnabled = Boolean(this.serverStatus?.vault_watch?.enabled);
    actions.createEl("button", {
      text: watchEnabled ? "Disable vault watch" : "Enable vault watch",
    }).onclick = () => this.toggleVaultWatch(!watchEnabled);
    actions.createEl("button", { text: "Calibrate thresholds" }).onclick = () =>
      this.calibrateThresholds();

    if (this.checkpointState?.recoverable) {
      actions.createEl("button", { text: "Recover saved notes" }).onclick = () =>
        this.recoverCheckpoint();
    }

    if (this.checkpointState?.resumable) {
      const continueBtn = actions.createEl("button", {
        text: `Continue run (${this.checkpointState.savedCount} saved)`,
      });
      continueBtn.disabled = this.isAnalyzing;
      continueBtn.onclick = () => this.startAnalyze({ resume: true });
    }

    if (this.analyzeError) {
      section.createEl("div", { cls: "actualizer-error", text: this.analyzeError });
    }
    if (this.calibration) {
      const result = this.calibration;
      section.createEl("div", {
        cls: "actualizer-muted actualizer-calibration",
        text:
          `${result.message} Recommended novel ${result.recommended_novel_threshold}, ` +
          `known ${result.recommended_known_threshold} (${result.sample_size} samples).`,
      });
    }
  }

  async toggleVaultWatch(enabled) {
    try {
      const watch = await this.plugin.api.setVaultWatch(enabled);
      await this.refreshServerStatus();
      this.render();
      new Notice(`Vault watch ${watch.active ? "enabled" : "disabled"}`);
    } catch (err) {
      new Notice(`Vault watch failed: ${err.message}`);
    }
  }

  async calibrateThresholds() {
    try {
      this.calibration = await this.plugin.api.calibrateThresholds();
      this.render();
    } catch (err) {
      new Notice(`Threshold calibration failed: ${err.message}`);
    }
  }

  renderProgressBar(container) {
    if (!this.isAnalyzing && !this.progress) {
      return;
    }
    const box = container.createEl("div", { cls: "actualizer-progress-box" });
    const row = box.createEl("div", { cls: "actualizer-progress-row" });
    const messageEl = row.createEl("span", {
      cls: "actualizer-progress-message",
      text: this.progress || "Working...",
    });
    const countEl = row.createEl("span", { cls: "actualizer-muted actualizer-progress-count" });
    if (this.progressEvent?.total > 0) {
      countEl.setText(`${this.progressEvent.current}/${this.progressEvent.total}`);
    }
    const track = box.createEl("div", { cls: "actualizer-progress-track" });
    const bar = track.createEl("div", { cls: "actualizer-progress-bar" });
    bar.style.width = `${this.progressPercent()}%`;

    this._ui.progressBox = box;
    this._ui.progressMessage = messageEl;
    this._ui.progressCount = countEl;
    this._ui.progressBar = bar;
  }

  renderLiveDraftList(host) {
    if (!host) {
      return;
    }
    host.empty();
    const notes = this.liveDraftNotes || [];
    if (!notes.length) {
      const waiting =
        this.progressEvent?.stage === "drafting"
          ? "Drafting first note..."
          : "Notes will appear here during drafting.";
      host.createEl("div", { cls: "actualizer-muted actualizer-live-empty", text: waiting });
      return;
    }
    for (const note of notes) {
      const item = host.createEl("div", { cls: "actualizer-live-item" });
      item.createEl("div", {
        cls: "actualizer-live-title",
        text: note.concept_title || note.note_path || "Untitled",
      });
      if (note.note_path) {
        item.createEl("div", { cls: "actualizer-muted", text: note.note_path });
      }
    }
  }

  renderLiveDraftsSection(container) {
    const section = container.createEl("div", { cls: "actualizer-live-drafts" });
    section.createEl("h4", { text: "Drafting notes" });
    const count = this.liveDraftNotes.length;
    const countEl = section.createEl("div", {
      cls: "actualizer-muted actualizer-live-count",
      text: count ? `${count} note(s) drafted so far` : "Waiting for first note...",
    });
    const listHost = section.createEl("div", { cls: "actualizer-live-list" });
    enableNestedScroll(listHost);
    this._ui.liveDraftsSection = section;
    this._ui.liveDraftCount = countEl;
    this._ui.liveNotesHost = listHost;
    this.renderLiveDraftList(listHost);
  }

  buildAnalyzePayload({ resume = false } = {}) {
    const url = (this.sourceUrl || "").trim();
    const file = this.pendingFile;

    if (url) {
      return {
        payload: { url },
        context: { kind: "url", url },
      };
    }

    if (file) {
      const ctx = this.plugin.lastAnalyzeContext;
      if (
        resume &&
        ctx?.kind === "upload" &&
        ctx.fileName &&
        ctx.fileName !== file.name
      ) {
        throw new Error(
          `Re-select the same file (${ctx.fileName}) to continue the interrupted run.`
        );
      }
      return {
        payload: { fileBlob: file.blob, fileName: file.name },
        context: { kind: "upload", fileName: file.name },
      };
    }

    if (resume) {
      return {
        payload: null,
        context: this.plugin.lastAnalyzeContext,
        useStoredContext: true,
      };
    }

    throw new Error("Provide a URL or choose a file.");
  }

  async startAnalyze({ resume = false } = {}) {
    this.analyzeError = "";
    try {
      const built = this.buildAnalyzePayload({ resume });
      let payload = built.payload;
      if (built.useStoredContext) {
        payload = await this.plugin.buildAnalyzePayload();
      }
      if (built.context) {
        this.plugin.rememberAnalyzeContext(built.context);
      }
      await this.plugin.runAnalyze(payload, {
        resume,
        label: resume ? "Continuing interrupted run..." : "Analyzing...",
      });
    } catch (err) {
      this.analyzeError = err.message || String(err);
      this.render();
    }
  }

  renderMetadata(container, source, novelty) {
    const sourceTags = source.tags || [];
    const sourceLinks = source.wikilinks || [];
    const overlapTags = novelty.tag_overlap || [];
    const normalizedSourceTags = new Set(
      sourceTags.map((tag) => String(tag).replace(/^#/, "").toLowerCase())
    );
    const matchedTags = overlapTags.filter((tag) =>
      normalizedSourceTags.has(String(tag).replace(/^#/, "").toLowerCase())
    );
    if (!sourceTags.length && !sourceLinks.length && !overlapTags.length) {
      return;
    }
    const panel = container.createEl("div", { cls: "actualizer-source-metadata" });
    if (sourceTags.length) {
      panel.createEl("span", { cls: "actualizer-muted", text: "Source tags: " });
      sourceTags.forEach((tag) =>
        panel.createEl("code", { cls: "actualizer-tag", text: `#${String(tag).replace(/^#/, "")}` })
      );
    }
    if (overlapTags.length) {
      const row = panel.createEl("div");
      row.createEl("span", { cls: "actualizer-muted", text: "Overlap note tags: " });
      overlapTags.forEach((tag) =>
        row.createEl("code", { cls: "actualizer-tag", text: `#${String(tag).replace(/^#/, "")}` })
      );
    }
    if (matchedTags.length) {
      const row = panel.createEl("div");
      row.createEl("span", { cls: "actualizer-muted", text: "Source/vault tag overlap: " });
      matchedTags.forEach((tag) =>
        row.createEl("code", { cls: "actualizer-tag", text: `#${String(tag).replace(/^#/, "")}` })
      );
    }
    if (sourceLinks.length) {
      const row = panel.createEl("div");
      row.createEl("span", { cls: "actualizer-muted", text: "Source wikilinks: " });
      sourceLinks.forEach((link) => {
        const button = row.createEl("button", {
          cls: "actualizer-inline-link",
          text: `[[${link.target}]]${link.resolved ? "" : " (unresolved)"}`,
        });
        button.disabled = !link.resolved;
        if (link.resolved) {
          button.onclick = () => this.plugin.openVaultNote(link.note_path);
        }
      });
    }
  }

  renderOverlap(container, notes) {
    const items = notes || [];
    if (!items.length) {
      return;
    }
    const panel = container.createEl("details", { cls: "actualizer-overlap-panel" });
    panel.createEl("summary", {
      text: `Overlapping vault notes (${items.length})`,
    });
    const list = panel.createEl("div", { cls: "actualizer-overlap-list" });
    for (const note of items.slice(0, 8)) {
      const item = list.createEl("div", { cls: "actualizer-overlap-item" });
      item.createEl("div", {
        cls: "actualizer-overlap-title",
        text: note.note_title || note.note_path,
      });
      item.createEl("div", {
        cls: "actualizer-muted",
        text: `${note.note_path} · similarity ${note.max_similarity}`,
      });
      if (note.tags?.length) {
        item.createEl("div", {
          cls: "actualizer-muted",
          text: `Tags: ${note.tags.map((tag) => `#${String(tag).replace(/^#/, "")}`).join(" ")}`,
        });
      }
      if (note.sample_heading) {
        item.createEl("div", {
          cls: "actualizer-overlap-heading",
          text: note.sample_heading,
        });
      }
      if (note.sample_text) {
        item.createEl("div", {
          cls: "actualizer-overlap-excerpt",
          text: note.sample_text,
        });
      }
      const openBtn = item.createEl("button", {
        cls: "actualizer-link-btn",
        text: "Open note",
      });
      openBtn.onclick = () => this.plugin.openVaultNote(note.note_path);
    }
  }

  renderNovel(container, chunks) {
    const items = chunks || [];
    if (!items.length) {
      return;
    }
    const panel = container.createEl("details", { cls: "actualizer-overlap-panel" });
    panel.createEl("summary", {
      text: `Novel snippets (${items.length})`,
    });
    const list = panel.createEl("div", { cls: "actualizer-overlap-list" });
    for (const chunk of items.slice(0, 8)) {
      const item = list.createEl("div", { cls: "actualizer-overlap-item" });
      item.createEl("div", {
        cls: "actualizer-overlap-excerpt",
        text: chunk,
      });
    }
  }

  renderSuggestion(container, item, index) {
    const box = container.createEl("div", { cls: "actualizer-suggestion" });
    const header = box.createEl("div", { cls: "actualizer-suggestion-header" });
    const label = header.createEl("label");
    const cb = label.createEl("input", { type: "checkbox" });
    cb.checked = this.selected.has(index);
    cb.onchange = () => {
      if (cb.checked) {
        this.selected.add(index);
      } else {
        this.selected.delete(index);
      }
    };
    const title = item.is_moc ? `${item.concept_title} [MOC]` : item.concept_title;
    label.createEl("span", { text: title });
    header.createEl("span", {
      cls: "actualizer-muted",
      text: item.location?.display || "",
    });
    if (item.is_moc) {
      box.createEl("div", {
        cls: "actualizer-muted",
        text: "Deselected by default: MOCs are optional navigation indexes, not atomic source notes.",
      });
    } else if (item.is_novel === false) {
      box.createEl("div", {
        cls: "actualizer-muted",
        text: "Deselected by default: this topic looks known or only partially new in your vault. Select it to write or append anyway.",
      });
    }

    const pathRow = box.createEl("div", { cls: "actualizer-field" });
    pathRow.createEl("label", { text: "Vault path" });
    const pathInput = pathRow.createEl("input", {
      cls: "actualizer-input",
      type: "text",
      value: item.note_path || "",
    });
    pathInput.oninput = () => {
      this.suggestions[index].note_path = pathInput.value;
    };

    if (item.append_target && !item.is_moc) {
      const modeRow = box.createEl("div", { cls: "actualizer-field" });
      modeRow.createEl("label", { text: "Write mode" });
      const modeWrap = modeRow.createEl("div", { cls: "actualizer-mode-row" });
      const writeLabel = modeWrap.createEl("label");
      const writeRadio = writeLabel.createEl("input", {
        type: "radio",
        name: `write-mode-${index}`,
        value: "write",
      });
      writeRadio.checked = (item.write_mode || "write") === "write";
      writeLabel.createEl("span", { text: "New file" });
      const appendLabel = modeWrap.createEl("label");
      const appendRadio = appendLabel.createEl("input", {
        type: "radio",
        name: `write-mode-${index}`,
        value: "append",
      });
      appendRadio.checked = item.write_mode === "append";
      appendLabel.createEl("span", { text: "Append to existing" });
      const syncMode = () => {
        const mode = appendRadio.checked ? "append" : "write";
        this.suggestions[index].write_mode = mode;
        if (mode === "append") {
          this.suggestions[index].note_path = item.append_target;
          pathInput.value = item.append_target;
        } else {
          const original = (this.result?.suggestions || [])[index]?.note_path;
          if (original) {
            this.suggestions[index].note_path = original;
            pathInput.value = original;
          }
        }
      };
      writeRadio.onchange = syncMode;
      appendRadio.onchange = syncMode;
      box.createEl("div", {
        cls: "actualizer-muted",
        text: `High overlap (${item.overlap_similarity}) with ${item.append_target}`,
      });
      if (item.append_heading) {
        box.createEl("div", {
          cls: "actualizer-muted",
          text: `Append under heading: ${item.append_heading}`,
        });
      }
      const previewDetails = box.createEl("details", { cls: "actualizer-append-preview" });
      previewDetails.createEl("summary", { text: "Preview exact merged note" });
      const previewHost = previewDetails.createEl("div", { cls: "actualizer-append-diff" });
      previewDetails.addEventListener("toggle", () => {
        if (previewDetails.open) {
          this.loadAppendPreview(index, previewHost, item);
        }
      });
    }

    const details = box.createEl("details", { cls: "actualizer-note-details" });
    details.open = this.expanded.has(index);
    details.createEl("summary", { text: "Edit note content" });
    const contentArea = details.createEl("textarea", {
      cls: "actualizer-textarea",
      text: item.content || "",
    });
    contentArea.value = item.content || "";
    contentArea.oninput = () => {
      this.suggestions[index].content = contentArea.value;
    };
    details.addEventListener("toggle", () => {
      if (details.open) {
        this.expanded.add(index);
      } else {
        this.expanded.delete(index);
      }
    });
  }

  async loadAppendPreview(index, host, item) {
    host.empty();
    host.setText("Loading preview...");
    try {
      const note = buildApplyNote(this.suggestions[index] || item, false);
      const preview = await this.plugin.api.previewSuggestion(
        await this.plugin.resolveVaultPathForApi(),
        note
      );
      host.empty();
      if (note.append_heading) {
        host.createEl("div", {
          cls: "actualizer-muted",
          text: `Append heading: ${note.append_heading}`,
        });
      }
      const grid = host.createEl("div", { cls: "actualizer-append-grid" });
      const currentWrap = grid.createEl("div");
      currentWrap.createEl("strong", { text: "Existing content" });
      const current = currentWrap.createEl("pre", { cls: "actualizer-preview-block" });
      current.setText(preview.existing_content || "(empty note)");
      enableNestedScroll(current);
      const finalWrap = grid.createEl("div");
      finalWrap.createEl("strong", { text: "Exact final content" });
      const next = finalWrap.createEl("pre", { cls: "actualizer-preview-block" });
      next.setText(preview.final_content || "(empty note)");
      enableNestedScroll(next);
    } catch (err) {
      host.setText(`Preview failed: ${err.message}`);
    }
  }

  renderResults(container) {
    container.createEl("hr", { cls: "actualizer-divider" });
    container.createEl("h4", { text: "Results" });

    const verdict = this.result.novelty?.verdict || "—";
    container.createEl("span", {
      cls: `actualizer-verdict ${this.verdictClass(verdict)}`,
      text: verdict,
    });

    const source = this.result.source || {};
    container.createEl("div", {
      text: `${source.title || "Source"} (${source.source_type || "?"})`,
    });

    this.renderMetadata(container, source, this.result.novelty || {});
    this.renderOverlap(container, this.result.novelty?.overlapping_notes || []);
    this.renderNovel(container, this.result.novelty?.novel_chunks || []);

    container.createEl("div", {
      cls: "actualizer-muted",
      text: `${this.suggestions.length} proposed note(s) · ${this.selected.size} selected`,
    });
    if (this.suggestions.some((item) => item.is_moc)) {
      container.createEl("div", {
        cls: "actualizer-muted",
        text: "MOC suggestions start deselected because they are optional navigation notes.",
      });
    }
    if (this.suggestions.some((item) => !item.is_moc && item.is_novel === false)) {
      container.createEl("div", {
        cls: "actualizer-muted",
        text: "Known / partial topics start deselected; select them to write or append anyway.",
      });
    }

    const selectionRow = container.createEl("div", { cls: "actualizer-actions" });
    selectionRow.createEl("button", { text: "Select all" }).onclick = () => {
      this.selected = new Set(
        this.suggestions.map((item, index) => (item.is_moc ? null : index)).filter((i) => i !== null)
      );
      this.render();
    };
    selectionRow.createEl("button", { text: "Select none" }).onclick = () => {
      this.selected = new Set();
      this.render();
    };
    selectionRow.createEl("button", { text: "Select novel only" }).onclick = () => {
      this.selected = new Set(
        this.suggestions
          .map((item, index) =>
            item.is_moc || item.is_novel === false ? null : index
          )
          .filter((index) => index !== null)
      );
      this.render();
    };

    const pageControls = container.createEl("div", { cls: "actualizer-pager" });
    const sizeLabel = pageControls.createEl("label", { cls: "actualizer-pager-size" });
    sizeLabel.createEl("span", { text: "Per page " });
    const sizeSelect = sizeLabel.createEl("select", { cls: "actualizer-select" });
    for (const value of [5, 10, 25, 50, "all"]) {
      const option = sizeSelect.createEl("option", {
        text: value === "all" ? "All" : String(value),
        value: String(value),
      });
      if (String(this.pageSize) === String(value)) {
        option.selected = true;
      }
    }
    sizeSelect.onchange = () => {
      const value = sizeSelect.value;
      this.pageSize = value === "all" ? "all" : Number(value);
      this.currentPage = 1;
      this.render();
    };

    const slice = suggestionPageSlice(
      this.suggestions.length,
      this.currentPage,
      this.pageSize
    );
    this.currentPage = slice.page;

    if (slice.pages > 1) {
      const prevBtn = pageControls.createEl("button", { text: "Prev" });
      prevBtn.disabled = slice.page <= 1;
      prevBtn.onclick = () => {
        this.currentPage = Math.max(1, this.currentPage - 1);
        this.render();
      };
      pageControls.createEl("span", {
        cls: "actualizer-muted actualizer-pager-label",
        text: `Page ${slice.page} of ${slice.pages} (notes ${slice.start + 1}–${slice.end} of ${this.suggestions.length})`,
      });
      const nextBtn = pageControls.createEl("button", { text: "Next" });
      nextBtn.disabled = slice.page >= slice.pages;
      nextBtn.onclick = () => {
        this.currentPage = Math.min(slice.pages, this.currentPage + 1);
        this.render();
      };
    } else if (this.suggestions.length) {
      pageControls.createEl("span", {
        cls: "actualizer-muted actualizer-pager-label",
        text: `${this.suggestions.length} note(s)`,
      });
    }

    const list = container.createEl("div", { cls: "actualizer-suggestion-list" });
    for (let i = slice.start; i < slice.end; i += 1) {
      this.renderSuggestion(list, this.suggestions[i], i);
    }

    const actions = container.createEl("div", { cls: "actualizer-actions" });
    actions
      .createEl("button", { text: "Write selected to vault", cls: "mod-cta" })
      .onclick = () => this.writeSelected();
    actions.createEl("button", { text: "Clear results" }).onclick = () => {
      this.result = null;
      this.suggestions = [];
      this.selected = new Set();
      this.currentPage = 1;
      this.writeStatus = "";
      this.render();
    };
    actions.createEl("button", { text: "Open web UI" }).onclick = () =>
      window.open(this.plugin.settings.apiBaseUrl, "_blank");

    if (this.writeStatus) {
      container.createEl("div", {
        cls: "actualizer-write-status",
        text: this.writeStatus,
      });
    }

    if (this.warnings.length) {
      const warn = container.createEl("div", { cls: "actualizer-warnings" });
      warn.setText(this.warnings.join(" "));
    }
  }

  render() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.addClass("actualizer-panel");
    containerEl.addClass("actualizer-view-root");
    this._ui = {};

    const fixed = containerEl.createEl("div", { cls: "actualizer-panel-fixed" });
    fixed.createEl("h3", { text: "Knowledge Actualizer" });

    this.renderAnalyzeSource(fixed);
    this.renderProgressBar(fixed);

    if (this.isAnalyzing) {
      this.renderLiveDraftsSection(fixed);
      if (this.warnings.length) {
        const warn = fixed.createEl("div", { cls: "actualizer-warnings" });
        warn.setText(this.warnings.join(" "));
      }
      if (!this.result) {
        return;
      }
    }

    if (!this.result) {
      if (this.warnings.length) {
        const warn = fixed.createEl("div", { cls: "actualizer-warnings" });
        warn.setText(this.warnings.join(" "));
      }
      return;
    }

    const resultsScroll = containerEl.createEl("div", { cls: "actualizer-results-scroll" });
    enableScrollContainer(resultsScroll);
    this._ui.resultsScroll = resultsScroll;
    this.renderResults(resultsScroll);
  }

  async recoverCheckpoint() {
    try {
      this.isAnalyzing = false;
      this.progress = "Loading saved notes...";
      this.progressEvent = null;
      this.render();
      const data = await this.plugin.api.getCheckpoint();
      const saved = (data && data.suggestions) || [];
      if (!data?.exists || !saved.length) {
        new Notice("No saved notes to recover");
        this.progress = "";
        this.render();
        return;
      }
      this.setResult({
        source: data.source || {},
        novelty: {
          verdict: "Recovered",
          overlapping_notes: [],
          novel_chunks: [],
          known_chunks: [],
        },
        suggestions: saved,
        warnings: [
          ...(data.warnings || []),
          `Recovered ${saved.length} saved note(s) from the last ${
            data.completed ? "completed" : "interrupted"
          } run.`,
        ],
      });
      this.progress = "";
      this.progressEvent = null;
      new Notice(`Recovered ${saved.length} note(s)`);
    } catch (err) {
      this.progress = "";
      this.progressEvent = null;
      new Notice(`Recover failed: ${err.message}`);
      this.render();
    }
  }

  async continueRun() {
    await this.startAnalyze({ resume: true });
  }

  async writeSelected({ overwriteExisting = false } = {}) {
    const notes = buildSelectedApplyNotes(
      this.suggestions,
      this.selected,
      overwriteExisting
    );
    if (!notes.length) {
      new Notice("Select at least one note");
      return;
    }

    const missingPath = notes.filter((note) => !note.note_path);
    if (missingPath.length) {
      new Notice("Some selected notes are missing a vault path");
      return;
    }

    try {
      this.setProgress("Writing notes to vault...");
      this.writeStatus = "";
      const vaultPath = await this.plugin.resolveVaultPathForApi();
      if (!vaultPath) {
        throw new Error("Vault path is unavailable; configure it in the server or use a local vault.");
      }
      const responses = [await this.plugin.api.applyBatch(vaultPath, notes)];
      let results = [...(responses[0].results || [])];
      let skipped = results.filter((result) => result.status === "skipped_exists");

      if (skipped.length && !overwriteExisting) {
        const paths = skipped.map((result) => result.note_path);
        const shown = paths.slice(0, 8).join("\n");
        const extra = paths.length > 8 ? `\n…and ${paths.length - 8} more` : "";
        const confirmed = await this.plugin.confirmAction({
          title: "Overwrite existing notes?",
          message:
            `${skipped.length} note(s) already exist:\n\n${shown}${extra}\n\n` +
            "The server will keep a .bak copy before each overwrite.",
          confirmText: "Overwrite",
        });
        if (confirmed) {
          const skippedPaths = new Set(paths);
          const retryNotes = notes
            .filter((note) => skippedPaths.has(note.note_path))
            .map((note) => ({ ...note, overwrite: true }));
          const retryResponse = await this.plugin.api.applyBatch(vaultPath, retryNotes);
          responses.push(retryResponse);
          results = [
            ...results.filter((result) => result.status !== "skipped_exists"),
            ...(retryResponse.results || []),
          ];
          skipped = [];
        }
      }

      const written = results.filter((result) => result.written_path);
      const errors = results.filter((result) => result.status === "error");
      if (written.length) {
        for (const response of responses) {
          if (response.index_refresh?.warning) {
            this.warnings = [...this.warnings, response.index_refresh.warning];
          }
        }
        await this.plugin.openVaultNotes(written.map((result) => result.written_path));
      }

      const parts = [];
      if (written.length) {
        parts.push(`Wrote ${written.length} note(s)`);
      }
      if (skipped.length && !overwriteExisting) {
        parts.push(`skipped ${skipped.length} existing`);
      }
      if (errors.length) {
        parts.push(`${errors.length} failed`);
      }

      this.writeStatus = parts.join("; ") || "Nothing written.";
      if (written.length) {
        this.writeStatus += `: ${written
          .slice(0, 3)
          .map((r) => r.written_path)
          .join(", ")}${written.length > 3 ? ` (+${written.length - 3} more)` : ""}`;
      }
      if (errors.length) {
        this.writeStatus += ` — ${errors.map((e) => `${e.note_path}: ${e.error}`).join("; ")}`;
      }

      if (written.length) {
        new Notice(`Wrote ${written.length} note(s) to vault`);
      } else if (errors.length) {
        new Notice(`${errors.length} note(s) failed to write`);
      } else if (skipped.length) {
        new Notice(`Skipped ${skipped.length} existing note(s)`);
      }

      this.setProgress("");
      await this.refreshCheckpointState();
      this.render();
    } catch (err) {
      this.writeStatus = `Write failed: ${err.message}`;
      new Notice(`Write failed: ${err.message}`);
      this.setProgress("");
      this.render();
    }
  }
}

class ActualizerSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Knowledge Database Actualizer" });

    new Setting(containerEl)
      .setName("API base URL")
      .setDesc("Local Actualizer server (default http://127.0.0.1:8000)")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_API)
          .setValue(this.plugin.settings.apiBaseUrl)
          .onChange(async (value) => {
            this.plugin.settings.apiBaseUrl = value.trim() || DEFAULT_API;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("API token")
      .setDesc("Optional — matches API_TOKEN in the Actualizer .env")
      .addText((text) => {
        text.inputEl.type = "password";
        return text.setValue(this.plugin.settings.apiToken).onChange(async (value) => {
          this.plugin.settings.apiToken = value;
          await this.plugin.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName("Open notes after write")
      .setDesc("Open newly written vault notes in Obsidian after apply")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.openNotesAfterWrite)
          .onChange(async (value) => {
            this.plugin.settings.openNotesAfterWrite = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Test API connection")
      .setDesc(
        "Checks that Obsidian can reach the local server (uses requestUrl, not browser fetch)"
      )
      .addButton((button) =>
        button.setButtonText("Test").onClick(async () => {
          try {
            const status = await this.plugin.api.status();
            new Notice(
              `Connected — ${status.indexed_chunks || 0} indexed chunks`
            );
          } catch (err) {
            new Notice(`Connection failed: ${err.message}`);
          }
        })
      );
  }
}

class KnowledgeDatabaseActualizerPlugin extends Plugin {
  async onload() {
    await this.loadSettings();
    this.api = new ActualizerApi(this);
    this.lastAnalyzeContext = null;
    this._checkpointPollTimer = null;
    this._activeAnalyzeController = null;

    this.registerView(VIEW_TYPE, (leaf) => new ActualizerView(leaf, this));

    this.addRibbonIcon("search-check", "Actualizer sidebar", () => {
      this.activateView();
    });

    this.addCommand({
      id: "actualizer-analyze-current-file",
      name: "Analyze current note",
      callback: () => this.analyzeCurrentFile(),
    });

    this.addCommand({
      id: "actualizer-analyze-url",
      name: "Analyze URL from clipboard",
      callback: () => this.analyzeClipboardUrl(),
    });

    this.addCommand({
      id: "actualizer-analyze-selection",
      name: "Analyze current selection",
      callback: () => this.analyzeSelection(),
    });

    this.addCommand({
      id: "actualizer-index-vault",
      name: "Index vault",
      callback: () => this.indexVault(),
    });

    this.addCommand({
      id: "actualizer-recover-checkpoint",
      name: "Recover saved notes",
      callback: () => this.recoverFromCheckpoint(),
    });

    this.addCommand({
      id: "actualizer-continue-run",
      name: "Continue interrupted run",
      callback: () => this.continueInterruptedRun(),
    });

    this.addCommand({
      id: "actualizer-write-selected",
      name: "Write selected notes to vault",
      callback: () => this.writeSelectedNotes(),
    });

    this.addCommand({
      id: "actualizer-cancel-analyze",
      name: "Cancel analysis",
      callback: () => this.cancelAnalyze(),
    });

    this.addCommand({
      id: "actualizer-open-sidebar",
      name: "Open Actualizer sidebar",
      callback: () => this.activateView(),
    });

    this.addSettingTab(new ActualizerSettingTab(this.app, this));
  }

  onunload() {
    this.cancelAnalyze({ silent: true });
    this.stopCheckpointPoll();
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async activateView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    workspace.revealLeaf(leaf);
    const view = leaf.view;
    if (view instanceof ActualizerView) {
      await view.refreshCheckpointState();
      await view.refreshServerStatus();
      view.prefillSourceFromContext();
      view.render();
    }
    return view;
  }

  async getView() {
    const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    return leaf ? leaf.view : null;
  }

  vaultPath() {
    const adapter = this.app.vault.adapter;
    if (adapter instanceof FileSystemAdapter) {
      return adapter.getBasePath();
    }
    return adapter.basePath || null;
  }

  async resolveVaultPathForApi() {
    const local = this.vaultPath();
    if (local) {
      return local;
    }
    try {
      const status = await this.api.status();
      return status.vault_path || null;
    } catch (_) {
      return null;
    }
  }

  vaultPathMismatch(status = null) {
    const local = this.vaultPath();
    const configured = status?.vault_path;
    if (!local || !configured) {
      return "";
    }
    const normalize = (value) => {
      const normalized = String(value).replace(/\\/g, "/").replace(/\/+$/, "");
      return /^[a-z]:/i.test(normalized) ? normalized.toLowerCase() : normalized;
    };
    if (normalize(local) === normalize(configured)) {
      return "";
    }
    return `Vault path mismatch: Obsidian is using ${local}, but the server is configured for ${configured}. Requests will explicitly use the Obsidian vault; update VAULT_PATH to keep watcher and status data aligned.`;
  }

  confirmAction(details) {
    return new Promise((resolve) => {
      new ActualizerConfirmModal(this.app, details, resolve).open();
    });
  }

  async writeSelectedNotes() {
    const view = (await this.activateView()) || (await this.getView());
    if (!view || !view.suggestions?.length) {
      new Notice("Recover or analyze notes first, then write from the sidebar");
      return;
    }
    await view.writeSelected();
  }

  async openVaultNote(relativePath) {
    const file = this.app.vault.getAbstractFileByPath(relativePath);
    if (!file) {
      new Notice(`Note not found: ${relativePath}`);
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  async openVaultNotes(relativePaths) {
    if (!this.settings.openNotesAfterWrite || !relativePaths?.length) {
      return;
    }
    for (let i = 0; i < relativePaths.length; i += 1) {
      let file = this.app.vault.getAbstractFileByPath(relativePaths[i]);
      for (let attempt = 0; !file && attempt < 5; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 100 * (attempt + 1)));
        file = this.app.vault.getAbstractFileByPath(relativePaths[i]);
      }
      if (!file) {
        new Notice(`Note was written but is not visible in Obsidian yet: ${relativePaths[i]}`);
        continue;
      }
      const leaf = i === 0 ? this.app.workspace.getLeaf(false) : this.app.workspace.getLeaf("tab");
      await leaf.openFile(file);
    }
  }

  async buildAnalyzePayload() {
    const ctx = this.lastAnalyzeContext;
    if (!ctx) {
      throw new Error("No previous analyze context — run Analyze on the same source first");
    }
    if (ctx.kind === "url") {
      return { url: ctx.url };
    }
    if (ctx.kind === "file") {
      const file = this.app.vault.getAbstractFileByPath(ctx.path);
      if (!file) {
        throw new Error(`File no longer exists: ${ctx.path}`);
      }
      const content = await this.currentEditorContent(file);
      return {
        fileBlob: new Blob([content], { type: "text/markdown" }),
        fileName: file.name,
        vaultNotePath: ctx.path,
      };
    }
    if (ctx.kind === "selection") {
      return {
        fileBlob: new Blob([ctx.text], { type: "text/markdown" }),
        fileName: ctx.fileName,
        vaultNotePath: ctx.path || undefined,
      };
    }
    if (ctx.kind === "upload") {
      throw new Error(
        `Re-select the same file (${ctx.fileName}) in the sidebar to continue the interrupted run.`
      );
    }
    throw new Error("Unknown analyze context");
  }

  async ensureFreshIndex() {
    const status = await this.api.status();
    if (this.vaultPathMismatch(status)) {
      await this.api.indexVault(this.vaultPath(), true);
      return;
    }
    const stale = status.stale_note_count || 0;
    if (stale <= 0) {
      return;
    }
    const proceed = await this.confirmAction({
      title: "Update stale vault index?",
      message: `${stale} vault note(s) changed since the last index. Update it before analysis?`,
      confirmText: "Update index",
    });
    if (!proceed) {
      return;
    }
    await this.api.indexVault(this.vaultPath(), true);
    new Notice(`Re-indexed vault (${stale} stale note(s))`);
  }

  cancelAnalyze({ silent = false } = {}) {
    const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    const view = leaf?.view;
    if (view instanceof ActualizerView && view.isAnalyzing) {
      view._cancelPending = true;
      view.render();
    }
    if (this._activeAnalyzeController) {
      this._activeAnalyzeController.abort();
      if (!silent) {
        new Notice("Canceling analysis…");
      }
      return true;
    }
    if (!silent) {
      new Notice("No analysis is running");
    }
    return false;
  }

  async runAnalyze(payload, { resume = false, label = "Analyzing..." } = {}) {
    if (this._activeAnalyzeController) {
      new Notice("An analysis is already running — cancel it first");
      return;
    }
    const view = (await this.activateView()) || (await this.getView());
    const controller = new AbortController();
    this._activeAnalyzeController = controller;
    if (view) {
      view.isAnalyzing = true;
      view._cancelPending = false;
      view.analyzeError = "";
      view.liveDraftNotes = [];
      if (!resume) {
        view.clearResult();
      }
      view.warnings = [];
      view.setProgress(label);
    }
    this.startCheckpointPoll(view);
    try {
      if (!resume) {
        await this.ensureFreshIndex();
      }
      if (controller.signal.aborted) {
        throw makeAnalyzeAbortError();
      }
      const result = await this.api.analyze({
        ...payload,
        resume,
        signal: controller.signal,
        onEvent: (event) => {
          if (controller.signal.aborted) {
            return;
          }
          if (view && event.type === "progress") {
            view.setProgress(event.message || "Working...", event);
            if (event.stage === "drafting") {
              this.pollCheckpointOnce(view);
            }
          }
          if (view && event.type === "warning") {
            view.warnings = [...(view.warnings || []), event.message];
          }
        },
      });
      if (controller.signal.aborted) {
        throw makeAnalyzeAbortError();
      }
      if (view) {
        view.setResult(result);
        await view.refreshCheckpointState();
        await view.refreshServerStatus();
      }
      new Notice(`Verdict: ${result.novelty?.verdict || "done"}`);
    } catch (err) {
      if (view) {
        view.isAnalyzing = false;
        view._cancelPending = false;
        view.progress = "";
        view.progressEvent = null;
        view.liveDraftNotes = [];
        if (isAnalyzeAbortError(err)) {
          view.clearResult();
          view.analyzeError =
            "Analysis canceled. Use Recover/Continue if checkpoint notes were already saved.";
          view.render();
          await view.refreshCheckpointState();
          new Notice("Analysis canceled");
        } else {
          view.analyzeError = err.message || String(err);
          if (err.partialSuggestions?.length) {
            view.setResult({
              source: {},
              novelty: {
                verdict: "Partial",
                overlapping_notes: [],
                novel_chunks: [],
                known_chunks: [],
              },
              suggestions: err.partialSuggestions,
              warnings: [
                ...(view.warnings || []),
                `Generation stopped early (${err.message}). Recovered ${err.partialSuggestions.length} saved note(s).`,
              ],
            });
          } else {
            view.render();
          }
          await view.refreshCheckpointState();
          new Notice(`Analyze failed: ${err.message}`);
        }
      } else if (isAnalyzeAbortError(err)) {
        new Notice("Analysis canceled");
      } else {
        new Notice(`Analyze failed: ${err.message}`);
      }
    } finally {
      if (this._activeAnalyzeController === controller) {
        this._activeAnalyzeController = null;
      }
      if (view) {
        view._cancelPending = false;
      }
      this.stopCheckpointPoll();
    }
  }

  async pollCheckpointOnce(view) {
    if (!view || !view.isAnalyzing) {
      return;
    }
    try {
      const ckpt = await this.api.getCheckpoint();
      if (ckpt?.suggestions?.length) {
        view.applyLiveDrafts(ckpt.suggestions);
      }
    } catch (_) {
      /* ignore transient poll errors */
    }
  }

  startCheckpointPoll(view) {
    this.stopCheckpointPoll();
    if (!view) {
      return;
    }
    this.pollCheckpointOnce(view);
    this._checkpointPollTimer = window.setInterval(() => {
      this.pollCheckpointOnce(view);
    }, 1500);
  }

  stopCheckpointPoll() {
    if (this._checkpointPollTimer) {
      window.clearInterval(this._checkpointPollTimer);
      this._checkpointPollTimer = null;
    }
  }

  rememberAnalyzeContext(context) {
    this.lastAnalyzeContext = context;
  }

  async currentEditorContent(file) {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (view?.file?.path === file.path) {
      const value = editorMarkdown(view);
      if (value || view.editor?.getValue) {
        return value;
      }
    }
    return this.app.vault.read(file);
  }

  async analyzeCurrentFile() {
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("No active file");
      return;
    }
    this.rememberAnalyzeContext({ kind: "file", path: file.path });
    const content = await this.currentEditorContent(file);
    const blob = new Blob([content], { type: "text/markdown" });
    await this.runAnalyze(
      { fileBlob: blob, fileName: file.name, vaultNotePath: file.path },
      { label: `Analyzing ${file.name}...` }
    );
  }

  async analyzeClipboardUrl() {
    const text = await navigator.clipboard.readText();
    const url = text.trim();
    if (!url.startsWith("http")) {
      new Notice("Clipboard does not contain a URL");
      return;
    }
    const view = (await this.activateView()) || (await this.getView());
    if (view) {
      view.sourceUrl = url;
      view.pendingFile = null;
      view.analyzeError = "";
      view.render();
    }
    this.rememberAnalyzeContext({ kind: "url", url });
    await this.runAnalyze({ url }, { label: "Analyzing URL..." });
  }

  async analyzeSelection() {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!view) {
      new Notice("Open a markdown note first");
      return;
    }
    const selection = editorMarkdown(view, true);
    if (!selection.trim()) {
      new Notice("Select some text first");
      return;
    }
    const file = view.file;
    const fileName = file ? `${file.basename}-selection.md` : "selection.md";
    this.rememberAnalyzeContext({
      kind: "selection",
      path: file ? file.path : null,
      text: selection,
      fileName,
    });
    const blob = new Blob([selection], { type: "text/markdown" });
    await this.runAnalyze(
      { fileBlob: blob, fileName, vaultNotePath: file ? file.path : undefined },
      { label: "Analyzing selection..." }
    );
  }

  async indexVault() {
    try {
      new Notice("Indexing vault...");
      const result = await this.api.indexVault(this.vaultPath(), true);
      const view = await this.getView();
      if (view) {
        await view.refreshServerStatus();
        view.render();
      }
      if (result.skipped) {
        new Notice("Index is already fresh");
      } else {
        new Notice(`Indexed ${result.chunk_count || result.indexed_chunks || 0} chunks`);
      }
    } catch (err) {
      new Notice(`Index failed: ${err.message}`);
    }
  }

  async recoverFromCheckpoint() {
    const view = (await this.activateView()) || (await this.getView());
    if (view) {
      await view.recoverCheckpoint();
    }
  }

  async continueInterruptedRun() {
    const view = (await this.activateView()) || (await this.getView());
    if (view) {
      await view.startAnalyze({ resume: true });
      return;
    }
    new Notice("Open the Actualizer sidebar first");
  }
}

module.exports = KnowledgeDatabaseActualizerPlugin;
