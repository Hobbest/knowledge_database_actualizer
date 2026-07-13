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
