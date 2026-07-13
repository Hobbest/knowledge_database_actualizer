const vaultPathInput = document.getElementById("vaultPath");
const indexBtn = document.getElementById("indexBtn");
const calibrateBtn = document.getElementById("calibrateBtn");
const vaultWatchToggle = document.getElementById("vaultWatchToggle");
const statusBox = document.getElementById("statusBox");
const sourceUrlInput = document.getElementById("sourceUrl");
const sourceFileInput = document.getElementById("sourceFile");
const analyzeBtn = document.getElementById("analyzeBtn");
const analyzeError = document.getElementById("analyzeError");
const resultsSection = document.getElementById("resultsSection");
const verdictBadge = document.getElementById("verdictBadge");
const noveltyScore = document.getElementById("noveltyScore");
const sourceTitle = document.getElementById("sourceTitle");
const sourceType = document.getElementById("sourceType");
const overlapPanel = document.getElementById("overlapPanel");
const novelPanel = document.getElementById("novelPanel");
const overlapList = document.getElementById("overlapList");
const novelList = document.getElementById("novelList");
const overlapCount = document.getElementById("overlapCount");
const novelCount = document.getElementById("novelCount");

/** Auto-expand short lists; keep long ones collapsed until the user opens them. */
const COLLAPSE_LIST_THRESHOLD = 4;
const suggestionsList = document.getElementById("suggestionsList");
const suggestionCount = document.getElementById("suggestionCount");
const writeAllBtn = document.getElementById("writeAllBtn");
const applyMessage = document.getElementById("applyMessage");
const analyzeProgress = document.getElementById("analyzeProgress");
const progressLabel = document.getElementById("progressLabel");
const progressCount = document.getElementById("progressCount");
const progressBar = document.getElementById("progressBar");
const suggestionToolbar = document.getElementById("suggestionToolbar");
const suggestionPager = document.getElementById("suggestionPager");
const selectAllBtn = document.getElementById("selectAllBtn");
const clearAllBtn = document.getElementById("clearAllBtn");
const pageSizeSelect = document.getElementById("pageSizeSelect");
const prevPageBtn = document.getElementById("prevPageBtn");
const nextPageBtn = document.getElementById("nextPageBtn");
const pageLabel = document.getElementById("pageLabel");
const recoverBtn = document.getElementById("recoverBtn");
const continueBtn = document.getElementById("continueBtn");
const warningsBox = document.getElementById("warningsBox");
const tagOverlapBox = document.getElementById("tagOverlapBox");
const tagOverlapList = document.getElementById("tagOverlapList");
const sourceMeta = document.getElementById("sourceMeta");

let graphNetwork = null;
let currentSuggestions = [];
let currentPage = 1;
let pageSize = 10;
let obsidianVaultName = null;
let obsidianUriEnabled = false;
const API_TOKEN_KEY = "actualizer_api_token";

function getApiToken() {
  return sessionStorage.getItem(API_TOKEN_KEY) || "";
}

function setApiToken(token) {
  if (token) sessionStorage.setItem(API_TOKEN_KEY, token);
  else sessionStorage.removeItem(API_TOKEN_KEY);
}

function withAuthHeaders(options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getApiToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return { ...options, headers };
}

async function ensureApiToken(authRequired) {
  if (!authRequired && !getApiToken()) return;
  if (getApiToken()) return;
  const entered = window.prompt("API token required (Authorization Bearer). Leave blank to cancel:");
  if (!entered) throw new Error("API token required");
  setApiToken(entered.trim());
}

function verdictClass(verdict) {
  if (verdict === "Already known") return "badge-known";
  if (verdict === "Partially new") return "badge-partial";
  return "badge-novel";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Map streamed stages to a monotonic 0-100% bar so it never jumps backwards.
function computeProgress(event) {
  const { stage, current = 0, total = 0 } = event;
  const ratio = total > 0 ? Math.min(1, current / total) : 0;
  switch (stage) {
    case "loading":
      return 5;
    case "novelty":
      return 15;
    case "scoring":
      return 15 + ratio * 25;
    case "drafting":
      return 40 + ratio * 60;
    default:
      return 0;
  }
}

function showProgress(event) {
  analyzeProgress.classList.remove("hidden");
  progressLabel.textContent = event.message || "Working...";
  progressCount.textContent = event.total > 0 ? `${event.current}/${event.total}` : "";
  progressBar.style.width = `${computeProgress(event)}%`;
}

function beginProgress(message = "Starting analysis...") {
  analyzeProgress.classList.remove("hidden");
  progressLabel.textContent = message;
  progressCount.textContent = "";
  progressBar.style.width = "2%";
}

function resetProgress() {
  progressBar.style.width = "0%";
  progressCount.textContent = "";
  progressLabel.textContent = "Working...";
  analyzeProgress.classList.add("hidden");
}

function renderWarnings(warnings) {
  const list = warnings || [];
  if (!list.length) {
    warningsBox.classList.add("hidden");
    warningsBox.innerHTML = "";
    return;
  }
  warningsBox.classList.remove("hidden");
  warningsBox.innerHTML = list
    .map((message) => `<div>⚠️ ${escapeHtml(message)}</div>`)
    .join("");
}

// Stream newline-delimited JSON events from a POST request.
async function streamNdjson(url, options, onEvent) {
  let response = await fetch(url, withAuthHeaders(options));
  if (response.status === 401) {
    setApiToken("");
    await ensureApiToken(true);
    response = await fetch(url, withAuthHeaders(options));
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg || String(item)).join("; "));
    }
    throw new Error(detail || response.statusText);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleLine = (line) => {
    const trimmed = line.trim();
    if (trimmed) onEvent(JSON.parse(trimmed));
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex;
    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      handleLine(buffer.slice(0, newlineIndex));
      buffer = buffer.slice(newlineIndex + 1);
    }
  }
  handleLine(buffer);
}

