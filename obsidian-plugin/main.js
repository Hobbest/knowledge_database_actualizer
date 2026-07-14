const {
  Plugin,
  Notice,
  ItemView,
  Setting,
  MarkdownView,
  PluginSettingTab,
  requestUrl,
  TFile,
  FileSystemAdapter,
} = require("obsidian");
const http = require("http");
const https = require("https");
const { URL } = require("url");

const VIEW_TYPE = "actualizer-sidebar";
const DEFAULT_API = "http://127.0.0.1:8000";

const DEFAULT_SETTINGS = {
  apiBaseUrl: DEFAULT_API,
  apiToken: "",
  openNotesAfterWrite: true,
};

function stripFrontmatter(content) {
  const raw = String(content || "");
  if (!raw.trim().startsWith("---")) {
    return raw.trim();
  }
  const parts = raw.split("---");
  if (parts.length >= 3) {
    return parts.slice(2).join("---").trim();
  }
  return raw.trim();
}

function appendPreviewBody(content) {
  const body = stripFrontmatter(content);
  if (!body) {
    return "";
  }
  if (/^#{1,6}\s/m.test(body.trimStart())) {
    return body;
  }
  return `## Update\n\n${body}`;
}

function normalizeHeading(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function contentForAppend(fullContent, heading = "Update") {
  const body = stripFrontmatter(fullContent);
  const trimmed = body.trim();
  if (!trimmed) {
    return "";
  }
  if (/^#{1,6}\s/m.test(trimmed)) {
    return trimmed;
  }
  return `## ${heading}\n\n${trimmed}`;
}

function splitByHeadings(text) {
  const pattern = /^(#{1,6})\s+(.+)$/gm;
  const matches = [...String(text || "").matchAll(pattern)];
  if (!matches.length) {
    return text.trim() ? [[null, text.trim()]] : [];
  }

  const sections = [];
  if (matches[0].index > 0) {
    const preamble = text.slice(0, matches[0].index).trim();
    if (preamble) {
      sections.push([null, preamble]);
    }
  }

  for (let i = 0; i < matches.length; i += 1) {
    const match = matches[i];
    const heading = match[2].trim();
    const start = match.index + match[0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
    const body = text.slice(start, end).trim();
    if (body) {
      sections.push([heading, body]);
    }
  }
  return sections;
}

function mergeAppendIntoNote(
  existingContent,
  draftContent,
  targetHeading = null,
  fallbackHeading = "Update"
) {
  const frontmatterMatch = existingContent.match(/^---[\s\S]*?---\n?/);
  const frontmatter = frontmatterMatch ? frontmatterMatch[0] : "";
  const existingBody = stripFrontmatter(existingContent);
  const appendBody = contentForAppend(draftContent, fallbackHeading);
  if (!appendBody) {
    return existingContent.endsWith("\n") ? existingContent : `${existingContent}\n`;
  }

  if (!targetHeading) {
    return `${existingContent.trimEnd()}\n\n${appendBody.trim()}\n`;
  }

  const targetNorm = normalizeHeading(targetHeading);
  const sections = splitByHeadings(existingBody);
  if (!sections.length) {
    return `${existingContent.trimEnd()}\n\n${appendBody.trim()}\n`;
  }

  const rebuilt = [];
  let matched = false;
  sections.forEach(([heading, sectionBody], index) => {
    if (heading === null) {
      rebuilt.push(sectionBody.trim());
      return;
    }
    rebuilt.push(`## ${heading}`);
    if (normalizeHeading(heading) === targetNorm) {
      matched = true;
      rebuilt.push(`${sectionBody.trimEnd()}\n\n${appendBody.trim()}`);
    } else {
      rebuilt.push(sectionBody.trim());
    }
    if (index < sections.length - 1) {
      rebuilt.push("");
    }
  });

  if (!matched) {
    return `${existingContent.trimEnd()}\n\n${appendBody.trim()}\n`;
  }

  const newBody = `${rebuilt.join("\n").trim()}\n`;
  return frontmatter ? `${frontmatter}${newBody}` : newBody;
}

function parseAppendTarget(target) {
  if (!target) {
    return { path: "", heading: null };
  }
  const raw = String(target).trim();
  if (!raw.includes("#")) {
    return { path: raw, heading: null };
  }
  const hashIndex = raw.indexOf("#");
  return {
    path: raw.slice(0, hashIndex).trim(),
    heading: raw.slice(hashIndex + 1).trim().replace(/^#+/, "").trim() || null,
  };
}

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
  let buffer = "";
  buffer = consumeNdjsonLines(String(text || ""), (event) => {
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
  });
  if (buffer.trim()) {
    consumeNdjsonLines(`${buffer}\n`, (event) => {
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
    });
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

  getVaultNote(notePath, vaultPath) {
    const params = new URLSearchParams({ note_path: notePath });
    if (vaultPath) {
      params.set("vault_path", vaultPath);
    }
    return this.request(`/api/vault/note?${params.toString()}`);
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

  refreshNotes(vaultPath, notePaths) {
    return this.request("/api/vault/refresh-notes", {
      method: "POST",
      json: true,
      body: JSON.stringify({ vault_path: vaultPath, note_paths: notePaths }),
    });
  }

  async analyze({ url, fileBlob, fileName, resume = false, vaultNotePath, onEvent }) {
    const multipart = await buildMultipartBody({ url, fileBlob, fileName, resume, vaultNotePath });
    const apiUrl = `${this.apiBase()}/api/sources/analyze`;
    const body = Buffer.from(multipart.body);
    const headers = {
      ...this.authHeaders(),
      "Content-Type": multipart.contentType,
      "Content-Length": String(body.length),
    };

    try {
      return await this._analyzeStreamNode(apiUrl, headers, body, onEvent);
    } catch (err) {
      // Fall back to buffered requestUrl (no live progress events).
      const text = await this.request("/api/sources/analyze", {
        method: "POST",
        body: multipart.body,
        contentType: multipart.contentType,
        rawText: true,
      });
      return parseNdjsonStream(text, onEvent);
    }
  }

  _analyzeStreamNode(apiUrl, headers, body, onEvent) {
    return new Promise((resolve, reject) => {
      const parsed = new URL(apiUrl);
      const transport = parsed.protocol === "https:" ? https : http;
      let buffer = "";
      let result = null;
      let streamError = null;
      let partialSuggestions = [];

      const finish = () => {
        if (streamError) {
          if (partialSuggestions.length) {
            streamError.partialSuggestions = partialSuggestions;
          }
          reject(streamError);
          return;
        }
        if (!result) {
          reject(new Error("No result from analyze stream"));
          return;
        }
        resolve(result);
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

      const req = transport.request(
        {
          hostname: parsed.hostname,
          port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
          path: `${parsed.pathname}${parsed.search}`,
          method: "POST",
          headers,
        },
        (res) => {
          if (res.statusCode === 401) {
            res.resume();
            reject(new Error("API token required or invalid (401)"));
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
                reject(new Error(payload.detail || raw || String(res.statusCode)));
              } catch (_) {
                reject(new Error(raw || String(res.statusCode)));
              }
            });
            return;
          }

          res.setEncoding("utf8");
          res.on("data", (chunk) => {
            buffer = consumeNdjsonLines(buffer + chunk, handleEvent);
          });
          res.on("end", () => {
            if (buffer.trim()) {
              consumeNdjsonLines(`${buffer}\n`, handleEvent);
            }
            finish();
          });
        }
      );

      req.on("error", (err) => reject(this.connectionError(err, apiUrl)));
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
    this.checkpointState = null;
    this.liveDraftNotes = [];
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
    await this.refreshCheckpointState();
    await this.refreshServerStatus();
    this.prefillSourceFromContext();
    this.render();
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
        .map((item, index) => (item.is_moc ? null : index))
        .filter((index) => index !== null)
    );
    this.expanded = new Set();
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
    fileField.createEl("label", { text: "Or upload file (txt, md, pdf)" });
    const fileRow = fileField.createEl("div", { cls: "actualizer-file-row" });
    const fileInput = fileRow.createEl("input", {
      type: "file",
      cls: "actualizer-file-input",
    });
    fileInput.setAttr("accept", ".txt,.md,.markdown,.pdf");
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

    actions.createEl("button", { text: "Index vault" }).onclick = () =>
      this.plugin.indexVault();

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
      const previewDetails = box.createEl("details", { cls: "actualizer-append-preview" });
      previewDetails.createEl("summary", { text: "Preview append diff" });
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
      const existing = await this.plugin.api.getVaultNote(
        item.append_target,
        this.plugin.vaultPath()
      );
      const proposed = appendPreviewBody(this.suggestions[index].content || item.content);
      host.empty();
      const grid = host.createEl("div", { cls: "actualizer-append-grid" });
      const current = grid.createEl("pre", { cls: "actualizer-preview-block" });
      current.setText(existing.content || "(empty note)");
      const next = grid.createEl("pre", { cls: "actualizer-preview-block" });
      next.setText(proposed || "(nothing to append)");
    } catch (err) {
      host.setText(`Preview failed: ${err.message}`);
    }
  }

  render() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.addClass("actualizer-panel");
    this._ui = {};

    containerEl.createEl("h3", { text: "Knowledge Actualizer" });

    this.renderAnalyzeSource(containerEl);
    this.renderProgressBar(containerEl);

    if (this.isAnalyzing) {
      this.renderLiveDraftsSection(containerEl);
      if (this.warnings.length) {
        const warn = containerEl.createEl("div", { cls: "actualizer-warnings" });
        warn.setText(this.warnings.join(" "));
      }
      if (!this.result) {
        return;
      }
    }

    if (!this.result) {
      if (this.warnings.length) {
        const warn = containerEl.createEl("div", { cls: "actualizer-warnings" });
        warn.setText(this.warnings.join(" "));
      }
      return;
    }

    containerEl.createEl("hr", { cls: "actualizer-divider" });
    containerEl.createEl("h4", { text: "Results" });

    const verdict = this.result.novelty?.verdict || "—";
    containerEl.createEl("span", {
      cls: `actualizer-verdict ${this.verdictClass(verdict)}`,
      text: verdict,
    });

    const source = this.result.source || {};
    containerEl.createEl("div", {
      text: `${source.title || "Source"} (${source.source_type || "?"})`,
    });

    this.renderOverlap(containerEl, this.result.novelty?.overlapping_notes || []);
    this.renderNovel(containerEl, this.result.novelty?.novel_chunks || []);

    containerEl.createEl("div", {
      cls: "actualizer-muted",
      text: `${this.suggestions.length} proposed note(s) · ${this.selected.size} selected`,
    });

    const selectionRow = containerEl.createEl("div", { cls: "actualizer-actions" });
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

    const list = containerEl.createEl("div", { cls: "actualizer-suggestion-list" });
    for (let i = 0; i < this.suggestions.length; i += 1) {
      this.renderSuggestion(list, this.suggestions[i], i);
    }

    const actions = containerEl.createEl("div", { cls: "actualizer-actions" });
    actions
      .createEl("button", { text: "Write selected to vault", cls: "mod-cta" })
      .onclick = () => this.writeSelected();
    actions.createEl("button", { text: "Clear results" }).onclick = () => {
      this.result = null;
      this.suggestions = [];
      this.selected = new Set();
      this.writeStatus = "";
      this.render();
    };
    actions.createEl("button", { text: "Open web UI" }).onclick = () =>
      window.open(this.plugin.settings.apiBaseUrl, "_blank");

    if (this.writeStatus) {
      containerEl.createEl("div", {
        cls: "actualizer-write-status",
        text: this.writeStatus,
      });
    }

    if (this.warnings.length) {
      const warn = containerEl.createEl("div", { cls: "actualizer-warnings" });
      warn.setText(this.warnings.join(" "));
    }
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
    const notes = [];
    for (const idx of this.selected) {
      const item = this.suggestions[idx];
      if (!item) {
        continue;
      }
      const mode = item.write_mode === "append" ? "append" : "write";
      const rawTarget =
        mode === "append" && item.append_target ? item.append_target : item.note_path;
      const { path: targetPath, heading: targetHeading } = parseAppendTarget(rawTarget);
      notes.push({
        note_path: String(targetPath || item.note_path || "").trim(),
        content: item.content,
        mode,
        overwrite: overwriteExisting,
        append_heading:
          mode === "append" ? item.append_heading || targetHeading || null : null,
      });
    }
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
      const results = [];
      for (const note of notes) {
        results.push(await this.plugin.writeNoteToVault(note));
      }

      let written = results.filter((r) => r.written_path);
      let skipped = results.filter((r) => r.status === "skipped_exists");
      const errors = results.filter((r) => r.status === "error");

      if (skipped.length && !overwriteExisting) {
        const paths = skipped.map((r) => r.note_path).slice(0, 8);
        const extra =
          skipped.length > 8 ? `\n…and ${skipped.length - 8} more` : "";
        const confirmed = confirm(
          `${skipped.length} note(s) already exist:\n\n${paths.join("\n")}${extra}\n\nOverwrite them? Obsidian file history may still apply.`
        );
        if (confirmed) {
          for (const skippedNote of skipped) {
            const retry = notes.find((n) => n.note_path === skippedNote.note_path);
            if (!retry) {
              continue;
            }
            const retryResult = await this.plugin.writeNoteToVault({
              ...retry,
              overwrite: true,
            });
            results.push(retryResult);
            if (retryResult.written_path) {
              written = [...written, retryResult];
            }
          }
          skipped = [];
        }
      }

      if (written.length) {
        const vaultPath = await this.plugin.resolveVaultPathForApi();
        if (vaultPath) {
          try {
            const refresh = await this.plugin.api.refreshNotes(
              vaultPath,
              written.map((r) => r.written_path)
            );
            if (refresh.warning) {
              this.warnings = [...this.warnings, refresh.warning];
            }
          } catch (err) {
            this.warnings = [
              ...this.warnings,
              `Notes were written, but index refresh failed: ${err.message}`,
            ];
          }
        } else {
          this.warnings = [
            ...this.warnings,
            "Notes were written in Obsidian, but the search index was not refreshed (vault path unavailable). Re-index the vault in the server UI.",
          ];
        }
        await this.plugin.openVaultNotes(written.map((r) => r.written_path));
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
      .addText((text) =>
        text.setValue(this.plugin.settings.apiToken).onChange(async (value) => {
          this.plugin.settings.apiToken = value;
          await this.plugin.saveSettings();
        })
      );

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
      id: "actualizer-open-sidebar",
      name: "Open Actualizer sidebar",
      callback: () => this.activateView(),
    });

    this.addSettingTab(new ActualizerSettingTab(this.app, this));
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

  async ensureParentFolders(notePath) {
    const parts = notePath.split("/").filter(Boolean);
    parts.pop();
    let acc = "";
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part;
      if (!this.app.vault.getAbstractFileByPath(acc)) {
        await this.app.vault.createFolder(acc);
      }
    }
  }

  async writeNoteToVault(note) {
    const path = String(note.note_path || "").trim();
    if (!path) {
      return { status: "error", note_path: path, error: "Missing vault path" };
    }

    const mode = note.mode === "append" ? "append" : "write";
    const content = String(note.content || "");
    const file = this.app.vault.getAbstractFileByPath(path);

    try {
      if (mode === "append") {
        if (file instanceof TFile) {
          const current = await this.app.vault.read(file);
          const merged = mergeAppendIntoNote(
            current,
            content,
            note.append_heading || null
          );
          await this.app.vault.modify(file, merged);
          return { status: "appended", note_path: path, written_path: path };
        }
        await this.ensureParentFolders(path);
        const created = await this.app.vault.create(
          path,
          content.endsWith("\n") ? content : `${content}\n`
        );
        return {
          status: "written",
          note_path: path,
          written_path: created.path,
        };
      }

      if (file instanceof TFile) {
        if (!note.overwrite) {
          return {
            status: "skipped_exists",
            note_path: path,
            error: "Note already exists",
          };
        }
        await this.app.vault.modify(
          file,
          content.endsWith("\n") ? content : `${content}\n`
        );
        return {
          status: "written",
          note_path: path,
          written_path: path,
          overwritten: true,
        };
      }

      await this.ensureParentFolders(path);
      const created = await this.app.vault.create(
        path,
        content.endsWith("\n") ? content : `${content}\n`
      );
      return {
        status: "written",
        note_path: path,
        written_path: created.path,
      };
    } catch (err) {
      return {
        status: "error",
        note_path: path,
        error: err.message || String(err),
      };
    }
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
      const file = this.app.vault.getAbstractFileByPath(relativePaths[i]);
      if (!file) {
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
      const content = await this.app.vault.read(file);
      return {
        fileBlob: new Blob([content], { type: "text/markdown" }),
        fileName: file.name,
        vaultNotePath: ctx.path,
      };
    }
    if (ctx.kind === "selection") {
      return {
        fileBlob: new Blob([ctx.text], { type: "text/plain" }),
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
    const stale = status.stale_note_count || 0;
    if (stale <= 0) {
      return;
    }
    const proceed = confirm(
      `${stale} vault note(s) changed since the last index.\n\nRe-index now?`
    );
    if (!proceed) {
      return;
    }
    await this.api.indexVault(this.vaultPath(), false);
    new Notice(`Re-indexed vault (${stale} stale note(s))`);
  }

  async runAnalyze(payload, { resume = false, label = "Analyzing..." } = {}) {
    const view = (await this.activateView()) || (await this.getView());
    if (view) {
      view.isAnalyzing = true;
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
      const result = await this.api.analyze({
        ...payload,
        resume,
        onEvent: (event) => {
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
      if (view) {
        view.setResult(result);
        await view.refreshCheckpointState();
        await view.refreshServerStatus();
      }
      new Notice(`Verdict: ${result.novelty?.verdict || "done"}`);
    } catch (err) {
      if (view) {
        view.isAnalyzing = false;
        view.progress = "";
        view.progressEvent = null;
        view.liveDraftNotes = [];
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
      }
      new Notice(`Analyze failed: ${err.message}`);
    } finally {
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

  async analyzeCurrentFile() {
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("No active file");
      return;
    }
    this.rememberAnalyzeContext({ kind: "file", path: file.path });
    const content = await this.app.vault.read(file);
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
    const selection = view.editor.getSelection();
    if (!selection.trim()) {
      new Notice("Select some text first");
      return;
    }
    const file = view.file;
    const fileName = file ? `${file.basename}-selection.txt` : "selection.txt";
    this.rememberAnalyzeContext({
      kind: "selection",
      path: file ? file.path : null,
      text: selection,
      fileName,
    });
    const blob = new Blob([selection], { type: "text/plain" });
    await this.runAnalyze(
      { fileBlob: blob, fileName, vaultNotePath: file ? file.path : undefined },
      { label: "Analyzing selection..." }
    );
  }

  async indexVault() {
    try {
      new Notice("Indexing vault...");
      const result = await this.api.indexVault(this.vaultPath(), false);
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
