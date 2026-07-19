# Knowledge Database Actualizer

A local web app that helps you decide whether a knowledge source (YouTube video, web article, PDF, EPUB, DOCX, text, markdown) contains **new information** relative to your Obsidian-style markdown vault.

> For system design (diagrams + module map), see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Features

- Index an Obsidian-compatible markdown vault (YAML frontmatter + `[[wikilinks]]`)
- Embed note chunks locally with `sentence-transformers`
- Compare incoming sources against your vault using ChromaDB similarity search
- Verdicts: **Already known**, **Partially new**, **Novel**
- Propose a draft note with related `[[wikilinks]]` (LLM optional, extractive fallback built-in)
- Approve before writing anything back to your vault
- Visualize the knowledge graph with `vis-network`

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional: copy and edit environment variables
cp .env.example .env

# Run the app (always use the project virtualenv — not Anaconda/base Python)
.venv/bin/uvicorn app.main:app --reload
# or: ./scripts/run_dev_server.sh
```

Open http://127.0.0.1:8000

### Troubleshooting: `google.protobuf` / `FieldDescriptor` ImportError

If startup fails with a protobuf circular-import error, the server is using a broken
global Python (common with Anaconda) instead of this project's `.venv`. The project
virtualenv installs a compatible `protobuf` pin for ChromaDB.

```bash
cd /path/to/knowledge_database_actualizer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Do **not** run `uvicorn` from `anaconda3/bin` unless you have repaired protobuf there.

The UI loads Tailwind and vis-network from `frontend/vendor/` (no CDN), so it works offline once dependencies are installed.

For development (unit tests), use an isolated virtual environment so pytest does
not pick up incompatible packages from a global Anaconda install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/
node --test frontend/app-core.test.js
npm --prefix obsidian-plugin test
```

`scripts/smoke_test.py` remains an optional heavier integration check (real MiniLM
download). CI runs the hermetic `tests/` suite and the client contract tests.

## Workflow

1. Enter your vault path and click **Index vault** (optional: enable **Index on save** to re-index when you edit notes in the vault)
2. Paste a YouTube or article URL, or upload a file (`.txt`, `.md`, `.pdf`, `.epub`, `.docx` — any non-YouTube `http(s)` URL is treated as a web article; the main text is extracted with [trafilatura](https://trafilatura.readthedocs.io/), skipping navigation, ads, and comments)
3. Review the verdict, overlapping notes, and novel snippets
4. Edit the proposed atomic notes (one concept per note, with source location)
5. Select notes and click **Write selected to vault** (known / partial topics and MOCs start deselected — select them to write or append)
   (existing files require confirmation; a `.bak` backup is kept on overwrite)

### Crash / rate-limit recovery

Notes are drafted in small LLM batches (three per call by default), so a long
run can still be interrupted by an LLM rate limit or a restart. Draft
suggestions are saved to a
checkpoint (`data/checkpoints/<source-key>.json`, tracked in
`data/checkpoints/manifest.json`) the moment it is created:

- A rate-limited note is retried with exponential backoff (`LLM_MAX_RETRIES`,
  `LLM_RETRY_BASE_DELAY`, `LLM_RETRY_MAX_DELAY`), since rate limits are usually
  per-minute. During a wait the progress bar shows "Rate limited — waiting Ns".
- Only after the retries are exhausted does that note fall back to an extractive
  summary. If several notes in a row fail (`LLM_DISABLE_AFTER_FAILURES`, quota
  likely gone), the rest of the run uses extractive summaries — re-run later to
  regenerate them with the LLM.
- Click **Recover last saved notes** (or `GET /api/suggestions/checkpoint`) to
  reload the notes from the most recent run, even after a server restart.
- If the last run was left incomplete, a **Continue interrupted run** button
  appears. Re-select the same source and click it to draft only the notes that
  are still missing — notes already saved are reused as-is instead of being
  regenerated. The saved novelty result and topic plan are also reused when
  the source and relevant settings are unchanged, avoiding repeated embedding
  and planning calls. (API: send `resume=true` to `POST /api/sources/analyze` with the
  same source.) YouTube URLs are matched by video id, so `youtu.be/…` and
  `youtube.com/watch?v=…` count as the same source. If the source does not match
  the checkpoint, the run aborts without clearing saved notes; use **Analyze**
  only when you intend to start fresh (that replaces the checkpoint).

> Tip: drafting uses roughly one LLM call per `LLM_DRAFT_BATCH_SIZE` notes,
> plus an optional planning call. A whole book can still hit per-minute quotas.
> Raise `SEGMENT_TARGET_CHARS`, raise `LLM_DRAFT_BATCH_SIZE`, or lower
> `MAX_NOTES_PER_SOURCE` to reduce calls.

## Configuration

**Source of truth:** defaults live in `app/config.py` (`Settings`). The committed
`.env.example` is generated from them — never edit `.env.example` by hand.

```bash
# After changing Settings defaults:
python scripts/env_sync.py generate

