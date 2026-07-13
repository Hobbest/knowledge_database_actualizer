const {
  Plugin,
  Notice,
  ItemView,
  Setting,
  MarkdownView,
  PluginSettingTab,
  requestUrl,
} = require("obsidian");

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

function parseNdjsonStream(text, onEvent) {
  let result = null;
  const lines = String(text || "").split("\n");
  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    const event = JSON.parse(line);
    if (onEvent) {
      onEvent(event);
    }
    if (event.type === "result") {
      result = event;
    }
    if (event.type === "error") {
      throw new Error(event.message || "Analyze failed");
    }
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

  async analyze({ url, fileBlob, fileName, resume = false, vaultNotePath, onEvent }) {
    const multipart = await buildMultipartBody({ url, fileBlob, fileName, resume, vaultNotePath });
    const text = await this.request("/api/sources/analyze", {
      method: "POST",
      body: multipart.body,
      contentType: multipart.contentType,
      rawText: true,
    });
    return parseNdjsonStream(text, onEvent);
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
    this.warnings = [];
    this.checkpointState = null;
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
    this.render();
  }

  setProgress(message) {
    this.progress = message || "";
    this.render();
  }

  setResult(result) {
    this.result = result;
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
    const actions = container.createEl("div", { cls: "actualizer-actions" });
    actions.createEl("button", { text: "Index vault" }).onclick = () =>
      this.plugin.indexVault();

    if (this.checkpointState?.recoverable) {
      const recover = actions.createEl("button", { text: "Recover saved notes" });
      recover.onclick = () => this.recoverCheckpoint();
    }

    if (this.checkpointState?.resumable) {
      const resume = actions.createEl("button", {
        text: `Continue run (${this.checkpointState.savedCount} saved)`,
      });
      resume.onclick = () => this.continueRun();
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

    containerEl.createEl("h3", { text: "Knowledge Actualizer" });

    if (this.progress) {
      containerEl.createEl("div", {
        cls: "actualizer-progress",
        text: this.progress,
      });
    }

    if (!this.result) {
      containerEl.createEl("p", {
        text: "Run a command to analyze a note, URL, or selection.",
      });
      this.renderIdleActions(containerEl);
      return;
    }

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

    containerEl.createEl("div", {
      cls: "actualizer-muted",
      text: `${this.suggestions.length} proposed note(s) · ${this.selected.size} selected`,
    });

    const list = containerEl.createEl("div", { cls: "actualizer-suggestion-list" });
    for (let i = 0; i < this.suggestions.length; i += 1) {
      this.renderSuggestion(list, this.suggestions[i], i);
    }

    const actions = containerEl.createEl("div", { cls: "actualizer-actions" });
    actions.createEl("button", { text: "Write selected" }).onclick = () =>
      this.writeSelected();
    actions.createEl("button", { text: "Clear results" }).onclick = () => {
      this.result = null;
      this.suggestions = [];
      this.selected = new Set();
      this.render();
    };
    actions.createEl("button", { text: "Open web UI" }).onclick = () =>
      window.open(this.plugin.settings.apiBaseUrl, "_blank");

    if (this.warnings.length) {
      const warn = containerEl.createEl("div", { cls: "actualizer-warnings" });
      warn.setText(this.warnings.join(" "));
    }
  }

  async recoverCheckpoint() {
    try {
      this.setProgress("Loading saved notes...");
      const data = await this.plugin.api.getCheckpoint();
      const saved = (data && data.suggestions) || [];
      if (!data?.exists || !saved.length) {
        new Notice("No saved notes to recover");
        this.setProgress("");
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
      this.setProgress("");
      new Notice(`Recovered ${saved.length} note(s)`);
    } catch (err) {
      this.setProgress("");
      new Notice(`Recover failed: ${err.message}`);
    }
  }

  async continueRun() {
    await this.plugin.runAnalyze(await this.plugin.buildAnalyzePayload(), {
      resume: true,
      label: "Continuing interrupted run...",
    });
  }

  async writeSelected() {
    const notes = [];
    for (const idx of this.selected) {
      const item = this.suggestions[idx];
      if (!item) {
        continue;
      }
      const mode = item.write_mode === "append" ? "append" : "write";
      const targetPath =
        mode === "append" && item.append_target ? item.append_target : item.note_path;
      notes.push({
        note_path: targetPath,
        content: item.content,
        mode,
        overwrite: false,
        append_heading: mode === "append" ? item.append_heading || null : null,
      });
    }
    if (!notes.length) {
      new Notice("Select at least one note");
      return;
    }
    try {
      this.setProgress("Writing notes...");
      const result = await this.plugin.api.applyBatch(this.plugin.vaultPath(), notes);
      const written = result.written_paths || [];
      const skipped = result.skipped_existing || [];
      if (written.length) {
        new Notice(`Wrote ${written.length} note(s)`);
        await this.plugin.openVaultNotes(written);
      }
      if (skipped.length) {
        new Notice(
          `Skipped ${skipped.length} existing note(s). Enable overwrite in the web UI if needed.`
        );
      }
      if (result.errors?.length) {
        new Notice(`${result.errors.length} note(s) failed to write`);
      }
      this.setProgress("");
      await this.refreshCheckpointState();
    } catch (err) {
      new Notice(`Write failed: ${err.message}`);
      this.setProgress("");
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
      view.render();
    }
    return view;
  }

  async getView() {
    const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    return leaf ? leaf.view : null;
  }

  vaultPath() {
    return this.app.vault.adapter.basePath;
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
      view.setProgress(label);
      if (!resume) {
        view.setResult(null);
      }
      view.warnings = [];
    }
    try {
      if (!resume) {
        await this.ensureFreshIndex();
      }
      const result = await this.api.analyze({
        ...payload,
        resume,
        onEvent: (event) => {
          if (view && event.type === "progress") {
            const count =
              event.total > 0 ? ` (${event.current}/${event.total})` : "";
            view.setProgress(`${event.message || "Working..."}${count}`);
          }
          if (view && event.type === "warning") {
            view.warnings = [...(view.warnings || []), event.message];
          }
        },
      });
      if (view) {
        view.setResult(result);
        view.setProgress("");
        await view.refreshCheckpointState();
      }
      new Notice(`Verdict: ${result.novelty?.verdict || "done"}`);
    } catch (err) {
      if (view) {
        view.setProgress("");
        await view.refreshCheckpointState();
        view.render();
      }
      new Notice(`Analyze failed: ${err.message}`);
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
      if (result.skipped) {
        new Notice("Index is already fresh");
      } else {
        new Notice(`Indexed ${result.chunk_count} chunks`);
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
    try {
      const payload = await this.buildAnalyzePayload();
      await this.runAnalyze(payload, {
        resume: true,
        label: "Continuing interrupted run...",
      });
    } catch (err) {
      new Notice(err.message);
    }
  }
}

module.exports = KnowledgeDatabaseActualizerPlugin;