async function fetchJson(url, options = {}) {
  let response = await fetch(url, withAuthHeaders(options));
  if (response.status === 401) {
    setApiToken("");
    await ensureApiToken(true);
    response = await fetch(url, withAuthHeaders(options));
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg || String(item)).join("; "));
    }
    throw new Error(detail || response.statusText);
  }
  return data;
}

async function loadStatus() {
  try {
    const status = await fetchJson("/api/status");
    if (status.auth_required) {
      await ensureApiToken(true);
    }
    if (status.vault_path) {
      vaultPathInput.value = status.vault_path;
    }
    obsidianVaultName = status.obsidian_vault_name || null;
    obsidianUriEnabled = Boolean(status.obsidian_uri_enabled);
    const llmLabel = status.llm_enabled
      ? `enabled (${status.llm_provider || "llm"})`
      : "extractive fallback";
    statusBox.textContent = [
      `Vault: ${status.vault_path || "not configured"}`,
      `Indexed chunks: ${status.indexed_chunks}`,
      `Graph: ${status.graph_nodes} nodes / ${status.graph_edges} edges`,
      `Embeddings: ${status.embedding_provider}/${status.embedding_model}`,
      `LLM: ${llmLabel}`,
    ].join(" | ");

    if (status.llm_enabled && status.llm_budget) {
      const b = status.llm_budget;
      statusBox.textContent += ` | Budget: ${b.max_calls_per_run} calls / ${Number(b.max_input_chars_per_run).toLocaleString()} chars per run`;
    }

    const recommended = status.thresholds && status.thresholds.recommended;
    if (recommended) {
      statusBox.textContent += ` | Thresholds: novel=${status.thresholds.novel} known=${status.thresholds.known}`;
    }

    const calibrationAvailable = status.thresholds && status.thresholds.calibration_available;
    if (calibrationAvailable) {
      statusBox.textContent += " | Threshold calibration available";
    }

    if (status.auth_required) {
      statusBox.textContent += " | Auth: required";
    }

    const incomplete = status.incomplete_checkpoints || [];
    if (incomplete.length) {
      const summary = incomplete
        .slice(0, 3)
        .map((item) => `${item.suggestion_count} note(s) for ${item.source_title || item.source_ref || item.source_key}`)
        .join("; ");
      statusBox.textContent += ` | Saved runs: ${summary}${incomplete.length > 3 ? "…" : ""}`;
    }

    const warnings = status.warnings || [];
    if (status.stale_note_count > 0) {
      statusBox.textContent += ` | ${status.stale_note_count} stale note(s)`;
    }

    const watch = status.vault_watch || {};
    if (vaultWatchToggle) {
      vaultWatchToggle.checked = Boolean(watch.enabled);
      vaultWatchToggle.disabled = !status.vault_path;
    }
    if (watch.enabled) {
      const watchLabel = watch.active ? "watch active" : "watch pending vault";
      statusBox.textContent += ` | Auto-index: ${watchLabel}`;
      if (watch.last_stale_count > 0 && watch.last_index_at) {
        statusBox.textContent += ` (last: ${watch.last_stale_count} stale)`;
      }
      if (watch.last_error) {
        statusBox.textContent += ` — ${watch.last_error}`;
      }
    }

    if (warnings.length) {
      statusBox.textContent += `\n⚠ ${warnings.join(" ")}`;
      statusBox.className = "text-sm text-amber-700 whitespace-pre-wrap";
    } else {
      statusBox.className = "text-sm text-slate-600";
    }
  } catch (error) {
    statusBox.textContent = `Failed to load status: ${error.message}`;
    statusBox.className = "text-sm text-red-600";
  }
}

