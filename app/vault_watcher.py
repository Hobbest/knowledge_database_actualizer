"""Debounced incremental vault re-index when markdown files change."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import settings
from app.index_meta import stale_note_count

logger = logging.getLogger(__name__)

_SKIP_DIR_NAMES = {".obsidian", ".trash", ".git", "templates", "template"}


class _VaultWatchHandler(FileSystemEventHandler):
    def __init__(self, schedule: Callable[[], None]):
        super().__init__()
        self._schedule = schedule

    def _maybe_schedule(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        paths = [getattr(event, "src_path", "") or ""]
        dest_path = getattr(event, "dest_path", "") or ""
        if dest_path:
            paths.append(dest_path)
        if not any(path.endswith(".md") for path in paths):
            return
        if any(
            part in _SKIP_DIR_NAMES
            for path in paths
            for part in Path(path).parts
        ):
            return
        self._schedule()

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_schedule(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_schedule(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._maybe_schedule(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._maybe_schedule(event)


class VaultWatchService:
    def __init__(self):
        self._observer: Observer | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._index_runner: Callable[[Path], dict[str, Any]] | None = None
        self.enabled = False
        self.active = False
        self.last_index_at: str | None = None
        self.last_stale_count: int = 0
        self.last_error: str | None = None
        self.last_stats: dict[str, Any] | None = None

    def configure(self, index_runner: Callable[[Path], dict[str, Any]]) -> None:
        self._index_runner = index_runner

    def _detach(self) -> tuple[Observer | None, threading.Timer | None]:
        with self._lock:
            timer = self._timer
            observer = self._observer
            self._timer = None
            self._observer = None
            self.active = False
            return observer, timer

    def _shutdown(self, observer: Observer | None, timer: threading.Timer | None) -> None:
        if timer is not None:
            timer.cancel()
        if observer is not None:
            observer.stop()
            # Join outside _lock — the observer thread may call _schedule_reindex().
            observer.join(timeout=5)

    def start(self) -> None:
        observer, timer = self._detach()
        self._shutdown(observer, timer)

        with self._lock:
            if not settings.vault_watch_enabled:
                self.enabled = False
                return
            vault_path = settings.vault_path
            if vault_path is None or not vault_path.is_dir():
                self.enabled = settings.vault_watch_enabled
                self.last_error = "VAULT_PATH is not configured or missing"
                return

            handler = _VaultWatchHandler(lambda: self._schedule_reindex())
            observer = Observer()
            observer.schedule(handler, str(vault_path.resolve()), recursive=True)
            observer.start()
            self._observer = observer
            self.enabled = True
            self.active = True
            self.last_error = None
            logger.info("Vault watch started for %s", vault_path)

    def stop(self) -> None:
        observer, timer = self._detach()
        self._shutdown(observer, timer)
        self.enabled = False

    def set_enabled(self, enabled: bool) -> None:
        settings.vault_watch_enabled = enabled
        if enabled:
            if self.active:
                return
            self.start()
        else:
            self.stop()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": settings.vault_watch_enabled,
            "active": self.active,
            "debounce_seconds": settings.vault_watch_debounce_seconds,
            "last_index_at": self.last_index_at,
            "last_stale_count": self.last_stale_count,
            "last_error": self.last_error,
            "last_stats": self.last_stats,
        }

    def _schedule_reindex(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            delay = max(0.5, settings.vault_watch_debounce_seconds)
            self._timer = threading.Timer(delay, self._run_reindex)
            self._timer.daemon = True
            self._timer.start()

    def _run_reindex(self) -> None:
        vault_path = settings.vault_path
        if vault_path is None or not vault_path.is_dir() or self._index_runner is None:
            return

        try:
            stale = stale_note_count(vault_path)
            self.last_stale_count = stale
            if stale <= 0:
                self.last_index_at = datetime.now(timezone.utc).isoformat()
                self.last_error = None
                return

            stats = self._index_runner(vault_path.resolve())
            self.last_stats = stats
            self.last_index_at = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            logger.info("Vault watch re-indexed %s stale note(s)", stale)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.exception("Vault watch re-index failed")


vault_watch = VaultWatchService()
