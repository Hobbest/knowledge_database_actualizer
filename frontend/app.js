const vaultPathInput = document.getElementById("vaultPath");
const indexBtn = document.getElementById("indexBtn");
const calibrateBtn = document.getElementById("calibrateBtn");
const vaultWatchToggle = document.getElementById("vaultWatchToggle");
const vaultSwitcherWrap = document.getElementById("vaultSwitcherWrap");
const vaultSwitcher = document.getElementById("vaultSwitcher");
const statusBox = document.getElementById("statusBox");
const thresholdSummary = document.getElementById("thresholdSummary");
const calibrationResult = document.getElementById("calibrationResult");
const calibrationSummary = document.getElementById("calibrationSummary");
const applyThresholdsBtn = document.getElementById("applyThresholdsBtn");
const vaultSearchForm = document.getElementById("vaultSearchForm");
const vaultSearchQuery = document.getElementById("vaultSearchQuery");
const vaultSearchMode = document.getElementById("vaultSearchMode");
const vaultSearchBtn = document.getElementById("vaultSearchBtn");
const vaultSearchMessage = document.getElementById("vaultSearchMessage");
const vaultSearchResults = document.getElementById("vaultSearchResults");
const vaultChatForm = document.getElementById("vaultChatForm");
const vaultChatQuestion = document.getElementById("vaultChatQuestion");
const vaultChatBtn = document.getElementById("vaultChatBtn");
const vaultChatMessage = document.getElementById("vaultChatMessage");
const vaultChatAnswer = document.getElementById("vaultChatAnswer");
const vaultChatCitations = document.getElementById("vaultChatCitations");
const refreshAnalyticsBtn = document.getElementById("refreshAnalyticsBtn");
const analyticsTotals = document.getElementById("analyticsTotals");
const analyticsBars = document.getElementById("analyticsBars");
const sourceUrlInput = document.getElementById("sourceUrl");
const sourceFileInput = document.getElementById("sourceFile");
const vaultNotePathInput = document.getElementById("vaultNotePath");
const analyzeInPlaceHelp = document.getElementById("analyzeInPlaceHelp");
const analyzeBtn = document.getElementById("analyzeBtn");
const cancelAnalyzeBtn = document.getElementById("cancelAnalyzeBtn");
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
const exportCheckpointsBtn = document.getElementById("exportCheckpointsBtn");
const importCheckpointsInput = document.getElementById("importCheckpointsInput");
const continueBtn = document.getElementById("continueBtn");
const warningsBox = document.getElementById("warningsBox");
const tagOverlapBox = document.getElementById("tagOverlapBox");
const tagOverlapList = document.getElementById("tagOverlapList");
const sourceMeta = document.getElementById("sourceMeta");
const analysisConfig = document.getElementById("analysisConfig");
const exportReportMd = document.getElementById("exportReportMd");
const exportReportHtml = document.getElementById("exportReportHtml");
const graphFilter = document.getElementById("graphFilter");
const graphScope = document.getElementById("graphScope");
const graphExportPng = document.getElementById("graphExportPng");
const graphExportJson = document.getElementById("graphExportJson");
const graphFilterSummary = document.getElementById("graphFilterSummary");
const themeToggle = document.getElementById("themeToggle");
const analyzeDropZone = document.getElementById("analyzeDropZone");
const debugPanel = document.getElementById("debugPanel");
const debugSummary = document.getElementById("debugSummary");
const refreshDebugBtn = document.getElementById("refreshDebugBtn");
const recentLogs = document.getElementById("recentLogs");
const authDialog = document.getElementById("authDialog");
const authForm = document.getElementById("authForm");
const authCancelBtn = document.getElementById("authCancelBtn");
const apiTokenInput = document.getElementById("apiTokenInput");
const authError = document.getElementById("authError");
const {
  buildAnalyzeFormData,
  buildPreviewPayload,
  buildSearchResultHtml,
  escapeHtml,
  isAbortError,
  lineDiff,
  markdownToSafeHtml,
  readNdjsonResponse,
  sourceInputState,
  validateSourceInput,
} = KdaWebCore;

let graphNetwork = null;
let currentGraphData = null;
let currentFilteredGraph = null;
let currentSuggestions = [];
let latestAnalysisResult = null;
let currentPage = 1;
let pageSize = 10;
let obsidianVaultName = null;
let obsidianUriEnabled = false;
let latestStatus = null;
let latestCalibration = null;
let activeAnalyzeController = null;
let pendingAuthRequest = null;
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
  if (pendingAuthRequest) return pendingAuthRequest.promise;

  let resolveRequest;
  let rejectRequest;
  const promise = new Promise((resolve, reject) => {
    resolveRequest = resolve;
    rejectRequest = reject;
  });
  pendingAuthRequest = { promise, resolve: resolveRequest, reject: rejectRequest };
  authError.classList.add("hidden");
  authError.textContent = "";
  apiTokenInput.value = "";
  authDialog.showModal();
  requestAnimationFrame(() => apiTokenInput.focus());
  return promise;
}

function closeAuthDialog(error = null) {
  if (!pendingAuthRequest) return;
  const request = pendingAuthRequest;
  pendingAuthRequest = null;
  authDialog.close();
  if (error) request.reject(error);
  else request.resolve();
}

authForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = apiTokenInput.value.trim();
  if (!token) {
    authError.textContent = "Enter an API token or choose Cancel.";
    authError.classList.remove("hidden");
    apiTokenInput.focus();
    return;
  }
  setApiToken(token);
  closeAuthDialog();
});

authCancelBtn.addEventListener("click", () => {
  closeAuthDialog(new Error("API token entry canceled."));
});

authDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeAuthDialog(new Error("API token entry canceled."));
});

function verdictClass(verdict) {
  if (verdict === "Already known") return "badge-known";
  if (verdict === "Partially new") return "badge-partial";
  return "badge-novel";
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
    case "planning":
      return 40 + ratio * 10;
    case "drafting":
      return 50 + ratio * 50;
    default:
      return Math.max(2, Math.round(ratio * 100));
  }
}

