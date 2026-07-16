# Obsidian plugin

Thin client for the Knowledge Database Actualizer API. Keeps the Python server as the source of truth for indexing, novelty scoring, and note drafting.

## Install (manual)

1. Start the Actualizer server (`uvicorn app.main:app --reload`).
2. Copy this folder into your vault's plugins directory:
   - **From repo:** copy `obsidian-plugin/` to `<vault>/.obsidian/plugins/knowledge-database-actualizer/`
   - **Required files:** `manifest.json`, `main.js`, `styles.css`, `versions.json`
   - `core.js` is used only for Node contract tests; Obsidian loads `main.js` alone.
3. Enable **Knowledge Database Actualizer** under Community plugins.
4. Open **Settings → Knowledge Database Actualizer** and set:
   - **API base URL** — default `http://127.0.0.1:8000`
   - **API token** — only if `API_TOKEN` is set in the server's `.env`
   - **Open notes after write** — open newly written notes in the editor (default on)

## Commands

| Command | Action |
|---------|--------|
| Analyze current note | Sends the active file to `/api/sources/analyze` |
| Analyze URL from clipboard | Analyzes a YouTube/web URL |
| Analyze current selection | Analyzes selected editor text |
| Index vault | Runs `/api/vault/index` for this vault |
| Recover saved notes | Loads the latest checkpoint without re-analyzing |
| Continue interrupted run | Resumes drafting with `resume=true` (same source as last analyze) |
| Open Actualizer sidebar | Shows verdict, overlaps, and proposed notes |

## Sidebar workflow

The sidebar mirrors the web UI essentials:

- **Recover / Continue** when a checkpoint exists (rate limits, crashes, long PDF runs)
- **Overlapping vault notes** with similarity, heading, and excerpt
- **Editable vault path and note content** before write
- **Append vs new file** when a topic matches an existing note
- **Exact merged-note preview** via `POST /api/suggestions/preview`
- **Write selected** via `/api/suggestions/apply-batch`, including server backups, atomic writes,
  block references, index refresh, and optional opening of written notes
- **Vault watch and threshold calibration** controls beside the index action

## Community plugin submission

To publish in Obsidian Community Plugins:

1. Create a release tag (e.g. `obsidian-plugin-0.1.0`) containing this folder's files.
2. Submit a PR to [obsidianmd/obsidian-releases](https://github.com/obsidianmd/obsidian-releases) with:
   - `manifest.json` (this folder)
   - `versions.json` mapping version → minimum Obsidian app version
   - Release asset URL for `main.js` (and `styles.css` if not bundled)
3. Include `icon.svg` (128×128) in the plugin repo for the directory listing.

## Tips

- **Start the server first:** `uvicorn app.main:app --reload` (default http://127.0.0.1:8000).
- The plugin uses Obsidian's `requestUrl` API (not browser `fetch`) so local requests are not blocked by CORS from `app://obsidian.md`.
- Use **Test API connection** in plugin settings if Index/Analyze fails.
- Set `OBSIDIAN_VAULT_NAME` in the server `.env` so the web UI can build `obsidian://open` links.
- Place templates in your vault's template folder; the server auto-discovers them when `USE_OBSIDIAN_TEMPLATES=true` (default).
- For auth, use the same bearer token in both the plugin settings and the web UI.
- **Continue interrupted run** requires analyzing the same source again (same note, URL, or selection) — the plugin remembers the last analyze context automatically.
