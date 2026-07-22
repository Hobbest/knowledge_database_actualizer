const shared = require("../shared/api-client.js");

function buildApplyNote(item, overwrite = false) {
  return shared.buildApplyNote(item, overwrite);
}

function buildSelectedApplyNotes(suggestions, selectedIndexes, overwrite = false) {
  return Array.from(selectedIndexes)
    .map((index) => suggestions[index])
    .filter(Boolean)
    .map((item) => buildApplyNote(item, overwrite));
}

function consumeNdjsonLines(buffer, onEvent) {
  return shared.consumeNdjsonLines(buffer, onEvent);
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
  return shared.isAbortError(error);
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
  parseAppendTarget: shared.parseAppendTarget,
  parseNdjsonStream,
  suggestionPageCount,
  suggestionPageSlice,
  extractErrorDetail: shared.extractErrorDetail,
};
