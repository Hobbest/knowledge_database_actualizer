"""Build Obsidian ``obsidian://`` URIs for opening vault notes."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from app.config import settings


def note_path_for_obsidian_uri(note_path: str) -> str:
    """Vault-relative path without ``.md`` (Obsidian URI convention)."""
    target = (note_path or "").replace("\\", "/").strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    return target


def obsidian_open_uri(
    note_path: str,
    *,
    vault_name: str | None = None,
    new_leaf: bool = False,
) -> str:
    """Return an ``obsidian://open`` URI for the given vault-relative note path."""
    file_target = note_path_for_obsidian_uri(note_path)
    if not file_target:
        return ""

    params: dict[str, str] = {"file": file_target}
    name = (vault_name or settings.obsidian_vault_name or "").strip()
    if name:
        params["vault"] = name
    if new_leaf:
        params["newLeaf"] = "true"

    # Obsidian expects encoded paths; urlencode handles spaces/special chars.
    return f"obsidian://open?{urlencode(params, quote_via=quote)}"


def obsidian_uri_available() -> bool:
    """Whether the UI should offer Open-in-Obsidian links."""
    return bool(settings.obsidian_vault_name) or bool(settings.vault_path)
