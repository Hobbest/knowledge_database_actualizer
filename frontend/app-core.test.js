const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildAnalyzeFormData,
  buildPreviewPayload,
  createNdjsonParser,
  markdownToSafeHtml,
  readNdjsonResponse,
  sourceInputState,
  validateSourceInput,
} = require("./app-core.js");

class FakeFormData {
  constructor() {
    this.entries = [];
  }

  append(key, value) {
    this.entries.push([key, value]);
  }
}

test("NDJSON parser handles fragmented and final lines", () => {
  const events = [];
  const parser = createNdjsonParser((event) => events.push(event));
  parser.push('{"type":"progress","cur');
  parser.push('rent":1}\n\n{"type":"result"');
  parser.push(',"ok":true}');
  parser.finish();
  assert.deepEqual(events, [
    { type: "progress", current: 1 },
    { type: "result", ok: true },
  ]);
});

test("stream reading responds to AbortController cancellation", async () => {
  let resolveRead;
  let canceled = false;
  const reader = {
    read() {
      return new Promise((resolve) => {
        resolveRead = resolve;
      });
    },
    cancel() {
      canceled = true;
      resolveRead?.({ done: true });
      return Promise.resolve();
    },
    releaseLock() {},
  };
  const controller = new AbortController();
  const consuming = readNdjsonResponse(
    { body: { getReader: () => reader } },
    () => {},
    controller.signal
  );
  controller.abort(new DOMException("Analysis canceled", "AbortError"));
  await assert.rejects(consuming, { name: "AbortError" });
  assert.equal(canceled, true);
});

test("preview payload matches canonical preview API contract", () => {
  assert.deepEqual(
    buildPreviewPayload(
      {
        note_path: "topics/existing.md",
        content: "---\ntags: [new]\n---\nNew evidence",
        write_mode: "append",
        append_heading: "Evidence",
      },
      "/vault"
    ),
    {
      vault_path: "/vault",
      note_path: "topics/existing.md",
      content: "---\ntags: [new]\n---\nNew evidence",
      mode: "append",
      overwrite: false,
      append_heading: "Evidence",
    }
  );
});

test("analyze form includes request vault and analyze-in-place path", () => {
  const form = buildAnalyzeFormData(
    {
      url: "https://example.com/article",
      file: null,
      resume: true,
      vaultNotePath: "inbox/source.md",
      vaultPath: "/vault",
    },
    FakeFormData
  );
  assert.deepEqual(form.entries, [
    ["url", "https://example.com/article"],
    ["resume", "true"],
    ["vault_note_path", "inbox/source.md"],
    ["vault_path", "/vault"],
  ]);
});

test("source inputs are mutually exclusive and reject invalid combinations", () => {
  const file = { name: "source.md", size: 12 };
  assert.deepEqual(sourceInputState({ url: "", file }), {
    urlDisabled: true,
    fileDisabled: false,
  });
  assert.deepEqual(sourceInputState({ url: "https://example.com", file: null }), {
    urlDisabled: false,
    fileDisabled: true,
  });
  assert.match(validateSourceInput({ url: "https://example.com", file }).message, /either/i);
  assert.match(validateSourceInput({ url: "javascript:alert(1)", file: null }).message, /HTTP/i);
  assert.match(
    validateSourceInput({ url: "", file: { name: "source.exe", size: 12 } }).message,
    /Unsupported/
  );
});

test("rendered Markdown escapes HTML and unsafe links", () => {
  const rendered = markdownToSafeHtml(
    '# Safe\n<script>alert("x")</script>\n[bad](javascript:alert(1))\n[good](https://example.com)'
  );
  assert.doesNotMatch(rendered, /<script>/);
  assert.doesNotMatch(rendered, /href="javascript:/);
  assert.match(rendered, /&lt;script&gt;/);
  assert.match(rendered, /href="https:\/\/example.com"/);
});
