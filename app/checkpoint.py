"""Incremental persistence of drafted note suggestions.

Each source gets its own checkpoint file keyed by ``normalize_source_key``,
so analyzing source B never wipes saved notes from an interrupted run on
source A. A small manifest tracks incomplete runs for ``/api/status``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.runtime import CHECKPOINT_LOCK
from app.source_identity import normalize_source_key

MANIFEST_NAME = "manifest.json"
LEGACY_LATEST = "latest.json"


def checkpoint_dir() -> Path:
    path = settings.data_dir / "checkpoints"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_checkpoint_basename(source_key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (source_key or "").strip())
    return (safe[:200] if safe else "unknown")


def checkpoint_path_for(source_key: str) -> Path:
    return checkpoint_dir() / f"{_safe_checkpoint_basename(source_key)}.json"


def latest_checkpoint_path() -> Path:
    """Legacy path — used only for one-time migration from pre-Phase-4 runs."""
    return checkpoint_dir() / LEGACY_LATEST


def manifest_path() -> Path:
    return checkpoint_dir() / MANIFEST_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_manifest_unlocked() -> dict:
    data = _read_json(manifest_path())
    if not data:
        return {"entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {"entries": []}
    return {"entries": entries}


def _save_manifest_unlocked(entries: list[dict]) -> None:
    checkpoint_dir()
    payload = {"entries": entries, "updated_at": _now()}
    fd, tmp_name = tempfile.mkstemp(dir=str(checkpoint_dir()), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, manifest_path())
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _manifest_entry_from_state(path: Path, state: dict, source_key: str) -> dict:
    source = state.get("source") or {}
    return {
        "file": path.name,
        "source_key": source_key or source.get("source_key") or "",
        "source_title": source.get("title") or "",
        "source_ref": source.get("source_ref") or "",
        "source_type": source.get("source_type") or "",
        "completed": bool(state.get("completed")),
        "suggestion_count": len(state.get("suggestions") or []),
        "updated_at": state.get("updated_at") or _now(),
    }


def _upsert_manifest_entry(path: Path, state: dict, source_key: str) -> None:
    entry = _manifest_entry_from_state(path, state, source_key)
    entries = _load_manifest_unlocked()["entries"]
    entries = [item for item in entries if item.get("file") != path.name]
    entries.append(entry)
    entries.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    _save_manifest_unlocked(entries)


def _migrate_legacy_latest_unlocked() -> None:
    legacy = latest_checkpoint_path()
    if not legacy.exists():
        return
    data = _read_json(legacy)
    if not data:
        legacy.unlink(missing_ok=True)
        return
    source = data.get("source") or {}
    source_key = source.get("source_key") or normalize_source_key(
        source.get("source_type"),
        source.get("source_ref"),
    )
    if source_key:
        target = checkpoint_path_for(source_key)
        if not target.exists():
            fd, tmp_name = tempfile.mkstemp(dir=str(checkpoint_dir()), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, target)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        _upsert_manifest_entry(target, data, source_key)
    legacy.unlink(missing_ok=True)


def _ensure_migrated() -> None:
    with CHECKPOINT_LOCK:
        _migrate_legacy_latest_unlocked()


class SuggestionCheckpoint:
    """Writes the growing set of note suggestions to a JSON file atomically."""

    def __init__(self, path: Path | None = None, *, source_key: str | None = None):
        self._explicit_path = path is not None
        if path is None:
            if not source_key:
                raise ValueError("source_key is required when path is omitted")
            path = checkpoint_path_for(source_key)
        self.source_key = source_key or ""
        self.path = path
        self._state: dict = {
            "created_at": _now(),
            "updated_at": _now(),
            "completed": False,
            "source": None,
            "suggestions": [],
            "warnings": [],
        }

    @classmethod
    def for_source(
        cls,
        source_type: str,
        source_ref: str,
        *,
        source_key: str | None = None,
    ) -> SuggestionCheckpoint:
        key = source_key or normalize_source_key(source_type, source_ref)
        return cls(source_key=key)

    def start(self, source: dict | None) -> None:
        if source and not self._explicit_path:
            self.source_key = source.get("source_key") or normalize_source_key(
                source.get("source_type"),
                source.get("source_ref"),
            )
            if self.source_key:
                self.path = checkpoint_path_for(self.source_key)
        self._state["source"] = source
        self._state["completed"] = False
        self._state["suggestions"] = []
        self._state["warnings"] = []
        self._flush()

    def resume(
        self,
        source: dict | None,
        suggestions: list[dict],
        warnings: list[str] | None = None,
    ) -> None:
        """Seed the checkpoint with already-recovered notes before continuing."""
        if source and not self._explicit_path:
            self.source_key = source.get("source_key") or normalize_source_key(
                source.get("source_type"),
                source.get("source_ref"),
            )
            if self.source_key:
                self.path = checkpoint_path_for(self.source_key)
        self._state["source"] = source
        self._state["completed"] = False
        self._state["suggestions"] = list(suggestions)
        self._state["warnings"] = list(warnings or [])
        self._flush()

    def add(self, suggestion: dict) -> None:
        self._state["suggestions"].append(suggestion)
        self._flush()

    def add_warning(self, message: str) -> None:
        self._state["warnings"].append(message)
        self._flush()

    def finish(self, *, completed: bool = True) -> None:
        self._state["completed"] = completed
        self._flush()

    @property
    def suggestions(self) -> list[dict]:
        return list(self._state["suggestions"])

    @property
    def warnings(self) -> list[str]:
        return list(self._state["warnings"])

    def _flush(self) -> None:
        self._state["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with CHECKPOINT_LOCK:
            _migrate_legacy_latest_unlocked()
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._state, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self.path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
            source = self._state.get("source") or {}
            key = self.source_key or source.get("source_key") or normalize_source_key(
                source.get("source_type"),
                source.get("source_ref"),
            )
            if key:
                _upsert_manifest_entry(self.path, self._state, key)


def _load_checkpoint_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    with CHECKPOINT_LOCK:
        _migrate_legacy_latest_unlocked()
        return _read_json(path)


def load_checkpoint_for_source(
    source_type: str | None,
    source_ref: str | None,
    *,
    source_key: str | None = None,
) -> dict | None:
    """Load the checkpoint for a specific source, if any."""
    _ensure_migrated()
    key = source_key or normalize_source_key(source_type, source_ref)
    if not key:
        return None
    return _load_checkpoint_file(checkpoint_path_for(key))


def load_checkpoint_by_key(source_key: str) -> dict | None:
    _ensure_migrated()
    key = (source_key or "").strip()
    if not key:
        return None
    return _load_checkpoint_file(checkpoint_path_for(key))


def load_latest_checkpoint() -> dict | None:
    """Most recently updated checkpoint (any source) — for Recover last saved notes."""
    _ensure_migrated()
    entries = _load_manifest_unlocked()["entries"]
    if not entries:
        return None
    for entry in sorted(entries, key=lambda item: item.get("updated_at", ""), reverse=True):
        data = _load_checkpoint_file(checkpoint_dir() / entry["file"])
        if data:
            return data
    return None


def list_incomplete_checkpoints() -> list[dict]:
    """Summaries of runs that still have saved notes and are not marked completed."""
    _ensure_migrated()
    incomplete: list[dict] = []
    for entry in _load_manifest_unlocked()["entries"]:
        if entry.get("completed"):
            continue
        data = _load_checkpoint_file(checkpoint_dir() / entry["file"])
        if not data or not data.get("suggestions"):
            continue
        source = data.get("source") or {}
        incomplete.append(
            {
                "source_key": entry.get("source_key") or source.get("source_key"),
                "source_title": entry.get("source_title") or source.get("title"),
                "source_ref": entry.get("source_ref") or source.get("source_ref"),
                "source_type": entry.get("source_type") or source.get("source_type"),
                "suggestion_count": len(data.get("suggestions") or []),
                "updated_at": data.get("updated_at"),
                "warnings": data.get("warnings") or [],
            }
        )
    incomplete.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return incomplete


def checkpoint_matches_source(
    checkpoint: dict | None,
    source_ref: str,
    *,
    source_type: str | None = None,
    source_key: str | None = None,
) -> bool:
    """True when a saved checkpoint was produced from the same source."""
    if not checkpoint:
        return False
    source = checkpoint.get("source") or {}
    saved_key = source.get("source_key") or normalize_source_key(
        source.get("source_type"),
        source.get("source_ref"),
    )
    current_key = source_key or normalize_source_key(source_type, source_ref)
    return bool(current_key) and saved_key == current_key
