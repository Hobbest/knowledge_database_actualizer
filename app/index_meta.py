"""Persisted metadata about vault indexes (for health checks)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.embeddings import embedding_collection_suffix
from app.vault_fingerprints import count_stale_vault_notes
from app.vault_index import resolve_vault_meta, vault_index_key


def index_meta_path() -> Path:
    return settings.data_dir / "index_meta.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embedding_fields() -> dict:
    return {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "collection_suffix": embedding_collection_suffix(),
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }


def save_index_meta(
    *,
    vault_path: Path,
    chunk_count: int,
    note_count: int | None = None,
    note_fingerprints: dict[str, dict] | None = None,
    index_mode: str | None = None,
) -> dict:
    """Record which vault/embedding space the on-disk Chroma index belongs to."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    previous = load_index_meta() or {}
    vault_path = vault_path.resolve()
    key = vault_index_key(vault_path)

    entry = {
        "vault_path": str(vault_path),
        **_embedding_fields(),
        "chunk_count": chunk_count,
        "note_count": note_count,
        "indexed_at": _now(),
    }
    if note_fingerprints is not None:
        entry["note_fingerprints"] = note_fingerprints
    elif previous.get("note_fingerprints") and not settings.multi_vault_index_enabled:
        entry["note_fingerprints"] = previous["note_fingerprints"]
    else:
        prior = resolve_vault_meta(previous, vault_path) or {}
        if prior.get("note_fingerprints"):
            entry["note_fingerprints"] = prior["note_fingerprints"]
    if index_mode:
        entry["last_index_mode"] = index_mode

    if settings.multi_vault_index_enabled:
        vaults = dict(previous.get("vaults") or {})
        if "vault_path" in previous and "vaults" not in previous:
            legacy_key = vault_index_key(Path(previous["vault_path"]))
            vaults[legacy_key] = {
                key: previous[key]
                for key in (
                    "vault_path",
                    "embedding_provider",
                    "embedding_model",
                    "collection_suffix",
                    "chunk_size",
                    "chunk_overlap",
                    "chunk_count",
                    "note_count",
                    "indexed_at",
                    "note_fingerprints",
                    "last_index_mode",
                )
                if key in previous
            }
        vaults[key] = entry
        meta = {
            "multi_vault": True,
            "vaults": vaults,
            "active_vault": key,
            **entry,
        }
    else:
        meta = entry

    path = index_meta_path()
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def load_index_meta() -> dict | None:
    path = index_meta_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def stale_note_count(vault_path: Path | None = None) -> int:
    """How many vault notes differ from the last index (0 when unknown)."""
    meta = load_index_meta()
    if meta is None:
        return 0
    resolved = vault_path
    if resolved is None and settings.vault_path:
        resolved = settings.vault_path.resolve()
    if resolved is None:
        indexed = meta.get("vault_path")
        if indexed:
            resolved = Path(indexed).resolve()
    if resolved is None or not resolved.is_dir():
        return 0
    vault_meta = resolve_vault_meta(meta, resolved) or meta
    fingerprints = vault_meta.get("note_fingerprints") or {}
    if not fingerprints:
        return 0
    return count_stale_vault_notes(resolved, fingerprints)


def collect_index_warnings(*, indexed_chunks: int, vault_path: Path | None = None) -> list[str]:
    """Human-readable warnings about index freshness / configuration mismatch."""
    warnings: list[str] = []
    meta = load_index_meta()

    if indexed_chunks <= 0:
        warnings.append(
            "No indexed chunks — novelty search will treat everything as novel. Index a vault first."
        )
        return warnings

    if meta is None:
        warnings.append(
            "Index metadata is missing (older index). Re-index the vault so health checks can verify "
            "the embedding model and vault path."
        )
        return warnings

    active = vault_path or settings.vault_path
    vault_meta = resolve_vault_meta(meta, active.resolve()) if active else meta

    if vault_meta.get("embedding_provider") != settings.embedding_provider or vault_meta.get(
        "embedding_model"
    ) != settings.embedding_model:
        warnings.append(
            "Embedding settings changed since the last index "
            f"(indexed with {vault_meta.get('embedding_provider')}/{vault_meta.get('embedding_model')}, "
            f"now {settings.embedding_provider}/{settings.embedding_model}). "
            "Re-index the vault or novelty scores will be wrong/empty."
        )

    indexed_chunk_size = vault_meta.get("chunk_size")
    if indexed_chunk_size is None:
        warnings.append(
            "The index predates chunk-size tracking and was likely built with oversized "
            "chunks that the embedding model truncates. Re-index the vault so novelty "
            "scores cover full note contents."
        )
    elif indexed_chunk_size != settings.chunk_size:
        warnings.append(
            f"CHUNK_SIZE changed since the last index (indexed with {indexed_chunk_size}, "
            f"now {settings.chunk_size}). Source chunks and vault chunks are compared at "
            "different granularities — re-index the vault for consistent novelty scores."
        )

    configured = str(active.resolve()) if active else None
    indexed_vault = vault_meta.get("vault_path")
    if configured and indexed_vault and Path(indexed_vault).resolve() != Path(configured).resolve():
        warnings.append(
            f"Configured vault ({configured}) differs from the indexed vault ({indexed_vault}). "
            "Re-index or novelty will compare against the wrong notes."
        )

    fingerprints = vault_meta.get("note_fingerprints") or {}
    if configured and fingerprints:
        stale = count_stale_vault_notes(Path(configured), fingerprints)
        if stale:
            warnings.append(
                f"{stale} vault note(s) changed since the last index — re-index for accurate novelty scores."
            )
    elif configured and not fingerprints and indexed_chunks > 0:
        warnings.append(
            "The index has no per-note fingerprints (older index). Re-index once to enable "
            "incremental updates and staleness detection."
        )

    return warnings