function showProgress(event) {
  analyzeProgress.classList.remove("hidden");
  progressLabel.textContent = event.message || "Working...";
  progressCount.textContent = event.total > 0 ? `${event.current}/${event.total}` : "";
  const progress = Math.max(2, Math.round(computeProgress(event)));
  progressBar.style.width = `${progress}%`;
  analyzeProgress.setAttribute("aria-valuenow", String(progress));
  analyzeProgress.setAttribute("aria-valuetext", progressLabel.textContent);
  // Map LLM planning windows onto the Plan pill (same phase as scoring).
  const stageKey = event.stage === "planning" ? "scoring" : event.stage;
  document.querySelectorAll("#progressStages [data-stage]").forEach((item) => {
    const active = item.dataset.stage === stageKey;
    item.classList.toggle("font-semibold", active);
    item.classList.toggle("text-indigo-700", active);
  });
}

function beginProgress(message = "Starting analysis...") {
  analyzeProgress.classList.remove("hidden");
  progressLabel.textContent = message;
  progressCount.textContent = "";
  progressBar.style.width = "2%";
  analyzeProgress.setAttribute("aria-valuenow", "2");
  analyzeProgress.setAttribute("aria-valuetext", message);
}

function resetProgress() {
  progressBar.style.width = "0%";
  progressCount.textContent = "";
  progressLabel.textContent = "Working...";
  analyzeProgress.setAttribute("aria-valuenow", "0");
  analyzeProgress.removeAttribute("aria-valuetext");
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

  await readNdjsonResponse(response, onEvent, options.signal);
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
    latestStatus = status;
    if (status.auth_required) {
      await ensureApiToken(true);
    }
    if (status.vault_path) {
      vaultPathInput.value = status.vault_path;
    }
    const vaultEntries = Object.values(status.index_meta?.vaults || {});
    if (status.multi_vault_index_enabled && vaultEntries.length) {
      vaultSwitcher.innerHTML = vaultEntries
        .map((entry) => `<option value="${escapeHtml(entry.vault_path)}">${escapeHtml(entry.vault_path)}</option>`)
        .join("");
      vaultSwitcher.value = status.vault_path || vaultEntries[0].vault_path;
      vaultSwitcherWrap.classList.remove("hidden");
    } else {
      vaultSwitcherWrap.classList.add("hidden");
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
      `Analyze in place: ${status.analyze_in_place_enabled ? "enabled" : "disabled"}`,
    ].join(" | ");

    vaultNotePathInput.disabled = !status.analyze_in_place_enabled;
    if (status.analyze_in_place_enabled) {
      analyzeInPlaceHelp.textContent =
        "Enabled by the server. Enter a vault-relative Markdown path to target suggestions to that note.";
    } else {
      vaultNotePathInput.value = "";
      analyzeInPlaceHelp.textContent =
        "Disabled by the server configuration (ANALYZE_IN_PLACE_ENABLED).";
    }

    if (status.llm_enabled && status.llm_budget) {
      const b = status.llm_budget;
      statusBox.textContent += ` | Budget: ${b.max_calls_per_run} calls / ${Number(b.max_input_chars_per_run).toLocaleString()} chars per run`;
    }

    const recommended = status.thresholds && status.thresholds.recommended;
    if (recommended) {
      statusBox.textContent += ` | Thresholds: novel=${status.thresholds.novel} known=${status.thresholds.known}`;
      thresholdSummary.textContent =
        `Current: novel ${status.thresholds.novel}, known ${status.thresholds.known}. ` +
        `Model starting point: novel ${recommended.novel_threshold}, known ${recommended.known_threshold}.`;
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
    const thresholdWarnings = warnings.filter((message) =>
      /threshold/i.test(String(message))
    );
    if (thresholdWarnings.length) {
      thresholdSummary.textContent += ` Recommendation: ${thresholdWarnings.join(" ")}`;
      thresholdSummary.className = "text-amber-700";
    } else {
      thresholdSummary.className = "text-slate-600";
    }
    renderDebugSummary(status);
  } catch (error) {
    statusBox.textContent = `Failed to load status: ${error.message}`;
    statusBox.className = "text-sm text-red-600";
  }
}

function renderDebugSummary(status) {
  const metrics = status.metrics || {};
  const cards = [
    ["Index", `${status.indexed_chunks || 0} chunks · ${status.stale_note_count || 0} stale`],
    ["Requests", `${metrics.requests || 0} · ${metrics.request_errors || 0} errors · ${metrics.average_request_ms || 0} ms avg`],
    ["Analyze", `${metrics.analyze_runs || 0} runs · ${metrics.notes_drafted || 0} notes · ${metrics.llm_calls || 0} LLM calls`],
    ["Graph", `${status.graph_nodes || 0} nodes · ${status.graph_edges || 0} edges`],
    ["Checkpoints", `${(status.incomplete_checkpoints || []).length} incomplete`],
    ["Backend", `${status.embedding_provider}/${status.embedding_model} · ${status.llm_provider || "extractive"}`],
  ];
  debugSummary.innerHTML = cards
    .map(([label, value]) => `<div class="border rounded p-2"><div class="text-xs text-slate-500">${escapeHtml(label)}</div><div>${escapeHtml(value)}</div></div>`)
    .join("");
}

async function refreshDebug() {
  refreshDebugBtn.disabled = true;
  try {
    await loadStatus();
    const result = await fetchJson("/api/debug/recent-logs?limit=100");
    recentLogs.textContent = (result.logs || [])
      .map((item) => `${item.timestamp} ${item.level} ${item.logger}: ${item.message}`)
      .join("\n") || "No logs captured yet.";
  } catch (error) {
    recentLogs.textContent = `Could not load debug data: ${error.message}`;
  } finally {
    refreshDebugBtn.disabled = false;
  }
}

