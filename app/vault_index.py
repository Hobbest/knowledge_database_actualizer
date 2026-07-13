"""Vault-scoped index identity helpers (Phase E multi-vault support)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import settings


def vault_index_key(vault_path: Path) -> str:
    return str(vault_path.resolve())


def vault_collection_token(vault_path: Path | None) -> str:
    """Short suffix for Chroma collection names when multi-vault indexing is enabled."""
    if not settings.multi_vault_index_enabled or vault_path is None:
        return ""
    digest = hashlib.sha256(vault_index_key(vault_path).encode("utf-8")).hexdigest()[:12]
    return f"_{digest}"


def resolve_vault_meta(meta: dict | None, vault_path: Path | None) -> dict | None:
    """Return index metadata for a vault, or None when unknown."""
    if meta is None or vault_path is None:
        return meta
    key = vault_index_key(vault_path)
    vaults = meta.get("vaults")
    if isinstance(vaults, dict) and key in vaults:
        return vaults[key]
    indexed = meta.get("vault_path")
    if indexed and Path(indexed).resolve() == vault_path.resolve():
        return meta
    return None