indexBtn.addEventListener("click", async () => {
  analyzeError.classList.add("hidden");
  indexBtn.disabled = true;
  indexBtn.textContent = "Indexing...";

  try {
    const vault_path = vaultPathInput.value.trim();
    if (!vault_path) {
      throw new Error("Enter a vault path first.");
    }

    const result = await fetchJson("/api/vault/index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vault_path }),
    });
    const mode = result.index_mode === "incremental" ? "incremental" : "full";
    const skipped = result.skipped_notes ? `, skipped ${result.skipped_notes} unchanged` : "";
    const updated = result.indexed_notes != null ? `, updated ${result.indexed_notes}` : "";
    statusBox.textContent =
      `${mode === "incremental" ? "Incremental index" : "Full index"}: ` +
      `${result.chunk_count} chunks from ${result.notes} notes (${result.links} links${updated}${skipped}).`;
    await loadStatus();
  } catch (error) {
    statusBox.textContent = `Index failed: ${error.message}`;
  } finally {
    indexBtn.disabled = false;
    indexBtn.textContent = "Index vault";
  }
});

calibrateBtn.addEventListener("click", async () => {
  analyzeError.classList.add("hidden");
  calibrateBtn.disabled = true;
  calibrateBtn.textContent = "Calibrating...";

  try {
    const result = await fetchJson("/api/vault/thresholds/calibrate");
    const lines = [
      result.message || "Threshold calibration complete.",
      `Samples: ${result.sample_size} (${result.same_note_samples} same-note, ${result.cross_note_samples} cross-note)`,
      `Recommended: NOVEL=${result.recommended_novel_threshold} KNOWN=${result.recommended_known_threshold}`,
      `Current: NOVEL=${result.current.novel} KNOWN=${result.current.known}`,
    ];
    if (result.fallback) {
      lines.push("(Using provider defaults — not enough data for vault-specific calibration.)");
    } else {
      lines.push("Update NOVEL_THRESHOLD and KNOWN_THRESHOLD in .env to apply these values.");
    }
    statusBox.textContent = lines.join("\n");
    statusBox.className = "text-sm text-slate-600 whitespace-pre-wrap";
    await loadStatus();
  } catch (error) {
    statusBox.textContent = `Calibration failed: ${error.message}`;
    statusBox.className = "text-sm text-red-600";
  } finally {
    calibrateBtn.disabled = false;
    calibrateBtn.textContent = "Calibrate thresholds";
  }
});

async function ensureFreshIndex() {
  const status = await fetchJson("/api/status");
  const stale = status.stale_note_count || 0;
  if (stale <= 0) {
    return;
  }
  const vaultPath = vaultPathInput.value.trim() || status.vault_path;
  if (!vaultPath) {
    return;
  }
  const proceed = window.confirm(
    `${stale} vault note(s) changed since the last index.\n\n` +
      "Re-index now for accurate novelty scores?"
  );
  if (!proceed) {
    return;
  }
  beginProgress(`Re-indexing ${stale} changed note(s)...`);
  const result = await fetchJson("/api/vault/index", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vault_path: vaultPath, if_stale: false }),
  });
  const mode = result.skipped ? "skipped (fresh)" : result.index_mode || "incremental";
  statusBox.textContent = `Re-index (${mode}): ${result.chunk_count ?? result.indexed_chunks ?? "?"} chunks.`;
  await loadStatus();
  resetProgress();
}