debugPanel.addEventListener("toggle", () => {
  if (debugPanel.open) refreshDebug();
});
refreshDebugBtn.addEventListener("click", refreshDebug);

vaultSearchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = vaultSearchQuery.value.trim();
  if (!query) {
    vaultSearchMessage.textContent = "Enter a search query.";
    return;
  }
  vaultSearchBtn.disabled = true;
  vaultSearchMessage.textContent = "Searching…";
  vaultSearchMessage.className = "text-sm text-slate-500";
  vaultSearchResults.innerHTML = "";
  try {
    const params = new URLSearchParams({
      q: query,
      mode: vaultSearchMode.value,
      top_k: "15",
    });
    const vaultPath = vaultPathInput.value.trim();
    if (vaultPath) params.set("vault_path", vaultPath);
    const result = await fetchJson(`/api/vault/search?${params}`);
    const matches = result.results || [];
    vaultSearchMessage.textContent = `${matches.length} result${matches.length === 1 ? "" : "s"} (${result.mode}).`;
    vaultSearchResults.innerHTML = matches.length
      ? matches.map(buildSearchResultHtml).join("")
      : "<li class='text-sm text-slate-500'>No matching indexed chunks found.</li>";
    const paths = [...new Set(matches.map((item) => item.note_path).filter(Boolean))];
    if (paths.length) {
      const graphData = await fetchJson(
        `/api/vault/graph?highlight=${encodeURIComponent(paths.join(","))}`
      );
      renderGraph(graphData);
    }
  } catch (error) {
    vaultSearchMessage.textContent = `Search failed: ${error.message}`;
    vaultSearchMessage.className = "text-sm text-red-600";
  } finally {
    vaultSearchBtn.disabled = false;
  }
});

vaultSwitcher.addEventListener("change", () => {
  vaultPathInput.value = vaultSwitcher.value;
  statusBox.textContent = "Vault selected. Click Index vault to activate or refresh it.";
});

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
    const thresholds = latestStatus?.thresholds;
    const recommended = thresholds?.recommended;
    const mismatch =
      recommended &&
      (Number(thresholds.novel) !== Number(recommended.novel_threshold) ||
        Number(thresholds.known) !== Number(recommended.known_threshold));
    if (thresholds?.calibration_available && mismatch) {
      calibrateBtn.click();
    }
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
    latestCalibration = result;
    const lines = [
      result.message || "Threshold calibration complete.",
      `Samples: ${result.sample_size} (${result.same_note_samples} same-note, ${result.cross_note_samples} cross-note)`,
      `Recommended: NOVEL=${result.recommended_novel_threshold} KNOWN=${result.recommended_known_threshold}`,
      `Current: NOVEL=${result.current.novel} KNOWN=${result.current.known}`,
    ];
    if (result.fallback) {
      lines.push("(Using provider defaults — not enough data for vault-specific calibration.)");
    }
    calibrationSummary.textContent = lines.join(" ");
    calibrationSummary.className = "text-slate-700";
    calibrationResult.classList.remove("hidden");
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

applyThresholdsBtn.addEventListener("click", async () => {
  if (!latestCalibration) return;
  applyThresholdsBtn.disabled = true;
  try {
    const result = await fetchJson("/api/vault/thresholds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        novel: latestCalibration.recommended_novel_threshold,
        known: latestCalibration.recommended_known_threshold,
        persist: true,
      }),
    });
    calibrationSummary.textContent =
      `Applied novel ${result.novel} and known ${result.known}` +
      (result.persisted ? " to this session and .env." : " to this session.");
    calibrationSummary.className = "text-slate-700";
    await loadStatus();
  } catch (error) {
    calibrationSummary.textContent = `Could not apply thresholds: ${error.message}`;
    calibrationSummary.className = "text-red-600";
  } finally {
    applyThresholdsBtn.disabled = false;
  }
});

async function ensureFreshIndex(signal) {
  const status = await fetchJson("/api/status", { signal });
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
    signal,
  });
  const mode = result.skipped ? "skipped (fresh)" : result.index_mode || "incremental";
  statusBox.textContent = `Re-index (${mode}): ${result.chunk_count ?? result.indexed_chunks ?? "?"} chunks.`;
  await loadStatus();
  resetProgress();
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
  if (source.source_ref) {
    parts.push(`<div><span class="text-slate-500">Source reference:</span> <span class="break-all">${escapeHtml(source.source_ref)}</span></div>`);
  }
  if (source.text_length != null) {
    parts.push(`<div><span class="text-slate-500">Extracted text:</span> ${Number(source.text_length).toLocaleString()} characters</div>`);
  }
  if (source.vault_note_path) {
    parts.push(`<div><span class="text-slate-500">Analyze-in-place target:</span> <code>${escapeHtml(source.vault_note_path)}</code></div>`);
  }
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

