const test = require("node:test");
const assert = require("node:assert/strict");

const shared = require("../shared/api-client.js");

test("shared NDJSON parser handles fragmented lines", () => {
  const events = [];
  const parser = shared.createNdjsonParser((event) => events.push(event));
  parser.push('{"type":"progress","cur');
  parser.push('rent":1}\n{"type":"done"}');
  parser.finish();
  assert.deepEqual(events, [
    { type: "progress", current: 1 },
    { type: "done" },
  ]);
});

test("shared preview and apply payloads stay aligned", () => {
  const preview = shared.buildPreviewPayload(
    { note_path: "a.md", content: "body", write_mode: "write" },
    "/vault"
  );
  const apply = shared.buildApplyNote({ note_path: "a.md", content: "body", write_mode: "write" });
  assert.equal(preview.note_path, apply.note_path);
  assert.equal(preview.content, apply.content);
});

test("extractErrorDetail prefers FastAPI detail strings", () => {
  assert.equal(shared.extractErrorDetail({ detail: "nope" }), "nope");
  assert.equal(shared.extractErrorDetail({ detail: [{ msg: "bad path" }] }), "bad path");
});
