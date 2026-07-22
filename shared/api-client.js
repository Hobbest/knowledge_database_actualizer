(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.KdaApiClient = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

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

  function createNdjsonParser(onEvent) {
    let buffer = "";
    const parseLine = (line) => {
      const trimmed = line.trim();
      if (trimmed) onEvent(JSON.parse(trimmed));
    };
    return {
      push(text) {
        buffer += text;
        let newlineIndex;
        while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
          parseLine(buffer.slice(0, newlineIndex));
          buffer = buffer.slice(newlineIndex + 1);
        }
      },
      finish() {
        parseLine(buffer);
        buffer = "";
      },
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

  function buildPreviewPayload(suggestion, vaultPath) {
    return {
      vault_path: String(vaultPath || "").trim() || null,
      note_path: String(suggestion?.note_path || suggestion?.append_target || "").trim(),
      content: String(suggestion?.content ?? ""),
      mode: suggestion?.write_mode === "append" ? "append" : "write",
      overwrite:
        suggestion?.write_mode === "append" ? false : suggestion?.preview_overwrite !== false,
      append_heading:
        suggestion?.write_mode === "append" ? suggestion?.append_heading || null : null,
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

  function extractErrorDetail(payload, fallback = "Request failed") {
    if (!payload) return fallback;
    if (typeof payload === "string") return payload;
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      return payload.detail
        .map((item) => (typeof item === "string" ? item : item?.msg || JSON.stringify(item)))
        .join("; ");
    }
    if (payload.message) return String(payload.message);
    return fallback;
  }

  function isAbortError(error) {
    if (!error) return false;
    if (error.name === "AbortError") return true;
    return /analysis canceled|The operation was aborted|aborted/i.test(
      String(error.message || error)
    );
  }

  return {
    buildApplyNote,
    buildPreviewPayload,
    consumeNdjsonLines,
    createNdjsonParser,
    extractErrorDetail,
    isAbortError,
    parseAppendTarget,
  };
});