function stripFrontmatter(text) {
  const raw = String(text || "");
  if (!raw.trimStart().startsWith("---")) {
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

async function fetchVaultNote(notePath) {
  const vaultPath = vaultPathInput.value.trim();
  const params = new URLSearchParams({ note_path: notePath });
  if (vaultPath) {
    params.set("vault_path", vaultPath);
  }
  return fetchJson(`/api/vault/note?${params.toString()}`);
}

function setCollapsibleOpen(panel, itemCount) {
  if (!panel) return;
  panel.open = itemCount > 0 && itemCount < COLLAPSE_LIST_THRESHOLD;
}

function renderTagBadges(tags) {
  if (!tags || !tags.length) {
    return "";
  }
  return tags
    .map((tag) => `<span class="inline-block px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-xs">#${escapeHtml(tag)}</span>`)
    .join(" ");
}

function renderTagOverlap(tags) {
  const items = tags || [];
  if (!items.length) {
    tagOverlapBox.classList.add("hidden");
    tagOverlapList.innerHTML = "";
    return;
  }
  tagOverlapBox.classList.remove("hidden");
  tagOverlapList.innerHTML = renderTagBadges(items);
}

function renderSourceMeta(source) {
  const parts = [];
  if (source.tags && source.tags.length) {
    parts.push(`<div><span class="text-slate-500">Source tags:</span> ${renderTagBadges(source.tags)}</div>`);
  }
  if (source.wikilinks && source.wikilinks.length) {
    const links = source.wikilinks
      .map((item) => {
        const label = item.resolved ? item.note_path : `${item.target} (unresolved)`;
        return `<span class="inline-block mr-2">${escapeHtml(label)}</span>`;
      })
      .join("");
    parts.push(`<div><span class="text-slate-500">Vault links in source:</span> ${links}</div>`);
  }
  if (!parts.length) {
    sourceMeta.classList.add("hidden");
    sourceMeta.innerHTML = "";
    return;
  }
  sourceMeta.classList.remove("hidden");
  sourceMeta.innerHTML = parts.join("");
}

function renderObsidianLink(note) {
  const uri = note.obsidian_uri;
  if (!uri) {
    return "";
  }
  return `<a href="${escapeHtml(uri)}" class="text-indigo-600 hover:underline text-xs">Open in Obsidian</a>`;
}

function renderOverlap(notes) {
  const items = notes || [];
  overlapList.innerHTML = "";
  overlapCount.textContent = `${items.length}`;
  setCollapsibleOpen(overlapPanel, items.length);

  if (!items.length) {
    overlapList.innerHTML = "<li class='text-slate-500'>No overlapping notes found.</li>";
    return;
  }

  for (const note of items) {
    const li = document.createElement("li");
    li.className = "border rounded-lg p-3";
    li.innerHTML = `
      <div class="flex items-start justify-between gap-2">
        <div class="font-medium">${escapeHtml(note.note_title)}</div>
        ${renderObsidianLink(note)}
      </div>
      <div class="text-slate-500">${escapeHtml(note.note_path)}</div>
      <div class="text-xs mt-1">Similarity: ${note.max_similarity}</div>
      ${note.tags && note.tags.length ? `<div class="mt-2 flex flex-wrap gap-1">${renderTagBadges(note.tags)}</div>` : ""}
      ${note.sample_heading ? `<div class="mt-2 text-xs font-medium text-slate-600">${escapeHtml(note.sample_heading)}</div>` : ""}
      <div class="mt-2 text-slate-700">${escapeHtml(note.sample_text)}</div>
    `;
    overlapList.appendChild(li);
  }
}

function renderNovel(chunks) {
  const items = chunks || [];
  novelList.innerHTML = "";
  novelCount.textContent = `${items.length}`;
  setCollapsibleOpen(novelPanel, items.length);

  if (!items.length) {
    novelList.innerHTML = "<li class='text-slate-500'>No clearly novel snippets detected.</li>";
    return;
  }

  for (const chunk of items) {
    const li = document.createElement("li");
    li.className = "border rounded-lg p-2 bg-emerald-50";
    const preview = chunk.length > 280 ? `${chunk.slice(0, 280)}...` : chunk;
    li.textContent = preview;
    novelList.appendChild(li);
  }
}

function pageCount() {
  if (pageSize === "all") return 1;
  return Math.max(1, Math.ceil(currentSuggestions.length / pageSize));
}

function updateSuggestionCount() {
  const total = currentSuggestions.length;
  const selected = currentSuggestions.filter((item) => item.selected).length;
  suggestionCount.textContent = `${selected} of ${total} note${total === 1 ? "" : "s"} selected`;
}

function renderSuggestions(suggestions) {
  currentSuggestions = (suggestions || []).map((item) => ({
    ...item,
    selected: item.is_moc ? false : item.selected !== false,
    write_mode: item.write_mode || "write",
  }));
  currentPage = 1;

  if (!currentSuggestions.length) {
    suggestionToolbar.classList.add("hidden");
    suggestionPager.classList.add("hidden");
    suggestionCount.textContent = "0 notes";
    suggestionsList.innerHTML = "<p class='text-sm text-slate-500'>No note suggestions were generated.</p>";
    return;
  }

  suggestionToolbar.classList.remove("hidden");
  suggestionToolbar.classList.add("flex");
  renderSuggestionsPage();
}

function renderSuggestionsPage() {
  suggestionsList.innerHTML = "";
  updateSuggestionCount();

  const total = currentSuggestions.length;
  const pages = pageCount();
  currentPage = Math.min(Math.max(1, currentPage), pages);

  const start = pageSize === "all" ? 0 : (currentPage - 1) * pageSize;
  const end = pageSize === "all" ? total : Math.min(total, start + pageSize);

  for (let index = start; index < end; index += 1) {
    const suggestion = currentSuggestions[index];
    const card = document.createElement("div");
    card.className = "border rounded-lg p-4 space-y-3 bg-slate-50";
    const mocBadge = suggestion.is_moc
      ? '<span class="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">MOC</span>'
      : "";
    const appendSectionHint =
      suggestion.append_heading && suggestion.write_mode === "append"
        ? `<div class="text-xs text-slate-500">Appending under section: <code>${escapeHtml(suggestion.append_heading)}</code></div>`
        : "";
    const overlapHint =
      suggestion.append_target && suggestion.overlap_similarity != null
        ? `<div class="text-xs text-slate-500">High overlap (${suggestion.overlap_similarity}) with <code>${escapeHtml(suggestion.append_target)}</code>${suggestion.append_heading ? ` · section <code>${escapeHtml(suggestion.append_heading)}</code>` : ""}</div>`
        : "";
    const appendControls =
      suggestion.append_target && !suggestion.is_moc
        ? `<div class="space-y-2 rounded-lg border bg-white p-3">
            <div class="text-xs font-medium text-slate-500">Write mode</div>
            <div class="flex flex-wrap gap-4 text-sm">
              <label class="inline-flex items-center gap-2">
                <input type="radio" name="write-mode-${index}" value="write" class="write-mode-radio" data-index="${index}" />
                New file
              </label>
              <label class="inline-flex items-center gap-2">
                <input type="radio" name="write-mode-${index}" value="append" class="write-mode-radio" data-index="${index}" />
                Append to existing
              </label>
            </div>
            <details class="append-diff-details mt-2">
              <summary class="text-xs text-indigo-600 cursor-pointer">Preview append diff</summary>
              <div class="append-diff mt-2 grid md:grid-cols-2 gap-3 text-xs"></div>
            </details>
          </div>`
        : "";

    card.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <label class="flex items-center gap-2 font-medium">
          <input type="checkbox" data-index="${index}" class="suggestion-select" />
          <span>#${index + 1} · ${escapeHtml(suggestion.concept_title)}</span>
          ${mocBadge}
        </label>
        <span class="text-xs px-2 py-1 rounded-full bg-white border text-slate-600 whitespace-nowrap">
          ${escapeHtml(suggestion.location?.display || "unknown location")}
        </span>
      </div>
      ${overlapHint}
      ${appendSectionHint}
      ${appendControls}
      <div class="space-y-1">
        <label class="text-xs font-medium text-slate-500">Vault path</label>
        <input data-field="note_path" data-index="${index}" class="w-full border rounded-lg px-3 py-2 text-sm bg-white" />
      </div>
      <div class="space-y-1">
        <label class="text-xs font-medium text-slate-500">Note content</label>
        <textarea data-field="content" data-index="${index}" rows="12" class="w-full border rounded-lg px-3 py-2 font-mono text-sm bg-white"></textarea>
      </div>
    `;

    card.querySelector(".suggestion-select").checked = suggestion.selected;
    card.querySelector('[data-field="note_path"]').value = suggestion.note_path;
    card.querySelector('[data-field="content"]').value = suggestion.content;

    const writeMode = suggestion.write_mode || "write";
    const writeRadio = card.querySelector(`input.write-mode-radio[value="${writeMode}"]`);
    if (writeRadio) {
      writeRadio.checked = true;
    }

    suggestionsList.appendChild(card);
  }

  suggestionsList.querySelectorAll(".suggestion-select").forEach((input) => {
    input.addEventListener("change", (event) => {
      const idx = Number(event.target.dataset.index);
      currentSuggestions[idx].selected = event.target.checked;
      updateSuggestionCount();
    });
  });

  suggestionsList.querySelectorAll("[data-field]").forEach((input) => {
    input.addEventListener("input", (event) => {
      const idx = Number(event.target.dataset.index);
      const field = event.target.dataset.field;
      currentSuggestions[idx][field] = event.target.value;
    });
  });

  suggestionsList.querySelectorAll(".write-mode-radio").forEach((input) => {
    input.addEventListener("change", async (event) => {
      const idx = Number(event.target.dataset.index);
      const suggestion = currentSuggestions[idx];
      suggestion.write_mode = event.target.value;
      if (suggestion.write_mode === "append" && suggestion.append_target) {
        suggestion.note_path = suggestion.append_target;
        const pathInput = suggestionsList.querySelector(`[data-field="note_path"][data-index="${idx}"]`);
        if (pathInput) {
          pathInput.value = suggestion.append_target;
        }
      } else if (suggestion._original_note_path) {
        suggestion.note_path = suggestion._original_note_path;
        const pathInput = suggestionsList.querySelector(`[data-field="note_path"][data-index="${idx}"]`);
        if (pathInput) {
          pathInput.value = suggestion._original_note_path;
        }
      }
      if (suggestion.write_mode === "append") {
        await refreshAppendDiff(idx);
      }
    });
  });

  for (let index = start; index < end; index += 1) {
    const suggestion = currentSuggestions[index];
    if (!suggestion._original_note_path) {
      suggestion._original_note_path = suggestion.note_path;
    }
  }

  suggestionsList.querySelectorAll(".append-diff-details").forEach((details) => {
    details.addEventListener("toggle", async (event) => {
      if (!event.target.open) {
        return;
      }
      const card = event.target.closest(".border");
      const idx = Number(card?.querySelector("[data-index]")?.dataset.index);
      if (!Number.isNaN(idx)) {
        await refreshAppendDiff(idx);
      }
    });
  });

  if (pageSize === "all" || pages <= 1) {
    suggestionPager.classList.add("hidden");
  } else {
    suggestionPager.classList.remove("hidden");
    suggestionPager.classList.add("flex");
    pageLabel.textContent = `Page ${currentPage} of ${pages} (notes ${start + 1}–${end} of ${total})`;
    prevPageBtn.disabled = currentPage <= 1;
    nextPageBtn.disabled = currentPage >= pages;
  }
}

async function refreshAppendDiff(index) {
  const suggestion = currentSuggestions[index];
  if (!suggestion?.append_target) {
    return;
  }
  const pathInput = suggestionsList.querySelector(`[data-field="note_path"][data-index="${index}"]`);
  const card = pathInput?.closest(".border");
  const diffHost = card?.querySelector(".append-diff");
  if (!diffHost) {
    return;
  }
  diffHost.innerHTML = "<div class='text-slate-500 col-span-2'>Loading existing note...</div>";
  try {
    const existing = await fetchVaultNote(suggestion.append_target);
    const proposed = appendPreviewBody(suggestion.content);
    const existingBody = existing.exists
      ? stripFrontmatter(existing.content)
      : "(note not found — will be created as a new file)";
    diffHost.innerHTML = `
      <div class="space-y-1">
        <div class="font-medium text-slate-600">Existing</div>
        <pre class="border rounded p-2 bg-slate-50 whitespace-pre-wrap max-h-48 overflow-auto">${escapeHtml(existingBody)}</pre>
      </div>
      <div class="space-y-1">
        <div class="font-medium text-slate-600">Will append</div>
        <pre class="border rounded p-2 bg-emerald-50 whitespace-pre-wrap max-h-48 overflow-auto">${escapeHtml(proposed)}</pre>
      </div>
    `;
  } catch (error) {
    diffHost.innerHTML = `<div class="text-red-600 col-span-2">${escapeHtml(error.message)}</div>`;
  }
}

function renderGraph(graphData) {
  const container = document.getElementById("graph");
  if (!graphData || !graphData.nodes || !graphData.nodes.length) {
    container.innerHTML = "<p class='text-sm text-slate-500 p-4'>No graph data available. Index your vault first.</p>";
    return;
  }

  container.innerHTML = "";
  const nodes = new vis.DataSet(
    graphData.nodes.map((node) => ({
      ...node,
      color: node.highlighted ? "#4f46e5" : "#94a3b8",
      font: { color: node.highlighted ? "#1e1b4b" : "#334155" },
    }))
  );
  const edges = new vis.DataSet(
    (graphData.edges || []).map((edge) => {
      // Never render text on the edge itself (it just repeats the target node's
      // title); expose the link as a hover tooltip instead.
      const { label, title, ...rest } = edge;
      return { ...rest, title: title || label || undefined };
    })
  );
  const data = { nodes, edges };
  const options = {
    physics: { stabilization: true },
    interaction: { hover: true },
    edges: { arrows: "to", color: "#cbd5e1", font: { align: "horizontal" }, smooth: true },
  };

  if (graphNetwork) {
    graphNetwork.destroy();
  }
  graphNetwork = new vis.Network(container, data, options);
}

function renderResult(result) {
  resultsSection.classList.remove("hidden");

  const verdict = result.novelty.verdict;
  verdictBadge.textContent = verdict;
  verdictBadge.className = `px-3 py-1 rounded-full text-sm font-medium ${verdictClass(verdict)}`;
  noveltyScore.textContent = result.novelty.novelty_score;
  sourceTitle.textContent = result.source.title;
  sourceType.textContent = `${result.source.source_type} (${result.source.segment_count} segments)`;

  renderSourceMeta(result.source || {});
  renderTagOverlap(result.novelty.tag_overlap || []);
  renderOverlap(result.novelty.overlapping_notes || []);
  renderNovel(result.novelty.novel_chunks || []);
  renderSuggestions(result.suggestions || []);
  renderWarnings(result.warnings || []);
  renderGraph(result.graph);

  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runAnalysis({ resume = false } = {}) {
  analyzeError.classList.add("hidden");
  analyzeBtn.disabled = true;
  continueBtn.disabled = true;
  analyzeBtn.textContent = resume ? "Continuing..." : "Analyzing...";
  applyMessage.textContent = "";
  renderWarnings([]);
  beginProgress(resume ? "Continuing interrupted run..." : "Starting analysis...");

  try {
    if (!resume) {
      await ensureFreshIndex();
    }
    const formData = new FormData();
    const url = sourceUrlInput.value.trim();
    const file = sourceFileInput.files[0];

    if (!url && !file) {
      throw new Error(
        resume
          ? "Re-select the same source (URL or file) to continue the interrupted run."
          : "Provide a URL or upload a file."
      );
    }
    if (url) formData.append("url", url);
    if (file) formData.append("file", file);
    if (resume) formData.append("resume", "true");

    let streamError = null;
    const liveWarnings = [];
    await streamNdjson(
      "/api/sources/analyze",
      { method: "POST", body: formData },
      (event) => {
        if (event.type === "progress") {
          showProgress(event);
        } else if (event.type === "warning") {
          liveWarnings.push(event.message);
          renderWarnings(liveWarnings);
        } else if (event.type === "result") {
          renderResult(event);
        } else if (event.type === "error") {
          streamError = new Error(event.message);
          const partial = event.partial_suggestions || [];
          if (partial.length) {
            resultsSection.classList.remove("hidden");
            renderSuggestions(partial);
            renderWarnings([
              ...liveWarnings,
              `Generation stopped early (${event.message}). Recovered ${partial.length} saved note(s).`,
            ]);
          }
        }
      }
    );

    if (streamError) throw streamError;
  } catch (error) {
    analyzeError.textContent = error.message;
    analyzeError.classList.remove("hidden");
  } finally {
    resetProgress();
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
    // The run either finished (checkpoint completed) or failed again; refresh
    // the Continue button to reflect the latest checkpoint state.
    refreshResumeState();
  }
}

// Show the Continue button only when the last run was left incomplete.
async function refreshResumeState() {
  try {
    const data = await fetchJson("/api/suggestions/checkpoint");
    const saved = (data && data.suggestions) || [];
    const resumable = data && data.exists && !data.completed && saved.length > 0;
    if (resumable) {
      const label = (data.source && data.source.title) || "last source";
      continueBtn.textContent = `Continue interrupted run (${saved.length} saved)`;
      continueBtn.title = `Draft the notes still missing for "${label}". Re-select the same source first.`;
      continueBtn.classList.remove("hidden");
    } else {
      continueBtn.classList.add("hidden");
    }
  } catch (error) {
    continueBtn.classList.add("hidden");
  }
}

analyzeBtn.addEventListener("click", () => runAnalysis({ resume: false }));
continueBtn.addEventListener("click", () => runAnalysis({ resume: true }));

recoverBtn.addEventListener("click", async () => {
  analyzeError.classList.add("hidden");
  applyMessage.textContent = "";
  recoverBtn.disabled = true;

  try {
    const data = await fetchJson("/api/suggestions/checkpoint");
    const saved = (data && data.suggestions) || [];
    if (!data.exists || !saved.length) {
      applyMessage.textContent = "No saved notes to recover yet.";
      applyMessage.className = "text-sm text-slate-500";
      return;
    }

    resultsSection.classList.remove("hidden");
    verdictBadge.textContent = "Recovered";
    verdictBadge.className = "px-3 py-1 rounded-full text-sm font-medium bg-slate-100 text-slate-700";
    noveltyScore.textContent = "—";
    sourceTitle.textContent = (data.source && data.source.title) || "(recovered run)";
    sourceType.textContent = (data.source && data.source.source_type) || "";

    renderOverlap([]);
    renderNovel([]);
    renderSuggestions(saved);
    renderGraph(null);

    const status = data.completed ? "completed run" : "interrupted run";
    renderWarnings([
      ...(data.warnings || []),
      `Recovered ${saved.length} saved note(s) from the last ${status}.`,
    ]);
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    analyzeError.textContent = error.message;
    analyzeError.classList.remove("hidden");
  } finally {
    recoverBtn.disabled = false;
  }
});

pageSizeSelect.addEventListener("change", (event) => {
  const value = event.target.value;
  pageSize = value === "all" ? "all" : Number(value);
  currentPage = 1;
  renderSuggestionsPage();
});

prevPageBtn.addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    renderSuggestionsPage();
  }
});

nextPageBtn.addEventListener("click", () => {
  if (currentPage < pageCount()) {
    currentPage += 1;
    renderSuggestionsPage();
  }
});

selectAllBtn.addEventListener("click", () => {
  currentSuggestions.forEach((item) => {
    item.selected = true;
  });
  renderSuggestionsPage();
});

clearAllBtn.addEventListener("click", () => {
  currentSuggestions.forEach((item) => {
    item.selected = false;
  });
  renderSuggestionsPage();
});

writeAllBtn.addEventListener("click", async () => {
  applyMessage.textContent = "";
  writeAllBtn.disabled = true;

  try {
    if (!currentSuggestions.length) {
      applyMessage.textContent = "Analyze a source first — there are no notes to write.";
      applyMessage.className = "text-sm text-red-600";
      return;
    }

    const vaultPath = vaultPathInput.value.trim();
    if (!vaultPath) {
      applyMessage.textContent =
        "Enter your Obsidian vault path in the Vault section above, then try again.";
      applyMessage.className = "text-sm text-red-600";
      return;
    }

    // Build the payload defensively: coerce to strings and drop any note that
    // lost its target path, so a single bad row can never break the whole write.
    const notes = [];
    let skipped = 0;
    for (const item of currentSuggestions) {
      if (!item.selected) continue;
      const notePath = String(item.note_path ?? "").trim();
      if (!notePath) {
        skipped += 1;
        continue;
      }
      notes.push({
        note_path: notePath,
        content: String(item.content ?? ""),
        mode: item.write_mode === "append" ? "append" : "write",
        overwrite: false,
        append_heading: item.write_mode === "append" ? item.append_heading || null : null,
      });
    }

    if (!notes.length) {
      applyMessage.textContent = skipped
        ? "Selected notes are missing a vault path. Fill in the path field, then try again."
        : "Select at least one note to write.";
      applyMessage.className = "text-sm text-red-600";
      return;
    }

    let result = await fetchJson("/api/suggestions/apply-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes, vault_path: vaultPath }),
    });

    const existing = result.skipped_existing || [];
    if (existing.length) {
      const confirmed = window.confirm(
        `${existing.length} note(s) already exist in the vault:\n\n` +
          existing.slice(0, 8).join("\n") +
          (existing.length > 8 ? `\n…and ${existing.length - 8} more` : "") +
          "\n\nOverwrite them? A .bak backup is kept for each replaced file."
      );
      if (confirmed) {
        const retry = notes
          .filter((n) => existing.includes(n.note_path))
          .map((n) => ({ ...n, overwrite: true }));
        const overwriteResult = await fetchJson("/api/suggestions/apply-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notes: retry, vault_path: vaultPath }),
        });
        result = {
          ...overwriteResult,
          written_paths: [
            ...(result.written_paths || []),
            ...(overwriteResult.written_paths || []),
          ],
          count: (result.count || 0) + (overwriteResult.count || 0),
          errors: [...(result.errors || []), ...(overwriteResult.errors || [])],
          skipped_existing: overwriteResult.skipped_existing || [],
          index_refresh: overwriteResult.index_refresh || result.index_refresh,
        };
      }
    }

    const parts = [];
    if (result.count) parts.push(`Wrote ${result.count} note(s)`);
    if ((result.skipped_existing || []).length) {
      parts.push(`skipped ${result.skipped_existing.length} existing (not overwritten)`);
    }
    if ((result.errors || []).length) {
      parts.push(`${result.errors.length} failed`);
    }
    if (skipped) parts.push(`skipped ${skipped} without a path`);

    const hasErrors = (result.errors || []).length > 0;
    const onlySkipped = !result.count && (result.skipped_existing || []).length > 0;
    const refreshWarning = result.index_refresh && result.index_refresh.warning;
    applyMessage.textContent =
      (parts.join("; ") || "Nothing written.") +
      (result.written_paths?.length
        ? `: ${result.written_paths.slice(0, 5).join(", ")}` +
          (result.written_paths.length > 5
            ? ` (+${result.written_paths.length - 5} more)`
            : "")
        : "");
    applyMessage.className = hasErrors || onlySkipped || refreshWarning
      ? "text-sm text-amber-700"
      : "text-sm text-emerald-700";

    if (hasErrors) {
      const detail = result.errors
        .map((e) => `${e.note_path}: ${e.error}`)
        .join("; ");
      applyMessage.textContent += ` — ${detail}`;
    }
    if (refreshWarning) {
      applyMessage.textContent += ` · ${refreshWarning}`;
    } else if (result.index_refresh && result.count) {
      const refreshed = result.index_refresh.indexed_notes ?? result.index_refresh.chunk_count_added;
      applyMessage.textContent += ` · re-indexed ${refreshed} note(s)`;
      await loadStatus();
    }
  } catch (error) {
    applyMessage.textContent = `Could not write notes: ${error.message}`;
    applyMessage.className = "text-sm text-red-600";
  } finally {
    writeAllBtn.disabled = false;
  }
});

if (vaultWatchToggle) {
  vaultWatchToggle.addEventListener("change", async () => {
    const enabled = vaultWatchToggle.checked;
    vaultWatchToggle.disabled = true;
    try {
      await fetchJson("/api/vault/watch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      await loadStatus();
    } catch (error) {
      vaultWatchToggle.checked = !enabled;
      statusBox.textContent = `Vault watch failed: ${error.message}`;
      statusBox.className = "text-sm text-red-600";
    } finally {
      vaultWatchToggle.disabled = false;
    }
  });
}

loadStatus();
refreshResumeState();
