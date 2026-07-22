"""API token capability checks and route→capability mapping."""

from __future__ import annotations

import re
from collections.abc import Iterable

ALL_CAPABILITIES = frozenset({"read", "analyze", "write", "admin", "chat"})


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
    """Return capabilities required for an HTTP request."""
    method = method.upper()
    if path == "/api/chat" and method == "POST":
        return frozenset({"chat"})
    if path == "/api/sources/analyze" and method == "POST":
        return frozenset({"analyze"})
    if path in {"/api/suggestions/apply", "/api/suggestions/apply-batch", "/api/suggestions/preview"}:
        return frozenset({"write"})
    if path == "/api/suggestions/checkpoint/import" and method == "POST":
        return frozenset({"admin"})
    if path == "/api/vault/thresholds" and method == "POST":
        return frozenset({"admin"})
    if path == "/api/debug/recent-logs" and method == "GET":
        return frozenset({"admin"})
    if path.startswith("/api/"):
        return frozenset({"read"})
    return frozenset()


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
