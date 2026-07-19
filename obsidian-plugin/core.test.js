const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildApplyNote,
  buildSelectedApplyNotes,
  consumeNdjsonLines,
  editorMarkdown,
  isAnalyzeAbortError,
  parseNdjsonStream,
  suggestionPageCount,
  suggestionPageSlice,
} = require("./core");

test("NDJSON parser preserves partial lines between chunks", () => {
  const events = [];
  let buffer = consumeNdjsonLines('{"type":"progress"}\n{"type":"res', (event) =>
    events.push(event)
  );
  buffer = consumeNdjsonLines(`${buffer}ult","suggestions":[]}\n`, (event) =>
    events.push(event)
  );

  assert.equal(buffer, "");
  assert.deepEqual(events.map((event) => event.type), ["progress", "result"]);
});

test("NDJSON result and partial error behavior are stable", () => {
  const result = parseNdjsonStream('{"type":"result","suggestions":[{"note_path":"a.md"}]}');
  assert.equal(result.suggestions[0].note_path, "a.md");

  assert.throws(
    () =>
      parseNdjsonStream(
        '{"type":"error","message":"stopped","partial_suggestions":[{"note_path":"saved.md"}]}'
      ),
    (error) => error.message === "stopped" && error.partialSuggestions[0].note_path === "saved.md"
  );
});

test("preview and apply use the same append payload", () => {
  const note = buildApplyNote({
    note_path: "new.md",
    append_target: "existing.md#Details",
    append_heading: "Facts",
    write_mode: "append",
    content: "New material",
  });

  assert.deepEqual(note, {
    note_path: "existing.md",
    content: "New material",
    mode: "append",
    overwrite: false,
    append_heading: "Facts",
  });
});

test("selected apply payload includes only selected notes", () => {
  const notes = buildSelectedApplyNotes(
    [
      { note_path: "a.md", content: "A", write_mode: "write" },
      { note_path: "b.md", content: "B", write_mode: "write" },
    ],
    new Set([1]),
    true
  );

  assert.equal(notes.length, 1);
  assert.equal(notes[0].note_path, "b.md");
  assert.equal(notes[0].overwrite, true);
});

test("editor content and selection come from the live buffer", () => {
  const view = {
    editor: {
      getValue: () => "unsaved **Markdown**",
      getSelection: () => "[[Selected note]]",
    },
  };

  assert.equal(editorMarkdown(view), "unsaved **Markdown**");
  assert.equal(editorMarkdown(view, true), "[[Selected note]]");
});

test("suggestion pagination slices pages like the web UI", () => {
  assert.equal(suggestionPageCount(25, 10), 3);
  assert.deepEqual(suggestionPageSlice(25, 2, 10), {
    start: 10,
    end: 20,
    page: 2,
    pages: 3,
  });
  assert.deepEqual(suggestionPageSlice(5, 9, 10), {
    start: 0,
    end: 5,
    page: 1,
    pages: 1,
  });
  assert.deepEqual(suggestionPageSlice(12, 1, "all"), {
    start: 0,
    end: 12,
    page: 1,
    pages: 1,
  });
});

test("analyze abort errors are detected", () => {
  const abort = new Error("Analysis canceled");
  abort.name = "AbortError";
  assert.equal(isAnalyzeAbortError(abort), true);
  assert.equal(isAnalyzeAbortError(new Error("boom")), false);
});
