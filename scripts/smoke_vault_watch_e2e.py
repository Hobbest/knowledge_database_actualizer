#!/usr/bin/env python3
"""End-to-end smoke: vault watch detects a save and runs incremental re-index.

Simulates an Obsidian save by editing a markdown file under VAULT_PATH while
the API server has vault watch enabled. Restores the file afterward.

Usage (from repo root, with .venv active):
  python scripts/smoke_vault_watch_e2e.py

Optional env:
  SMOKE_API_URL=http://127.0.0.1:8000
  SMOKE_VAULT_PATH=/path/to/vault
  SMOKE_NOTE_REL=Base/00 English.md
  SMOKE_START_SERVER=1   (default: start uvicorn if API is down)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.index_meta import load_index_meta, stale_note_count  # noqa: E402

API = os.environ.get("SMOKE_API_URL", "http://127.0.0.1:8000").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("SMOKE_HTTP_TIMEOUT", "30"))
DEFAULT_VAULT = Path(os.environ.get("SMOKE_VAULT_PATH", "/home/karufox/Obsidian/zettel"))
NOTE_REL = os.environ.get("SMOKE_NOTE_REL", "Base/00 English.md")
DEBOUNCE = float(os.environ.get("SMOKE_DEBOUNCE", "3"))
POLL_TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "180"))
MARKER = "<!-- actualizer-vault-watch-smoke -->"


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(method: str, path: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_up() -> bool:
    try:
        http_json("GET", "/api/status")
        return True
    except Exception:
        return False


def wait_for_api(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if api_up():
            return
        time.sleep(0.5)
    raise RuntimeError(f"API not reachable at {API}")


def resolve_vault() -> Path:
    meta = load_index_meta() or {}
    indexed = meta.get("vault_path")
    if indexed:
        indexed_path = Path(indexed).resolve()
        if indexed_path.is_dir():
            if DEFAULT_VAULT.resolve() != indexed_path:
                log(
                    f"Note: SMOKE_VAULT_PATH={DEFAULT_VAULT} differs from indexed vault "
                    f"{indexed_path}; using indexed vault for accurate staleness."
                )
            return indexed_path
    vault = DEFAULT_VAULT.resolve()
    if not vault.is_dir():
        raise RuntimeError(f"Vault path does not exist: {vault}")
    return vault


def main() -> int:
    vault = resolve_vault()
    note_path = vault / NOTE_REL
    if not note_path.is_file():
        raise RuntimeError(f"Smoke note missing: {note_path}")

    server_proc: subprocess.Popen | None = None
    started_server = False

    try:
        if not api_up():
            if os.environ.get("SMOKE_START_SERVER", "1") != "1":
                raise RuntimeError(f"API down at {API} and SMOKE_START_SERVER=0")
            log(f"Starting uvicorn on {API} with vault watch enabled...")
            env = os.environ.copy()
            env["VAULT_PATH"] = str(vault)
            env["VAULT_WATCH_ENABLED"] = "true"
            env["VAULT_WATCH_DEBOUNCE_SECONDS"] = str(DEBOUNCE)
            server_proc = subprocess.Popen(
                [str(ROOT / ".venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            started_server = True
            wait_for_api(timeout=120)
        else:
            log(f"Using existing API at {API}")

        status = http_json("GET", "/api/status")
        log(f"Vault: {status.get('vault_path')}")
        log(f"Indexed chunks: {status.get('indexed_chunks')} | stale: {status.get('stale_note_count')}")

        watch_state = status.get("vault_watch") or {}
        if watch_state.get("enabled") and watch_state.get("active"):
            log("Vault watch already enabled and active")
            watch = watch_state
        else:
            watch = http_json("POST", "/api/vault/watch", {"enabled": True})
            log(f"Vault watch: enabled={watch.get('enabled')} active={watch.get('active')}")
        if watch.get("last_error"):
            raise RuntimeError(f"Vault watch error: {watch['last_error']}")
        if not watch.get("active"):
            raise RuntimeError("Vault watch is not active — check VAULT_PATH on the running server")

        if stale_note_count(vault) > 0:
            log("Pre-test stale notes detected — running a quick index first...")
            http_json("POST", "/api/vault/index", {"vault_path": str(vault), "if_stale": False})

        before_watch = http_json("GET", "/api/status").get("vault_watch") or {}
        before_index_at = before_watch.get("last_index_at")
        before_stale = http_json("GET", "/api/status").get("stale_note_count", 0)

        original = note_path.read_text(encoding="utf-8")
        if MARKER in original:
            patched = original.replace(f"\n{MARKER}\n", "\n").replace(MARKER, "")
        else:
            patched = original
        patched = patched.rstrip() + f"\n{MARKER}\n"
        log(f"Simulating Obsidian save: {NOTE_REL}")
        note_path.write_text(patched, encoding="utf-8")

        after_edit_stale = stale_note_count(vault)
        log(f"Stale notes after edit: {after_edit_stale}")
        if after_edit_stale <= before_stale:
            raise RuntimeError("Expected stale note count to increase after edit")

        deadline = time.time() + POLL_TIMEOUT
        success = False
        while time.time() < deadline:
            time.sleep(1.0)
            status = http_json("GET", "/api/status")
            watch_state = status.get("vault_watch") or {}
            stale = status.get("stale_note_count", 0)
            last_at = watch_state.get("last_index_at")
            last_stale = watch_state.get("last_stale_count")
            err = watch_state.get("last_error")
            log(
                f"  poll: stale={stale} watch.last_stale={last_stale} "
                f"last_index_at={last_at} err={err or '-'}"
            )
            if err:
                raise RuntimeError(f"Vault watch re-index failed: {err}")
            if stale == 0 and last_at and last_at != before_index_at:
                stats = watch_state.get("last_stats") or {}
                log(
                    "Re-index completed: "
                    f"mode={stats.get('index_mode')} "
                    f"indexed_notes={stats.get('indexed_notes')} "
                    f"skipped_notes={stats.get('skipped_notes')}"
                )
                success = True
                break
            if stale == 0 and last_at and before_index_at is None:
                success = True
                break

        if not success:
            raise RuntimeError("Timed out waiting for vault watch to clear stale notes")

        log("PASS: vault watch detected save and re-indexed incrementally.")
        return 0
    finally:
        if note_path.is_file():
            try:
                current = note_path.read_text(encoding="utf-8")
                restored = current.replace(f"\n{MARKER}\n", "\n").replace(MARKER, "")
                if restored != current:
                    note_path.write_text(restored if restored.endswith("\n") else restored + "\n", encoding="utf-8")
                    log(f"Restored {NOTE_REL}")
            except Exception as exc:  # noqa: BLE001
                log(f"WARNING: could not restore note: {exc}")

        if started_server and server_proc is not None:
            log("Stopping smoke-test uvicorn...")
            server_proc.send_signal(signal.SIGINT)
            try:
                server_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
