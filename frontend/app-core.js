(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.KdaWebCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

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
    return {
      vault_path: String(vaultPath || "").trim() || null,
      note_path: String(suggestion?.note_path || suggestion?.append_target || "").trim(),
      content: String(suggestion?.content ?? ""),
      mode: suggestion?.write_mode === "append" ? "append" : "write",
      overwrite: false,
      append_heading:
        suggestion?.write_mode === "append" ? suggestion?.append_heading || null : null,
    };
  }

  function isAbortError(error) {
    return error?.name === "AbortError";
  }

  return {
    ALLOWED_FILE_EXTENSIONS,
    buildAnalyzeFormData,
    buildPreviewPayload,
    createNdjsonParser,
    escapeHtml,
    isAbortError,
    markdownToSafeHtml,
    readNdjsonResponse,
    sourceInputState,
    validateSourceInput,
  };
});