function renderAnalysisConfig() {
  if (!latestStatus) {
    analysisConfig.classList.add("hidden");
    return;
  }
  const thresholds = latestStatus.thresholds || {};
  const indexMeta = latestStatus.index_meta || {};
  const activeIndexMeta =
    indexMeta.multi_vault && indexMeta.active_vault
      ? indexMeta.vaults?.[indexMeta.active_vault] || {}
      : indexMeta;
  const values = [
    `Novel threshold: ${thresholds.novel ?? "—"}`,
    `Known threshold: ${thresholds.known ?? "—"}`,
    `Indexed chunks: ${latestStatus.indexed_chunks ?? "—"}`,
    activeIndexMeta.chunk_size ? `Chunk size: ${activeIndexMeta.chunk_size}` : null,
    `Embedding: ${latestStatus.embedding_provider || "—"}/${latestStatus.embedding_model || "—"}`,
  ].filter(Boolean);
  analysisConfig.textContent = `Analysis configuration · ${values.join(" · ")}`;
  analysisConfig.classList.remove("hidden");
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
    novelList.innerHTML = "<li class='text-slate-500'>No chunk-level evidence was returned.</li>";
    return;
  }

  for (const chunk of items) {
    const li = document.createElement("li");
    const isObject = chunk && typeof chunk === "object";
    const text = isObject ? chunk.text_preview || "" : String(chunk);
    const preview = text.length > 420 ? `${text.slice(0, 420)}…` : text;
    let label = "Novel";
    let badgeClass = "bg-emerald-100 text-emerald-800";
    if (isObject && chunk.is_known) {
      label = "Known";
      badgeClass = "bg-red-100 text-red-800";
    } else if (isObject && !chunk.is_novel) {
      label = "Partial";
      badgeClass = "bg-amber-100 text-amber-800";
    }
    li.className = "border rounded-lg p-3 bg-white space-y-2";
    li.innerHTML = `
      <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span class="font-medium">Chunk ${isObject ? Number(chunk.chunk_index) + 1 : "evidence"}</span>
        <span class="inline-flex items-center gap-2">
          ${isObject ? `<span>Best vault similarity: ${escapeHtml(chunk.best_similarity)}</span>` : ""}
          <span class="px-2 py-0.5 rounded-full ${badgeClass}">${label}</span>
        </span>
      </div>
      <div class="text-slate-700 whitespace-pre-wrap">${escapeHtml(preview)}</div>
    `;
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
    selected:
      item.is_moc || item.is_novel === false || item.duplicate_of
        ? false
        : item.selected !== false,
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
    card.dataset.suggestionCard = String(index);
    const mocBadge = suggestion.is_moc
      ? '<span class="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">MOC</span>'
      : "";
    const knownBadge =
      !suggestion.is_moc && suggestion.is_novel === false
        ? '<span class="text-xs px-2 py-0.5 rounded-full bg-slate-200 text-slate-700">Known / partial</span>'
        : "";
    const duplicateBadge = suggestion.duplicate_of
      ? `<span class="text-xs px-2 py-0.5 rounded-full bg-rose-100 text-rose-800" title="Near-duplicate of ${escapeHtml(suggestion.duplicate_of)}">Duplicate${suggestion.duplicate_similarity != null ? ` ${suggestion.duplicate_similarity}` : ""}</span>`
      : "";
    const qualityBadge =
      !suggestion.is_moc && suggestion.quality_score != null
        ? `<span class="text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-800" title="${escapeHtml((suggestion.quality_flags || []).join(", ") || "Heuristic structure score")}">Quality ${Number(suggestion.quality_score).toFixed(2)}</span>`
        : "";
    const appendSectionHint =
      suggestion.append_heading && suggestion.write_mode === "append"
        ? `<div class="text-xs text-slate-500">Appending under section: <code>${escapeHtml(suggestion.append_heading)}</code></div>`
        : "";
    const overlapHint =
      suggestion.append_target && suggestion.overlap_similarity != null
        ? `<div class="text-xs text-slate-500">High overlap (${suggestion.overlap_similarity}) with <code>${escapeHtml(suggestion.append_target)}</code>${suggestion.append_heading ? ` · section <code>${escapeHtml(suggestion.append_heading)}</code>` : ""}</div>`
        : "";
    const duplicateHint = suggestion.duplicate_of
      ? `<div class="text-xs text-rose-700">Near-duplicate of <code>${escapeHtml(suggestion.duplicate_of)}</code>${suggestion.duplicate_similarity != null ? ` (similarity ${suggestion.duplicate_similarity})` : ""}. Deselected by default.</div>`
      : "";
    const qualityHint =
      !suggestion.is_moc && suggestion.quality_score != null && (suggestion.quality_flags || []).length
        ? `<div class="text-xs text-slate-500">Quality flags: ${escapeHtml((suggestion.quality_flags || []).join(", "))}</div>`
        : "";
    const updateHint = suggestion.update_type
      ? `<div class="text-xs text-amber-700"><strong>${escapeHtml(suggestion.update_type === "contradiction" ? "Possible contradiction" : "Possible update")}:</strong> ${escapeHtml(suggestion.update_reason || "")}${suggestion.update_target ? ` Target: <code>${escapeHtml(suggestion.update_target)}</code>` : ""}</div>`
      : "";
    const appendControls =
      suggestion.append_target && !suggestion.is_moc
        ? `<div class="space-y-2 rounded-lg border bg-white p-3">
            <div class="text-xs font-medium text-slate-500">Write mode</div>
            <div class="flex flex-wrap gap-4 text-sm" role="group" aria-label="Write mode for ${escapeHtml(suggestion.concept_title)}">
              <label class="inline-flex items-center gap-2">
                <input type="radio" name="write-mode-${index}" value="write" class="write-mode-radio" data-index="${index}" />
                New file
              </label>
              <label class="inline-flex items-center gap-2">
                <input type="radio" name="write-mode-${index}" value="append" class="write-mode-radio" data-index="${index}" />
                Append to existing
              </label>
            </div>
          </div>`
        : "";

    card.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <label class="flex items-center gap-2 font-medium">
          <input type="checkbox" data-index="${index}" class="suggestion-select" />
          <span>#${index + 1} · ${escapeHtml(suggestion.concept_title)}</span>
          ${mocBadge}
          ${knownBadge}
          ${duplicateBadge}
          ${qualityBadge}
        </label>
        <span class="text-xs px-2 py-1 rounded-full bg-white border text-slate-600 whitespace-nowrap">
          ${escapeHtml(suggestion.location?.display || "unknown location")}
        </span>
      </div>
      ${overlapHint}
      ${duplicateHint}
      ${qualityHint}
      ${updateHint}
      ${appendSectionHint}
      ${appendControls}
      <details class="note-diff-details rounded-lg border bg-white p-3" open>
        <summary class="text-sm font-medium text-indigo-700 cursor-pointer">Exact changes on apply</summary>
        <div class="note-diff mt-2 text-xs"></div>
      </details>
      <div class="space-y-1">
        <label for="note-path-${index}" class="text-xs font-medium text-slate-500">Vault path</label>
        <input id="note-path-${index}" data-field="note_path" data-index="${index}" class="w-full border rounded-lg px-3 py-2 text-sm bg-white" />
      </div>
      <div class="space-y-1">
        <label for="note-content-${index}" class="text-xs font-medium text-slate-500">Note content</label>
        <textarea id="note-content-${index}" data-field="content" data-index="${index}" rows="12" class="w-full border rounded-lg px-3 py-2 font-mono text-sm bg-white"></textarea>
      </div>
      <details class="rendered-preview-details rounded-lg border bg-white p-3">
        <summary class="text-sm font-medium text-indigo-700 cursor-pointer">Rendered Markdown preview</summary>
        <div class="markdown-preview mt-3 border-t pt-2 text-sm"></div>
      </details>
    `;

    card.querySelector(".suggestion-select").checked = suggestion.selected;
    card.querySelector('[data-field="note_path"]').value = suggestion.note_path;
    card.querySelector('[data-field="content"]').value = suggestion.content;
    card.querySelector(".markdown-preview").innerHTML = markdownToSafeHtml(suggestion.content);

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
      if (field === "content") {
        const card = event.target.closest("[data-suggestion-card]");
        const rendered = card?.querySelector(".markdown-preview");
        if (rendered) rendered.innerHTML = markdownToSafeHtml(event.target.value);
        const diffDetails = card?.querySelector(".note-diff-details");
        if (diffDetails?.open) {
          clearTimeout(currentSuggestions[idx]._previewTimer);
          currentSuggestions[idx]._previewTimer = setTimeout(() => refreshNoteDiff(idx), 250);
        }
      }
      if (field === "note_path") {
        const card = event.target.closest("[data-suggestion-card]");
        const diffDetails = card?.querySelector(".note-diff-details");
        if (diffDetails?.open) {
          clearTimeout(currentSuggestions[idx]._previewTimer);
          currentSuggestions[idx]._previewTimer = setTimeout(() => refreshNoteDiff(idx), 250);
        }
      }
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
      await refreshNoteDiff(idx);
    });
  });

  for (let index = start; index < end; index += 1) {
    const suggestion = currentSuggestions[index];
    if (!suggestion._original_note_path) {
      suggestion._original_note_path = suggestion.note_path;
    }
  }

  suggestionsList.querySelectorAll(".note-diff-details").forEach((details) => {
    details.addEventListener("toggle", async (event) => {
      if (!event.target.open) {
        return;
      }
      const card = event.target.closest("[data-suggestion-card]");
      const idx = Number(card?.querySelector("[data-index]")?.dataset.index);
      if (!Number.isNaN(idx)) {
        await refreshNoteDiff(idx);
      }
    });
  });

  for (let index = start; index < end; index += 1) {
    refreshNoteDiff(index);
  }

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

function renderLineDiff(existingContent, finalContent) {
  const changes = lineDiff(existingContent, finalContent);
  return changes
    .map(({ type, line }) => {
      const prefix = type === "add" ? "+" : type === "remove" ? "−" : " ";
      const style =
        type === "add"
          ? "bg-emerald-50 text-emerald-900"
          : type === "remove"
            ? "bg-red-50 text-red-900"
            : "text-slate-600";
      return `<div class="${style} px-2 whitespace-pre-wrap break-all"><span class="select-none inline-block w-4">${prefix}</span>${escapeHtml(line)}</div>`;
    })
    .join("");
}

async function refreshNoteDiff(index) {
  const suggestion = currentSuggestions[index];
  if (!suggestion?.note_path) {
    return;
  }
  const pathInput = suggestionsList.querySelector(`[data-field="note_path"][data-index="${index}"]`);
  const card = pathInput?.closest("[data-suggestion-card]");
  const diffHost = card?.querySelector(".note-diff");
  if (!diffHost) {
    return;
  }
  const requestId = (suggestion._previewRequestId || 0) + 1;
  suggestion._previewRequestId = requestId;
  diffHost.innerHTML = "<div class='text-slate-500' role='status'>Loading canonical preview…</div>";
  try {
    const payload = buildPreviewPayload(suggestion, vaultPathInput.value);
    const preview = await fetchJson("/api/suggestions/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (suggestion._previewRequestId !== requestId) return;
    const writeStatus = preview.will_write
      ? suggestion.write_mode === "append"
        ? "This is the exact content the apply API will write."
        : "This is the exact content written for a new file or after overwrite confirmation."
      : "The apply API would not write this change with the current options.";
    diffHost.innerHTML = `
      <div class="border rounded bg-slate-50 max-h-72 overflow-auto font-mono">${renderLineDiff(preview.existing_content, preview.final_content)}</div>
      <div class="mt-2 text-slate-600">${escapeHtml(preview.exists ? writeStatus : "New file — every line shown above will be added.")}</div>
      <details class="mt-2 rounded border bg-white p-2">
        <summary class="font-medium text-indigo-700 cursor-pointer">Rendered final note</summary>
        <div class="markdown-preview mt-2 border-t pt-2">${markdownToSafeHtml(preview.final_content)}</div>
      </details>
    `;
  } catch (error) {
    if (suggestion._previewRequestId !== requestId) return;
    diffHost.innerHTML = `<div class="text-red-600 col-span-2">${escapeHtml(error.message)}</div>`;
  }
}

function filterGraphData(graphData) {
  const query = graphFilter.value.trim().toLocaleLowerCase();
  const scope = graphScope.value;
  let allowed = new Set(graphData.nodes.map((node) => node.id));
  if (scope !== "all") {
    const highlighted = new Set(
      graphData.nodes.filter((node) => node.highlighted).map((node) => node.id)
    );
    allowed = new Set(highlighted);
    const hops = Number(scope);
    if (Number.isFinite(hops) && hops > 0) {
      let frontier = new Set(highlighted);
      for (let depth = 0; depth < hops; depth += 1) {
        const next = new Set();
        for (const edge of graphData.edges || []) {
          if (frontier.has(edge.from)) next.add(edge.to);
          if (frontier.has(edge.to)) next.add(edge.from);
        }
        next.forEach((id) => allowed.add(id));
        frontier = next;
      }
    }
  }
  const nodes = graphData.nodes.filter((node) => {
    if (!allowed.has(node.id)) return false;
    if (!query) return true;
    const searchable = `${node.label || ""} ${node.id} ${(node.tags || []).join(" ")}`.toLocaleLowerCase();
    return searchable.includes(query);
  });
  const visible = new Set(nodes.map((node) => node.id));
  const edges = (graphData.edges || []).filter(
    (edge) => visible.has(edge.from) && visible.has(edge.to)
  );
  return { nodes, edges };
}

function renderGraphText(graphData) {
  const textContainer = document.getElementById("graphText");
  const labelById = new Map(graphData.nodes.map((node) => [node.id, node.label || node.id]));
  const highlighted = graphData.nodes.filter((node) => node.highlighted);
  const nodeSummary = highlighted.length
    ? `<p><strong>Related notes:</strong> ${highlighted.map((node) => escapeHtml(node.label || node.id)).join(", ")}</p>`
    : `<p>${graphData.nodes.length} notes are shown; none are specifically highlighted.</p>`;
  const edgesList = (graphData.edges || [])
    .map((edge) => {
      const from = labelById.get(edge.from) || edge.from;
      const to = labelById.get(edge.to) || edge.to;
      return `<li>${escapeHtml(from)} links to ${escapeHtml(to)}</li>`;
    })
    .join("");
  textContainer.innerHTML =
    nodeSummary +
    (edgesList
      ? `<p class="mt-2 font-medium">Links (${graphData.edges.length})</p><ul class="list-disc ml-5 mt-1 max-h-64 overflow-auto">${edgesList}</ul>`
      : "<p class='mt-2'>No links between these notes.</p>");
}

function applyGraphFilters() {
  if (!currentGraphData || !graphNetwork) return;
  currentFilteredGraph = filterGraphData(currentGraphData);
  const nodes = new vis.DataSet(
    currentFilteredGraph.nodes.map((node) => ({
      ...node,
      title: (node.tags || []).length ? `Tags: ${(node.tags || []).join(", ")}` : undefined,
      color: node.highlighted ? "#4f46e5" : "#94a3b8",
      font: { color: node.highlighted ? "#1e1b4b" : "#334155" },
    }))
  );
  const edges = new vis.DataSet(
    currentFilteredGraph.edges.map((edge) => {
      const { label, title, ...rest } = edge;
      return { ...rest, title: title || label || undefined };
    })
  );
  graphNetwork.setData({ nodes, edges });
  graphFilterSummary.textContent =
    `Showing ${currentFilteredGraph.nodes.length} of ${currentGraphData.nodes.length} nodes ` +
    `and ${currentFilteredGraph.edges.length} links.`;
  renderGraphText(currentFilteredGraph);
}

function renderGraph(graphData) {
  const container = document.getElementById("graph");
  const textContainer = document.getElementById("graphText");
  if (!graphData || !graphData.nodes || !graphData.nodes.length) {
    if (graphNetwork) {
      graphNetwork.destroy();
      graphNetwork = null;
    }
    currentGraphData = null;
    currentFilteredGraph = null;
    container.innerHTML = "<p class='text-sm text-slate-500 p-4'>No graph data available. Index your vault first.</p>";
    textContainer.innerHTML = "<p>No graph nodes or links are available.</p>";
    graphFilterSummary.textContent = "";
    return;
  }

  container.innerHTML = "";
  const options = {
    physics: { stabilization: true },
    interaction: { hover: true },
    edges: { arrows: "to", color: "#cbd5e1", font: { align: "horizontal" }, smooth: true },
  };

  if (graphNetwork) {
    graphNetwork.destroy();
  }
  graphNetwork = new vis.Network(container, { nodes: [], edges: [] }, options);
  currentGraphData = graphData;
  applyGraphFilters();
}

function renderResult(result) {
  latestAnalysisResult = result;
  resultsSection.classList.remove("hidden");

  const verdict = result.novelty.verdict;
  verdictBadge.textContent = verdict;
  verdictBadge.className = `px-3 py-1 rounded-full text-sm font-medium ${verdictClass(verdict)}`;
  noveltyScore.textContent = result.novelty.novelty_score;
  sourceTitle.textContent = result.source.title;
  sourceType.textContent = `${result.source.source_type} (${result.source.segment_count} segments)`;

  renderSourceMeta(result.source || {});
  renderAnalysisConfig();
  renderTagOverlap(result.novelty.tag_overlap || []);
  renderOverlap(result.novelty.overlapping_notes || []);
  renderNovel(result.novelty.chunk_results || result.novelty.novel_chunks || []);
  renderSuggestions(result.suggestions || []);
  renderWarnings(result.warnings || []);
  renderGraph(result.graph);

  resultsSection.focus({ preventScroll: true });
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function syncSourceInputs() {
  const state = sourceInputState({
    url: sourceUrlInput.value,
    file: sourceFileInput.files[0],
  });
  sourceUrlInput.disabled = state.urlDisabled;
  sourceFileInput.disabled = state.fileDisabled;
}

function clearAnalysisResults() {
  resultsSection.classList.add("hidden");
  currentSuggestions = [];
  latestAnalysisResult = null;
  currentPage = 1;
  applyMessage.textContent = "";
  renderWarnings([]);
  if (graphNetwork) {
    graphNetwork.destroy();
    graphNetwork = null;
  }
}

function setAnalyzing(isRunning, resume = false) {
  analyzeBtn.disabled = isRunning;
  continueBtn.disabled = isRunning;
  recoverBtn.disabled = isRunning;
  sourceUrlInput.disabled = isRunning || sourceInputState({
    url: sourceUrlInput.value,
    file: sourceFileInput.files[0],
  }).urlDisabled;
  sourceFileInput.disabled = isRunning || sourceInputState({
    url: sourceUrlInput.value,
    file: sourceFileInput.files[0],
  }).fileDisabled;
  vaultNotePathInput.disabled =
    isRunning || latestStatus?.analyze_in_place_enabled === false;
  analyzeBtn.textContent = isRunning ? (resume ? "Continuing…" : "Analyzing…") : "Analyze";
  cancelAnalyzeBtn.classList.toggle("hidden", !isRunning);
  cancelAnalyzeBtn.disabled = false;
  cancelAnalyzeBtn.textContent = "Cancel analysis";
  if (!isRunning) syncSourceInputs();
}

async function runAnalysis({ resume = false } = {}) {
  if (activeAnalyzeController) return;
  analyzeError.classList.add("hidden");
  analyzeError.textContent = "";
  clearAnalysisResults();
  const controller = new AbortController();
  activeAnalyzeController = controller;
  setAnalyzing(true, resume);
  beginProgress(resume ? "Continuing interrupted run..." : "Starting analysis...");

  try {
    if (!resume) {
      await ensureFreshIndex(controller.signal);
    }
    const url = sourceUrlInput.value.trim();
    const file = sourceFileInput.files[0];
    const validation = validateSourceInput({ url, file });
    if (!validation.valid) {
      if (resume && !url && !file) {
        throw new Error("Re-select the same source (URL or file) to continue the interrupted run.");
      }
      throw new Error(validation.message);
    }
    const vaultNotePath = vaultNotePathInput.value.trim();
    if (
      vaultNotePath &&
      (vaultNotePath.startsWith("/") ||
        /^[a-zA-Z]:[\\/]/.test(vaultNotePath) ||
        vaultNotePath.includes("\\") ||
        vaultNotePath.split(/[\\/]+/).includes("..") ||
        !vaultNotePath.toLowerCase().endsWith(".md"))
    ) {
      throw new Error("Existing vault note path must be a vault-relative Markdown (.md) path.");
    }
    const formData = buildAnalyzeFormData({
      url,
      file,
      resume,
      vaultNotePath:
        latestStatus?.analyze_in_place_enabled === false ? "" : vaultNotePath,
      vaultPath: vaultPathInput.value,
    });

    let streamError = null;
    const liveWarnings = [];
    await streamNdjson(
      "/api/sources/analyze",
      { method: "POST", body: formData, signal: controller.signal },
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
    if (isAbortError(error) || controller.signal.aborted) {
      clearAnalysisResults();
      analyzeError.textContent = "Analysis canceled. No results from the canceled run are shown.";
    } else {
      analyzeError.textContent = error.message;
      if (/matching interrupted run|checkpoint|source.*match/i.test(error.message)) {
        analyzeError.textContent +=
          " Use Recover last saved notes to inspect the checkpoint, or click Analyze to start a fresh run.";
      }
    }
    analyzeError.classList.remove("hidden");
    analyzeError.focus?.();
  } finally {
    if (activeAnalyzeController === controller) activeAnalyzeController = null;
    resetProgress();
    setAnalyzing(false);
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
cancelAnalyzeBtn.addEventListener("click", () => {
  if (!activeAnalyzeController) return;
  cancelAnalyzeBtn.disabled = true;
  cancelAnalyzeBtn.textContent = "Canceling…";
  if (pendingAuthRequest) {
    closeAuthDialog(new DOMException("Analysis canceled", "AbortError"));
  }
  activeAnalyzeController.abort(new DOMException("Analysis canceled", "AbortError"));
});
sourceUrlInput.addEventListener("input", syncSourceInputs);
sourceFileInput.addEventListener("change", syncSourceInputs);

recoverBtn.addEventListener("click", async () => {
  analyzeError.classList.add("hidden");
  clearAnalysisResults();
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

    renderSourceMeta(data.source || {});
    renderAnalysisConfig();
    renderTagOverlap([]);
    renderOverlap([]);
    renderNovel([]);
    renderSuggestions(saved);
    renderGraph(null);

    const status = data.completed ? "completed run" : "interrupted run";
    renderWarnings([
      ...(data.warnings || []),
      `Recovered ${saved.length} saved note(s) from the last ${status}.`,
    ]);
    resultsSection.focus({ preventScroll: true });
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    analyzeError.textContent = error.message;
    analyzeError.classList.remove("hidden");
  } finally {
    recoverBtn.disabled = false;
  }
});

exportCheckpointsBtn.addEventListener("click", async () => {
  exportCheckpointsBtn.disabled = true;
  try {
    const payload = await fetchJson("/api/suggestions/checkpoint/export");
    downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      "actualizer-checkpoints.json"
    );
  } catch (error) {
    analyzeError.textContent = `Checkpoint export failed: ${error.message}`;
    analyzeError.classList.remove("hidden");
  } finally {
    exportCheckpointsBtn.disabled = false;
  }
});

importCheckpointsInput.addEventListener("change", async () => {
  const file = importCheckpointsInput.files?.[0];
  if (!file) return;
  try {
    if (file.size > 10 * 1024 * 1024) {
      throw new Error("Checkpoint bundle exceeds the 10 MB browser import limit.");
    }
    const payload = JSON.parse(await file.text());
    const result = await fetchJson("/api/suggestions/checkpoint/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    applyMessage.textContent = `Imported ${result.imported} checkpoint(s).`;
    applyMessage.className = "text-sm text-emerald-700";
    await refreshResumeState();
    await loadStatus();
  } catch (error) {
    analyzeError.textContent = `Checkpoint import failed: ${error.message}`;
    analyzeError.classList.remove("hidden");
  } finally {
    importCheckpointsInput.value = "";
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
      body: JSON.stringify({
        notes,
        vault_path: vaultPath,
        source_title: latestAnalysisResult?.source?.title || null,
      }),
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
          body: JSON.stringify({
            notes: retry,
            vault_path: vaultPath,
            source_title: latestAnalysisResult?.source?.title || null,
          }),
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

graphFilter.addEventListener("input", applyGraphFilters);
graphScope.addEventListener("change", applyGraphFilters);

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

graphExportJson.addEventListener("click", () => {
  if (!currentFilteredGraph) return;
  downloadBlob(
    new Blob([JSON.stringify(currentFilteredGraph, null, 2)], {
      type: "application/json",
    }),
    "actualizer-graph.json"
  );
});

graphExportPng.addEventListener("click", () => {
  const canvas = document.querySelector("#graph canvas");
  if (!canvas) return;
  canvas.toBlob((blob) => {
    if (blob) downloadBlob(blob, "actualizer-graph.png");
  }, "image/png");
});

vaultChatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = vaultChatQuestion.value.trim();
  if (!question) return;
  vaultChatBtn.disabled = true;
  vaultChatMessage.textContent = "Retrieving vault context…";
  vaultChatAnswer.classList.add("hidden");
  vaultChatCitations.innerHTML = "";
  try {
    let answer = "";
    let citations = [];
    await streamNdjson(
      "/api/chat",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          vault_path: vaultPathInput.value.trim() || null,
        }),
      },
      (item) => {
        if (item.type === "answer") answer += item.text || "";
        if (item.type === "citations") citations = item.citations || [];
      }
    );
    vaultChatAnswer.textContent = answer;
    vaultChatAnswer.classList.remove("hidden");
    vaultChatCitations.innerHTML = citations
      .map((item) => `<li>[${item.id}] <code>${escapeHtml(item.note_path)}</code>${item.heading ? ` · ${escapeHtml(item.heading)}` : ""} (${item.score})</li>`)
      .join("");
    vaultChatMessage.textContent = `${citations.length} source passage${citations.length === 1 ? "" : "s"} retrieved.`;
  } catch (error) {
    vaultChatMessage.textContent = error.message;
  } finally {
    vaultChatBtn.disabled = false;
  }
});

async function loadAnalytics() {
  try {
    const data = await fetchJson("/api/analytics");
    const totals = data.totals || {};
    analyticsTotals.innerHTML = `
      <div class="rounded-lg bg-slate-50 p-3"><div class="text-slate-500">Sources analyzed</div><div class="text-2xl font-semibold">${Number(totals.analyzed_sources || 0)}</div></div>
      <div class="rounded-lg bg-slate-50 p-3"><div class="text-slate-500">Notes written</div><div class="text-2xl font-semibold">${Number(totals.written_notes || 0)}</div></div>`;
    const days = Object.entries(data.days || {}).sort(([a], [b]) => a.localeCompare(b)).slice(-14);
    const maximum = Math.max(1, ...days.map(([, item]) => Number(item.analyzed_sources || 0) + Number(item.written_notes || 0)));
    analyticsBars.innerHTML = days.map(([day, item]) => {
      const count = Number(item.analyzed_sources || 0) + Number(item.written_notes || 0);
      return `<div class="flex items-center gap-2 text-xs"><span class="w-24">${escapeHtml(day)}</span><div class="h-3 bg-indigo-500 rounded" style="width:${Math.max(2, (count / maximum) * 75)}%"></div><span>${count}</span></div>`;
    }).join("") || '<p class="text-sm text-slate-500">No activity recorded yet.</p>';
  } catch (error) {
    analyticsTotals.textContent = error.message;
  }
}

refreshAnalyticsBtn.addEventListener("click", loadAnalytics);

async function exportAnalysisReport(format) {
  if (!latestAnalysisResult) return;
  const options = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      result: latestAnalysisResult,
      format,
      title: `${latestAnalysisResult.source?.title || "Actualizer"} — analysis report`,
    }),
  };
  let response = await fetch("/api/reports/export", withAuthHeaders(options));
  if (response.status === 401) {
    setApiToken("");
    await ensureApiToken(true);
    response = await fetch("/api/reports/export", withAuthHeaders(options));
  }
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  downloadBlob(
    blob,
    `actualizer-report.${format === "html" ? "html" : "md"}`
  );
}

exportReportMd.addEventListener("click", () => {
  exportAnalysisReport("markdown").catch((error) => {
    applyMessage.textContent = `Report export failed: ${error.message}`;
  });
});
exportReportHtml.addEventListener("click", () => {
  exportAnalysisReport("html").catch((error) => {
    applyMessage.textContent = `Report export failed: ${error.message}`;
  });
});

function setTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  themeToggle.textContent = dark ? "Light mode" : "Dark mode";
  localStorage.setItem("actualizer_theme", dark ? "dark" : "light");
}

themeToggle.addEventListener("click", () => {
  setTheme(!document.documentElement.classList.contains("dark"));
});
setTheme(
  localStorage.getItem("actualizer_theme") === "dark" ||
    (!localStorage.getItem("actualizer_theme") &&
      window.matchMedia?.("(prefers-color-scheme: dark)").matches)
);

["dragenter", "dragover"].forEach((name) => {
  analyzeDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    analyzeDropZone.classList.add("drop-active");
  });
});
["dragleave", "drop"].forEach((name) => {
  analyzeDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    analyzeDropZone.classList.remove("drop-active");
  });
});
analyzeDropZone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  sourceFileInput.files = transfer.files;
  sourceUrlInput.value = "";
  syncSourceInputs();
  analyzeError.textContent = `Ready to analyze dropped file: ${file.name}`;
  analyzeError.className = "text-sm text-slate-600";
});

syncSourceInputs();
loadStatus();
loadAnalytics();
refreshResumeState();
