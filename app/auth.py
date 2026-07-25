"""API token capability checks and route→capability mapping."""

from __future__ import annotations

import re
from collections.abc import Iterable

ALL_CAPABILITIES = frozenset({"read", "analyze", "write", "admin", "chat"})

# Exhaustive method+path map for /api/*. Unmapped API routes fail closed to admin.
_ROUTE_CAPABILITIES: dict[tuple[str, str], frozenset[str]] = {
    # read — status, search, graph, checkpoints, calibration preview
    ("GET", "/api/status"): frozenset({"read"}),
    ("GET", "/api/analytics"): frozenset({"read"}),
    ("GET", "/api/vault/note"): frozenset({"read"}),
    ("GET", "/api/vault/search"): frozenset({"read"}),
    ("GET", "/api/vault/graph"): frozenset({"read"}),
    ("GET", "/api/vault/thresholds/calibrate"): frozenset({"read"}),
    ("GET", "/api/suggestions/checkpoint"): frozenset({"read"}),
    ("POST", "/api/reports/export"): frozenset({"read"}),
    # analyze
    ("POST", "/api/sources/analyze"): frozenset({"analyze"}),
    # write — vault mutators + note apply/preview
    ("POST", "/api/vault/index"): frozenset({"write"}),
    ("POST", "/api/vault/watch"): frozenset({"write"}),
    ("POST", "/api/vault/refresh-notes"): frozenset({"write"}),
    ("POST", "/api/suggestions/apply"): frozenset({"write"}),
    ("POST", "/api/suggestions/apply-batch"): frozenset({"write"}),
    ("POST", "/api/suggestions/preview"): frozenset({"write"}),
    # chat
    ("POST", "/api/chat"): frozenset({"chat"}),
    # admin — privileged config, logs, server-side exports/imports
    ("GET", "/api/debug/recent-logs"): frozenset({"admin"}),
    ("POST", "/api/vault/thresholds"): frozenset({"admin"}),
    ("GET", "/api/vault/index/export"): frozenset({"admin"}),
    ("GET", "/api/suggestions/checkpoint/export"): frozenset({"admin"}),
    ("POST", "/api/suggestions/checkpoint/import"): frozenset({"admin"}),
}


def parse_api_token_capabilities(raw: str) -> frozenset[str]:
    """Parse comma-separated capability names. Empty string grants all."""
    if not raw or not raw.strip():
        return ALL_CAPABILITIES
    parsed = frozenset(
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    )
    unknown = parsed - ALL_CAPABILITIES
    if unknown:
        raise ValueError(
            f"Unknown API_TOKEN_CAPABILITIES: {', '.join(sorted(unknown))}. "
            f"Use: {', '.join(sorted(ALL_CAPABILITIES))}"
        )
    return parsed


def required_capabilities(method: str, path: str) -> frozenset[str]:
    """Return capabilities required for an HTTP request.

    Mapped routes use the explicit table. Unmapped ``/api/*`` paths require
    ``admin`` (fail closed) so new endpoints are not silently readable.
    """
    method = method.upper()
    if not path.startswith("/api/"):
        return frozenset()
    return _ROUTE_CAPABILITIES.get((method, path), frozenset({"admin"}))


def token_grants_capabilities(
    granted: Iterable[str] | frozenset[str],
    required: Iterable[str] | frozenset[str],
) -> bool:
    granted_set = frozenset(granted)
    return frozenset(required).issubset(granted_set)


def validate_suggestion_note_path(note_path: str) -> str:
    """Ensure checkpoint/import note paths are safe relative markdown paths."""
    raw = str(note_path or "").replace("\\", "/").strip()
    if not raw:
        raise ValueError("note_path must not be empty")
    if raw.startswith("/"):
        raise ValueError(f"note_path must be vault-relative: {note_path!r}")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw.lower().endswith(".md"):
        raise ValueError(f"note_path must be a .md file: {note_path!r}")
    if ".." in raw.split("/"):
        raise ValueError(f"note_path must not contain '..': {note_path!r}")
    if re.search(r"[\x00-\x1f]", raw):
        raise ValueError("note_path contains control characters")
    return raw


def validate_suggestion_content(content: str, *, max_chars: int = 500_000) -> str:
    text = str(content or "")
    if len(text) > max_chars:
        raise ValueError(f"note content exceeds {max_chars} characters")
    return text
