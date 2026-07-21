"""Central logging configuration and lightweight in-process metrics."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_SECRET_RE = re.compile(
    r"(?i)(?:\b(api[_-]?key|authorization|token|password)\b\s*[:=]\s*(?:bearer\s+)?|"
    r"\bbearer\s+)[^\s&,]+"
)
_RECENT_LOGS: deque[dict[str, str]] = deque(maxlen=200)
_RECENT_LOGS_LOCK = threading.Lock()


class RecentLogHandler(logging.Handler):
    """Bounded, redacted log buffer for the authenticated debug endpoint."""

    def emit(self, record: logging.LogRecord) -> None:
        message = _SECRET_RE.sub("[redacted]", record.getMessage())
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        with _RECENT_LOGS_LOCK:
            _RECENT_LOGS.append(item)


def recent_logs(limit: int = 100) -> list[dict[str, str]]:
    with _RECENT_LOGS_LOCK:
        return list(_RECENT_LOGS)[-max(1, min(limit, 200)) :]


def configure_logging(level: str, output_format: str) -> None:
    """Configure application logs once, without duplicating handlers on reload."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    if output_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(handler)
    root.addHandler(RecentLogHandler())


class AppMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] = {
            "requests": 0,
            "request_errors": 0,
            "request_duration_ms_total": 0,
            "analyze_runs": 0,
            "analyze_errors": 0,
            "analyze_duration_ms_total": 0,
            "notes_drafted": 0,
            "llm_calls": 0,
            "cache_hits": 0,
        }

    def record_request(self, duration_ms: float, *, error: bool) -> None:
        with self._lock:
            self._values["requests"] += 1
            self._values["request_duration_ms_total"] += max(0, duration_ms)
            if error:
                self._values["request_errors"] += 1

    def record_analyze(
        self,
        duration_ms: float,
        *,
        error: bool,
        notes_drafted: int,
        llm_calls: int,
        cache_hits: int,
    ) -> None:
        with self._lock:
            self._values["analyze_runs"] += 1
            self._values["analyze_duration_ms_total"] += max(0, duration_ms)
            self._values["notes_drafted"] += max(0, notes_drafted)
            self._values["llm_calls"] += max(0, llm_calls)
            self._values["cache_hits"] += max(0, cache_hits)
            if error:
                self._values["analyze_errors"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            values = dict(self._values)
        requests = int(values["requests"])
        runs = int(values["analyze_runs"])
        return {
            "requests": requests,
            "request_errors": int(values["request_errors"]),
            "average_request_ms": round(
                values["request_duration_ms_total"] / requests, 1
            )
            if requests
            else 0.0,
            "analyze_runs": runs,
            "analyze_errors": int(values["analyze_errors"]),
            "average_analyze_ms": round(
                values["analyze_duration_ms_total"] / runs, 1
            )
            if runs
            else 0.0,
            "notes_drafted": int(values["notes_drafted"]),
            "llm_calls": int(values["llm_calls"]),
            "cache_hits": int(values["cache_hits"]),
        }


metrics = AppMetrics()
