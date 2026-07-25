"""Process-wide locks and thread pool for heavy vault/index work."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

# Serializes VectorStore + KnowledgeGraph mutations/queries so index and analyze
# cannot race (e.g. reset mid-query).
INDEX_LOCK = threading.RLock()

# Serializes checkpoint file read/write across overlapping analyze runs.
CHECKPOINT_LOCK = threading.Lock()

# Bound worker pool for short sync work (full re-index, apply + refresh).
WORKER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="actualizer")

# Separate pool for analyze streams. Analyze runs can last hours (per-note LLM
# calls with backoff sleeps); giving them their own pool means they can never
# starve index/apply requests, and vice versa.
ANALYZE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyze")

# Admission control for analyze streams (executor queue alone is unbounded).
_ANALYZE_IN_FLIGHT = 0
_ANALYZE_IN_FLIGHT_LOCK = threading.Lock()


def try_acquire_analyze_slot(limit: int) -> bool:
    """Reserve one in-flight analyze slot. ``limit <= 0`` means unlimited."""
    global _ANALYZE_IN_FLIGHT
    if limit <= 0:
        return True
    with _ANALYZE_IN_FLIGHT_LOCK:
        if _ANALYZE_IN_FLIGHT >= limit:
            return False
        _ANALYZE_IN_FLIGHT += 1
        return True


def release_analyze_slot() -> None:
    global _ANALYZE_IN_FLIGHT
    with _ANALYZE_IN_FLIGHT_LOCK:
        _ANALYZE_IN_FLIGHT = max(0, _ANALYZE_IN_FLIGHT - 1)


def analyze_in_flight_count() -> int:
    with _ANALYZE_IN_FLIGHT_LOCK:
        return _ANALYZE_IN_FLIGHT