# First-time setup (creates .env from the template):
cp .env.example .env
# Edit .env and add API keys locally — .env is gitignored

# When new settings are added upstream, add missing keys without overwriting yours:
python scripts/env_sync.py merge
```

See `.env.example` for:

- `VAULT_PATH` — default vault location
- `NOVEL_THRESHOLD` / `KNOWN_THRESHOLD` — similarity thresholds (0–1)
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` — similarity search (`local` uses sentence-transformers; `gemini` uses Google embedding models)
- `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` — optional note drafting (`openai`, `anthropic`, `gemini`, or `ollama` / `local`)
- `OLLAMA_BASE_URL` — when using `LLM_PROVIDER=ollama` (default `http://127.0.0.1:11434`)
- `API_TOKEN` — optional on localhost; **required** when `BIND_HOST` is non-loopback (Docker sets `0.0.0.0`)
- `ALLOWED_VAULT_ROOTS` — comma-separated roots; when empty and `VAULT_PATH` is set, only that vault is allowed
- `BIND_HOST` — uvicorn bind address (default `127.0.0.1`)
- `MAX_UPLOAD_MB` — reject oversized uploads before buffering (default `50`; `0` = unlimited)
- `MAX_FETCH_MB` — cap outbound web/HTML fetch bodies (default `10`; `0` = unlimited)
- `NOTE_OUTPUT_FOLDER`, `NOTE_OUTPUT_LAYOUT`, `NOTE_OUTPUT_PATTERN` — where new atomic notes are written (see [Vault workflow](#vault-workflow-phase-d))
- `VAULT_WATCH_ENABLED`, `VAULT_WATCH_DEBOUNCE_SECONDS` — automatic re-index on vault saves
- `ANALYZE_IN_PLACE_ENABLED` — append to the analyzed note when overlap targets it
- `YOUTUBE_TRANSCRIPT_LANGUAGES` — BCP-47 codes tried in order for YouTube transcripts (default `en,en-US,en-GB`); falls back to any available language when none match

**Important:** Gemini chat models (e.g. `gemini-2.0-flash`) belong in `LLM_MODEL`, not `EMBEDDING_MODEL`. For Gemini embeddings use `EMBEDDING_PROVIDER=gemini` and `EMBEDDING_MODEL=gemini-embedding-001`.

### Production deployment

For anything beyond local development:

- Do **not** use `--reload`; run a single worker process behind localhost or a reverse proxy.
- Set a strong `API_TOKEN`. Non-loopback binds (`BIND_HOST=0.0.0.0`) refuse to start without one.
- Set `ALLOWED_VAULT_ROOTS` (or rely on `VAULT_PATH` locking) so clients cannot point the API at arbitrary host directories.
- Set a sensible `MAX_UPLOAD_MB` / `MAX_FETCH_MB`, and expand `ALLOWED_HOSTS` only when serving beyond loopback.
- Mount persistent `DATA_DIR` and a **read-write** vault volume when you want Apply / write-to-vault to work (compose defaults to RW).

```bash
# Docker (requires API_TOKEN in the environment)
export API_TOKEN="$(openssl rand -hex 32)"
docker compose up --build

# systemd (example unit in deploy/knowledge-database-actualizer.service)
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The web UI and Obsidian plugin both write through `POST /api/suggestions/apply-batch`
so server-side `.bak` backups, atomic writes, block references, and append merge
logic stay canonical.

Recommended novelty thresholds (starting points — tune on your vault):

| Embedding provider | Model (examples) | `NOVEL_THRESHOLD` | `KNOWN_THRESHOLD` |
|--------------------|------------------|-------------------|-------------------|
| `local` (MiniLM)   | `all-MiniLM-L6-v2` (default) | 0.55 | 0.75 |
| `local` (stronger) | `all-mpnet-base-v2` | 0.58 | 0.78 |
| `local` (stronger) | `bge-small-en-v1.5` | 0.60 | 0.80 |
| `gemini`           | `gemini-embedding-001` | 0.65 | 0.82 |

When switching local models, update thresholds to the matching row (or use **Calibrate thresholds** after re-indexing).

Without an LLM configured, the app still works using extractive summaries.

### Local LLM (Ollama)

```bash
# Install/run Ollama, pull a model, then:
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
# OLLAMA_BASE_URL=http://127.0.0.1:11434
```

`LLM_PROVIDER=local` is accepted as an alias of `ollama`. No API key is required.

### API auth and local-web security (optional)

For anything beyond localhost, set `API_TOKEN` in `.env`. The UI prompts for the
token on 401 and stores it in `sessionStorage` for the tab. Scripted clients
should send `Authorization: Bearer <token>` or `X-API-Token: <token>`.

The app does **not** enable CORS — the SPA is same-origin, so other websites
cannot drive the API from your browser. When serving on a LAN hostname, add it
to `ALLOWED_HOSTS` (comma-separated, default `localhost,127.0.0.1,[::1]`) so
DNS-rebinding attacks cannot reach the server via a foreign `Host` header.
Leave `ALLOWED_HOSTS` empty to disable the Host check.

### LLM spend caps

Several notes share one LLM call when `LLM_DRAFT_BATCH_SIZE` > 1 (default `3`).
Topic planning may add a few more (see below). If a batch call fails or omits a
note, that note uses an **extractive** summary — it does **not** retry with a
separate per-note LLM call (which previously doubled spend after parse
failures). Set `LLM_DRAFT_BATCH_SIZE=1` for one call per note.

Cap cost with:

- `LLM_MAX_CALLS_PER_RUN` (default `50`) — hard limit on `complete()` calls per analyze
- `LLM_MAX_INPUT_CHARS_PER_RUN` (default `400000`) — rough input-size budget (≈ tokens × 4)
- `LLM_MAX_PLANNING_CALLS` (default `6`) — max topic-planning calls per run. Large
  sources are planned in **windows** (map-reduce) so the planner sees the whole
  source, not just a prefix; this bounds how many planning calls that can take.
  Set to `1` for a single planning window (windows beyond the cap are planned
  structurally), or `0` for unlimited (still bounded by the caps above).

When a cap is hit, remaining notes fall back to extractive summaries and a warning is shown. Set either to `0` for unlimited (easy to overspend).

### Getting more notes from large PDFs

Large sources are split into planning units before notes are generated. The
number of notes is driven mainly by:

- `SEGMENT_TARGET_CHARS` (default `1200`) — approximate size of each planning
  unit. **Lower it (e.g. `800`) to get more, finer notes** from big PDFs; raise
  it for fewer, broader notes.
- `ATOMIC_NOTE_CHAR_LIMIT` / `ATOMIC_NOTE_LINE_LIMIT` — a planning unit larger
  than these is split further, so lowering them also increases note count.
- `MAX_NOTES_PER_SOURCE` — hard cap on notes produced per source.

If a very large PDF still yields too few notes, first lower `SEGMENT_TARGET_CHARS`,
then raise `MAX_NOTES_PER_SOURCE` if you hit the cap.

When an LLM is configured, topic planning runs **map-reduce** over large sources:
the source is split into planning windows, each window is planned in its own LLM
call (bounded by `LLM_MAX_PLANNING_CALLS`), and the results are reconciled with
structural planning so **every segment becomes a note even if the LLM omits it**
or a window is left unplanned. Windows beyond the call cap fall back to
structural planning, so coverage never depends on the LLM.

### Skipping boilerplate

`FILTER_BOILERPLATE=true` (default) skips non-substantive sections such as
acknowledgements, tables of contents, reference lists, indexes, chapter
summaries, and contact/copyright pages, so they do not become notes. Set it to
`false` to keep everything.

### YouTube transcript languages

Non-English videos fail with the old English-only default unless you configure
preferred languages. Set comma-separated BCP-47 codes in
`YOUTUBE_TRANSCRIPT_LANGUAGES` (for example `ru,en`). The loader tries each code
in order, then falls back to the first transcript YouTube exposes when none of
your preferences match.

### PDF extraction quality

Text-based PDFs work well; scanned PDFs and complex layouts often extract poorly.
When extraction looks unreliable (sparse pages or garbled words), the analyze
stream emits a warning so you can treat novelty verdicts with caution. There is
no OCR fallback yet.

It also drops **low-value blocks** detected structurally — link/URL dumps,
citation-marker lists (`[17] http://…[18] http://…`), and export watermarks
(e.g. `OceanofPDF.com`) — by measuring the density of URLs and citation markers
against real words, so they are caught even without a "References" heading.
Ordinary prose that merely contains a link or an inline citation is kept.

### Tables and figures

`INCLUDE_MEDIA=true` (default) detects structured content and attaches it to the
notes it belongs to (matched by page or line range) under a **Tables & figures**
section:

- **PDF tables** are extracted with `pdfplumber` and rendered as markdown tables
  (works best on tables with ruling lines; borderless layouts may only yield the
  caption).
- **Markdown pipe tables** are kept verbatim.
- **Figures** cannot be read from the image, so their caption ("Figure 3-1: ...")
  is kept as a checkable reference.

Table/caption text is also removed from the extractive summary so prose does not
fill up with pipe characters. Set `INCLUDE_MEDIA=false` to disable.

### Vault workflow (Phase D)

Tune where notes land, keep the search index fresh as you edit, and analyze
existing vault notes without always creating duplicates.

#### Note output paths

New atomic notes are written under `NOTE_OUTPUT_FOLDER` (default `sources`).

| Setting | Default | Effect |
|---------|---------|--------|
| `NOTE_OUTPUT_LAYOUT` | `nested` | `{folder}/{source_slug}/{concept_slug}.md` |
| `NOTE_OUTPUT_LAYOUT` | `flat` | `{folder}/{concept_slug}.md` |
| `NOTE_OUTPUT_PATTERN` | *(empty)* | Custom path; when set, overrides the layout default |

`NOTE_OUTPUT_PATTERN` supports placeholders:

`{folder}`, `{source_slug}`, `{concept_slug}`, `{source_title}`, `{source_type}`,
`{date}`, `{year}`, `{month}`, `{day}`

Examples:

```bash
NOTE_OUTPUT_LAYOUT=flat
# or, e.g. year/type folders:
NOTE_OUTPUT_PATTERN={year}/{source_type}/{concept_slug}.md
```

If the pattern omits `{folder}`, the configured folder is still prepended unless
you include `{folder}` explicitly. Map-of-content (`index.md`) notes use the
parent directory of the first drafted note path.

#### Index on save (vault watch)

When `VAULT_WATCH_ENABLED=true`, the server watches `VAULT_PATH` for markdown
changes and runs an **incremental re-index** after edits settle (debounced by
`VAULT_WATCH_DEBOUNCE_SECONDS`, default `5.0`). Only notes detected as stale are
re-embedded — the same logic as a manual index after editing files on disk.

- Requires `VAULT_PATH` in `.env` (or set via **Index vault** first).
- Toggle at runtime with **Index on save** in the web UI, or `POST /api/vault/watch` with `{ "enabled": true }`.
- Watch status (`active`, `last_index_at`, `last_stale_count`, errors) is in `GET /api/status` under `vault_watch`.
- Depends on [`watchdog`](https://pypi.org/project/watchdog/) (included in `requirements.txt`).

The watcher reacts to any file change under the vault; debouncing batches rapid
saves. Re-indexing runs only when at least one indexed note is stale — unrelated
file edits do not trigger embedding work.

#### Analyze in place

When `ANALYZE_IN_PLACE_ENABLED=true` (default), analyzing an **existing vault
note** can default to **append** instead of a new file: if a drafted topic
overlaps strongly with the note you are analyzing (`≥ KNOWN_THRESHOLD`), and
that overlap target is the same path as the analyzed note, the suggestion uses
`write_mode: append` and targets that path.

- **Obsidian plugin:** **Analyze current note** and **Analyze selection** send
  `vault_note_path` automatically; resume/continue uses the same context.
- **API:** optional `vault_note_path` form field on `POST /api/sources/analyze`
  (vault-relative path, e.g. `notes/my-topic.md`).
- **Web UI:** upload or paste markdown; pass `vault_note_path` when the source
  is an on-disk vault file you want to update in place.

You can still switch any suggestion back to **write** (new file) before applying.

### Quality & cost (Phase E)

Reduce LLM spend, target append locations more precisely, and optionally isolate
indexes when you work with more than one vault.

#### Batch LLM drafting

When `LLM_DRAFT_BATCH_SIZE` is greater than `1`, the note drafter sends several
topics in a **single LLM call** and expects a JSON array of note bodies back.
This cuts round-trips on long sources while keeping the same per-note structure
(frontmatter, Related notes, Source section). Failed or incomplete batches fall
back to extractive text for the missing notes only — not to N extra LLM calls.

```bash
LLM_DRAFT_BATCH_SIZE=3   # draft up to 3 topics per call (default: 3)
```

Set to `1` for the original one-topic-per-call behavior. Requires an enabled
LLM provider (`LLM_PROVIDER` not `none`).

#### Heading-targeted append

Append mode can insert new content **under a matching `##` section** instead of
always appending to the end of the file.

| Setting | Default | Effect |
|---------|---------|--------|
| `APPEND_UNDER_OVERLAP_HEADING` | `true` | When overlap is detected, use the matched chunk's heading as the append target |
| `[[Note#Section]]` in `append_target` | — | Explicit section from a path-qualified wikilink target |

The API and UI pass `append_heading` on apply when append mode targets a section.
You can also set `append_target` to `notes/topic.md#Related concepts` in
suggestions returned from analyze.

#### Richer Templater & frontmatter

Vault templates get additional Templater tag normalization at draft time
(`tp.file.folder`, creation/modified dates, etc.). Drafted notes still receive
Obsidian-compatible YAML frontmatter (`type`, `status`, `tags`, source location
fields) via safe YAML serialization.

#### Multi-vault index

When `MULTI_VAULT_INDEX_ENABLED=true`, each indexed vault path gets its own
Chroma collection suffix so embeddings from one vault do not mix with another.
`index_meta.json` stores per-vault fingerprints when this mode is on.

```bash
MULTI_VAULT_INDEX_ENABLED=true
```

Leave disabled (default) for single-vault setups — behavior matches earlier
releases.

## API

- `GET /api/status` — config, index size, graph size, LLM/embedding info, `vault_watch`, `note_output`, `analyze_in_place_enabled`, Phase E settings (`llm_draft_batch_size`, `multi_vault_index_enabled`, `append_under_overlap_heading`), and index health `warnings`
- `POST /api/vault/index` `{ "vault_path": "...", "if_stale": false }` — set `if_stale: true` to skip when the index is already fresh
- `POST /api/vault/watch` `{ "enabled": true|false }` — enable or disable index-on-save
- `GET /api/vault/graph`
- `GET /api/vault/note?note_path=...` — read an existing note (append diff preview)
- `POST /api/sources/analyze` (multipart: `url` or `file`; optional `resume`, `vault_note_path`, `vault_path`) — streams NDJSON progress, `warning`, and `result` events
- `POST /api/suggestions/preview` — exact merged note content before write (append or overwrite)
- `GET /api/suggestions/checkpoint` — the last run's saved notes (crash/rate-limit recovery)
- `POST /api/suggestions/apply` `{ "note_path", "content", "mode": "write|append", "overwrite": false, "append_heading": null }` — also re-indexes written notes; `append_heading` inserts under a matching `##` section when mode is `append`
- `POST /api/suggestions/apply-batch` `{ "notes": [{ "note_path", "content", "mode", "overwrite", "append_heading" }] }` — returns per-note `results`, `written_paths`, `skipped_existing`, `errors`, and `index_refresh`
- `POST /api/vault/refresh-notes` `{ "vault_path", "note_paths" }` — re-embed notes already written via the Obsidian plugin (or other external tools)

## Sample vault

A small sample vault lives in `sample_vault/` for smoke testing.

```bash
# Hermetic unit tests (CI)
pytest tests/

# Optional integration smoke (downloads local embedding model)
python scripts/smoke_test.py
```

Wikilinks resolve Obsidian-style: bare `[[Note]]` when the basename is unique,
path-qualified `[[folder/Note]]`, and frontmatter `aliases`. Duplicate basenames
are reported on index — use a path-qualified link for those.

## Obsidian integration (Phase 3 + C)

- **Open in Obsidian:** set `OBSIDIAN_VAULT_NAME` in `.env` for `obsidian://open` links in the web UI overlap list.
- **Vault templates:** with `USE_OBSIDIAN_TEMPLATES=true` (default), the server reads your vault's template folder from `.obsidian/app.json` and applies `{{body}}`, `{{title}}`, and common Templater tags.
- **Obsidian plugin:** see [`obsidian-plugin/README.md`](obsidian-plugin/README.md) — analyze, index, recover/continue checkpoints, edit drafts, canonical merged-note preview, analyze-in-place (`vault_note_path`), and write via `POST /api/suggestions/apply-batch` (same safety semantics as the web UI).

## Quality & intelligence (Phase 4)

- **Richer embeddings:** with `RICH_NOTE_EMBEDDINGS=true` (default), indexed chunks include note title, aliases, and tags — not just body text.
- **Tag-aware novelty:** when source and vault tags overlap, similarity gets a small boost so on-topic notes rank higher. The boost only nudges the **known** side of a *borderline* segment (raw cosine already `≥ NOVEL_THRESHOLD`); a segment below `NOVEL_THRESHOLD` stays novel regardless of shared tags, so tags can no longer mask genuinely new content.
- **Per-note related links:** each drafted note links to the vault notes most similar to *that concept* (matches below `NOVEL_THRESHOLD` are dropped), instead of every note sharing one source-wide list. A truly novel note links nothing rather than loosely-related notes.
- **Sibling links:** with `LINK_SIBLING_NOTES=true` (default), notes drafted from the same source cross-link to their most similar siblings (up to `SIBLING_LINK_COUNT`, default `3`) — a small local graph on top of the MOC index. Best-effort: any embedding hiccup leaves notes untouched.
- **Novelty-first drafting:** when batch drafting (`LLM_DRAFT_BATCH_SIZE > 1`), novel topics are drafted before known/partial ones, so a limited LLM budget or an early rate limit is spent where the tool adds the most value. The final note order still follows the source.
- **Threshold assistant:** use **Calibrate thresholds** in the web UI or `GET /api/vault/thresholds/calibrate` after indexing (available when the index has enough chunks).
- **Block references:** set `INCLUDE_BLOCK_IDS=true` to append Obsidian `^block-id` suffixes to bullets and paragraphs when notes are written.

## Project structure

```
app/
  config.py          # settings
  vault.py           # markdown + wikilink parsing
  chunking.py        # text chunking
  embeddings.py      # local embeddings
  vectorstore.py     # Chroma index/query
  graph.py           # networkx graph
  novelty.py         # verdict logic
  llm.py             # optional LLM providers
  suggest.py         # draft + apply notes
  note_output.py     # note path patterns + append helpers
  vault_watcher.py   # debounced index-on-save
  sources/           # YouTube, web article, PDF, EPUB, DOCX, text loaders
  main.py            # FastAPI app
obsidian-plugin/     # Obsidian thin client (optional)
frontend/
  index.html
  app.js
```
