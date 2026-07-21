from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date
from pathlib import Path

from app.config import settings

_lock = threading.Lock()


def _path() -> Path:
    return settings.data_dir / "analytics.json"


def _empty() -> dict:
    return {"days": {}, "totals": {"analyzed_sources": 0, "written_notes": 0}}


def load_analytics() -> dict:
    path = _path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("days"), dict):
            payload.setdefault("totals", {"analyzed_sources": 0, "written_notes": 0})
            return payload
    except (OSError, json.JSONDecodeError):
        pass
    return _empty()


def record_counts(*, analyzed_sources: int = 0, written_notes: int = 0) -> dict:
    """Atomically increment daily and all-time local counters."""
    with _lock:
        payload = load_analytics()
        key = date.today().isoformat()
        day = payload["days"].setdefault(key, {"analyzed_sources": 0, "written_notes": 0})
        for bucket in (day, payload["totals"]):
            bucket["analyzed_sources"] = int(bucket.get("analyzed_sources", 0)) + analyzed_sources
            bucket["written_notes"] = int(bucket.get("written_notes", 0)) + written_notes
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".analytics-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return payload
