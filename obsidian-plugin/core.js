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

module.exports = {
  buildApplyNote,
  buildSelectedApplyNotes,
  consumeNdjsonLines,
  editorMarkdown,
  isAnalyzeAbortError,
  parseAppendTarget,
  parseNdjsonStream,
  suggestionPageCount,
  suggestionPageSlice,
};
