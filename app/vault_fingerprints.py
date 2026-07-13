"""Content fingerprints for incremental vault indexing."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import settings
from app.vault import VaultNote, load_vault


def note_content_hash(note: VaultNote) -> str:
    """Hash of note fields + chunk/embedding settings that affect indexed vectors."""
    payload = (
        f"{settings.chunk_size}:{settings.chunk_overlap}:"
        f"{settings.embedding_provider}:{settings.embedding_model}:"
        f"rich={settings.rich_note_embeddings}:"
        f"transclude={settings.transclude_depth}:{settings.transclude_excerpt_chars}:"
        f"{note.title}:{','.join(note.aliases)}:{','.join(note.tags)}:{note.content}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def note_fingerprint(vault_path: Path, note: VaultNote) -> dict:
    md_path = (vault_path / note.path).resolve()
    mtime_ns = md_path.stat().st_mtime_ns if md_path.is_file() else 0
    return {
        "hash": note_content_hash(note),
        "mtime_ns": mtime_ns,
    }


def embedding_index_config(meta: dict | None) -> tuple | None:
    if not meta:
        return None
    return (
        meta.get("embedding_provider"),
        meta.get("embedding_model"),
        meta.get("chunk_size"),
        meta.get("chunk_overlap"),
        meta.get("collection_suffix"),
    )


def current_embedding_index_config() -> tuple:
    from app.embeddings import embedding_collection_suffix

    return (
        settings.embedding_provider,
        settings.embedding_model,
        settings.chunk_size,
        settings.chunk_overlap,
        embedding_collection_suffix(),
    )


def index_config_changed(meta: dict | None) -> bool:
    if meta is None:
        return False
    previous = embedding_index_config(meta)
    if previous is None:
        return False
    return previous != current_embedding_index_config()


def count_stale_vault_notes(vault_path: Path, fingerprints: dict[str, dict]) -> int:
    """How many notes differ from the last index (new, changed, or removed)."""
    if not vault_path.is_dir() or not fingerprints:
        return 0

    vault_result = load_vault(vault_path)
    current_paths = set()
    stale = 0

    for note in vault_result.notes:
        rel = note.path.as_posix()
        current_paths.add(rel)
        stored = fingerprints.get(rel)
        if stored is None:
            stale += 1
            continue
        md_path = vault_path / note.path
        if not md_path.is_file():
            stale += 1
            continue
        mtime_ns = md_path.stat().st_mtime_ns
        if stored.get("mtime_ns") != mtime_ns:
            stale += 1
            continue
        if stored.get("hash") != note_content_hash(note):
            stale += 1

    stale += len(set(fingerprints.keys()) - current_paths)
    return stale
