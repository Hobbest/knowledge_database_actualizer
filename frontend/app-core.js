(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KdaWebCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const globalRoot =
    root ||
    (typeof globalThis !== "undefined"
      ? globalThis
      : typeof window !== "undefined"
        ? window
        : {});

  const ALLOWED_FILE_EXTENSIONS = [".txt", ".md", ".markdown", ".pdf", ".epub", ".docx"];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeLink(value) {
    const raw = String(value || "").trim();
    try {
      const parsed = new URL(raw, "https://local.invalid/");
      if (!["http:", "https:", "mailto:"].includes(parsed.protocol)) return null;
      return raw;
    } catch (_error) {
      return null;
    }
  }

  function inlineMarkdown(value) {
    let text = escapeHtml(value);
    text = text.replace(/`([^`\n]+)`/g, "<code class=\"rounded bg-slate-100 px-1\">$1</code>");
    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_match, label, href) => {
      const safeHref = safeLink(href);
      return safeHref
        ? `<a class="text-indigo-700 underline" href="${escapeHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : `${label} (${escapeHtml(href)})`;
    });
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    text = text.replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s).,!?:;])/g, "$1<em>$2</em>");
    return text;
  }

  function markdownToSafeHtml(markdown) {
    const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let inCode = false;
    let codeLines = [];
    let listType = null;

    const closeList = () => {
      if (listType) output.push(`</${listType}>`);
      listType = null;
    };

    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        closeList();
        if (inCode) {
          output.push(`<pre class="overflow-auto rounded bg-slate-900 p-3 text-slate-100"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
          codeLines = [];
        }
        inCode = !inCode;
        continue;
      }
      if (inCode) {
        codeLines.push(line);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        output.push(`<h${level} class="font-semibold mt-3 mb-1">${inlineMarkdown(heading[2])}</h${level}>`);
      } else if (unordered || ordered) {
        const nextType = unordered ? "ul" : "ol";
        if (listType !== nextType) {
          closeList();
          listType = nextType;
          output.push(`<${listType} class="${listType === "ul" ? "list-disc" : "list-decimal"} ml-5 space-y-1">`);
        }
        output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      } else if (/^\s*>\s?/.test(line)) {
        closeList();
        output.push(`<blockquote class="border-l-4 border-slate-300 pl-3 text-slate-600">${inlineMarkdown(line.replace(/^\s*>\s?/, ""))}</blockquote>`);
      } else if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        closeList();
        output.push("<hr class=\"my-3 border-slate-300\" />");
      } else if (!line.trim()) {
        closeList();
      } else {
        closeList();
        output.push(`<p class="my-2">${inlineMarkdown(line)}</p>`);
      }
    }
    closeList();
    if (inCode || codeLines.length) {
      output.push(`<pre class="overflow-auto rounded bg-slate-900 p-3 text-slate-100"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    }
    return output.join("");
  }

  function createNdjsonParser(onEvent) {
    if (typeof require === "function") {
      try {
        const shared = require("../shared/api-client.js");
        return shared.createNdjsonParser(onEvent);
      } catch (_error) {
        /* browser bundle without shared module */
      }
    }
    if (globalRoot.KdaApiClient?.createNdjsonParser) {
      return globalRoot.KdaApiClient.createNdjsonParser(onEvent);
    }
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

  function abortError() {
    if (typeof DOMException === "function") return new DOMException("Analysis canceled", "AbortError");
    const error = new Error("Analysis canceled");
    error.name = "AbortError";
    return error;
  }

  function throwIfAborted(signal) {
    if (signal?.aborted) throw signal.reason?.name === "AbortError" ? signal.reason : abortError();
  }

  async function readNdjsonResponse(response, onEvent, signal) {
    if (!response.body?.getReader) throw new Error("Streaming response is not supported by this browser.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = createNdjsonParser(onEvent);
    const cancelReader = () => reader.cancel(signal?.reason).catch(() => {});
    signal?.addEventListener("abort", cancelReader, { once: true });
    try {
      while (true) {
        throwIfAborted(signal);
        const { value, done } = await reader.read();
        throwIfAborted(signal);
        if (done) break;
        parser.push(decoder.decode(value, { stream: true }));
      }
      parser.push(decoder.decode());
      parser.finish();
    } finally {
      signal?.removeEventListener("abort", cancelReader);
      reader.releaseLock?.();
    }
  }

  function validateSourceInput({ url, file }) {
    const cleanUrl = String(url || "").trim();
    if (cleanUrl && file) return { valid: false, message: "Choose either a URL or a file, not both." };
    if (!cleanUrl && !file) return { valid: false, message: "Provide a URL or upload a file." };
    if (cleanUrl) {
      try {
        const parsed = new URL(cleanUrl);
        if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
      } catch (_error) {
        return { valid: false, message: "Enter a valid HTTP or HTTPS source URL." };
      }
    }
    if (file) {
      const lowerName = String(file.name || "").toLowerCase();
      if (!ALLOWED_FILE_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
        return {
          valid: false,
          message: `Unsupported file type. Choose ${ALLOWED_FILE_EXTENSIONS.join(", ")}.`,
        };
      }
      if (Number(file.size) === 0) return { valid: false, message: "The selected file is empty." };
    }
    return { valid: true, message: "", url: cleanUrl, file: file || null };
  }

  function sourceInputState({ url, file }) {
    return {
      urlDisabled: Boolean(file),
      fileDisabled: Boolean(String(url || "").trim()),
    };
  }

  function buildAnalyzeFormData(
    { url, file, resume = false, vaultNotePath = "", vaultPath = "" },
    FormDataCtor = FormData
  ) {
    const validation = validateSourceInput({ url, file });
    if (!validation.valid) throw new Error(validation.message);
    const formData = new FormDataCtor();
    if (validation.url) formData.append("url", validation.url);
    if (validation.file) formData.append("file", validation.file);
    if (resume) formData.append("resume", "true");
    if (String(vaultNotePath || "").trim()) {
      formData.append("vault_note_path", String(vaultNotePath).trim());
    }
    if (String(vaultPath || "").trim()) formData.append("vault_path", String(vaultPath).trim());
    return formData;
  }

  function buildPreviewPayload(suggestion, vaultPath) {
    if (typeof require === "function") {
      try {
        return require("../shared/api-client.js").buildPreviewPayload(suggestion, vaultPath);
      } catch (_error) {
        /* browser bundle */
      }
    }
    if (globalRoot.KdaApiClient?.buildPreviewPayload) {
      return globalRoot.KdaApiClient.buildPreviewPayload(suggestion, vaultPath);
    }
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

  function buildSearchResultHtml(result) {
    const title = escapeHtml(result?.note_title || result?.note_path || "Untitled note");
    const path = escapeHtml(result?.note_path || "");
    const heading = result?.heading
      ? `<span class="text-slate-500"> · ${escapeHtml(result.heading)}</span>`
      : "";
    const score = Number.isFinite(Number(result?.score))
      ? `<span class="text-xs text-slate-500">Score: ${escapeHtml(result.score)}</span>`
      : "";
    const uri = String(result?.obsidian_uri || "");
    const openLink = uri.startsWith("obsidian://")
      ? `<a href="${escapeHtml(uri)}" class="text-xs text-indigo-600 hover:underline">Open in Obsidian</a>`
      : "";
    return `<li class="border rounded-lg p-3 space-y-1">
      <div class="flex items-start justify-between gap-2"><div class="font-medium">${title}${heading}</div>${openLink}</div>
      <div class="text-xs text-slate-500">${path}</div>
      <div class="text-sm text-slate-700 whitespace-pre-wrap">${escapeHtml(result?.snippet || "")}</div>
      ${score}
    </li>`;
  }

  function lineDiff(before, after) {
    const left = String(before ?? "").replace(/\r\n?/g, "\n").split("\n");
    const right = String(after ?? "").replace(/\r\n?/g, "\n").split("\n");
    // Bound quadratic work for unusually large notes.
    if (left.length * right.length > 1_000_000) {
      if (String(before ?? "") === String(after ?? "")) {
        return left.map((line) => ({ type: "same", line }));
      }
      return [
        ...left.map((line) => ({ type: "remove", line })),
        ...right.map((line) => ({ type: "add", line })),
      ];
    }
    const table = Array.from({ length: left.length + 1 }, () =>
      new Uint32Array(right.length + 1)
    );
    for (let i = left.length - 1; i >= 0; i -= 1) {
      for (let j = right.length - 1; j >= 0; j -= 1) {
        table[i][j] =
          left[i] === right[j]
            ? table[i + 1][j + 1] + 1
            : Math.max(table[i + 1][j], table[i][j + 1]);
      }
    }
    const changes = [];
    let i = 0;
    let j = 0;
    while (i < left.length && j < right.length) {
      if (left[i] === right[j]) {
        changes.push({ type: "same", line: left[i] });
        i += 1;
        j += 1;
      } else if (table[i + 1][j] >= table[i][j + 1]) {
        changes.push({ type: "remove", line: left[i] });
        i += 1;
      } else {
        changes.push({ type: "add", line: right[j] });
        j += 1;
      }
    }
    while (i < left.length) changes.push({ type: "remove", line: left[i++] });
    while (j < right.length) changes.push({ type: "add", line: right[j++] });
    return changes;
  }

  function isAbortError(error) {
    if (typeof require === "function") {
      try {
        return require("../shared/api-client.js").isAbortError(error);
      } catch (_error) {
        /* browser bundle */
      }
    }
    if (globalRoot.KdaApiClient?.isAbortError) {
      return globalRoot.KdaApiClient.isAbortError(error);
    }
    return error?.name === "AbortError";
  }

  return {
    ALLOWED_FILE_EXTENSIONS,
    buildAnalyzeFormData,
    buildPreviewPayload,
    buildSearchResultHtml,
    createNdjsonParser,
    escapeHtml,
    isAbortError,
    lineDiff,
    markdownToSafeHtml,
    readNdjsonResponse,
    sourceInputState,
    validateSourceInput,
  };
});
