"""Shared vault→chunk→fingerprint orchestration for vector backends."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.chunking import chunk_text
from app.config import settings
from app.index_meta import load_index_meta, save_index_meta
from app.vault import VaultIndexResult, VaultNote, embedding_text_for_note, load_note, load_vault
from app.vault_fingerprints import index_config_changed, note_fingerprint
from app.vault_index import resolve_vault_meta
from app.wikilinks import WikilinkIndex, build_wikilink_index


@dataclass
class IndexedChunk:
    chunk_id: str
    note_path: str
    note_title: str
    text: str
    heading: str | None
    wikilinks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def chroma_metadata(self) -> dict[str, Any]:
        return {
            "note_path": self.note_path,
            "note_title": self.note_title,
            "heading": self.heading or "",
            "wikilinks": ",".join(self.wikilinks),
            "tags": ",".join(self.tags),
        }


@dataclass
class VaultIndexPlan:
    vault_path: Path
    vault_result: VaultIndexResult
    full_rebuild: bool
    to_index: list[VaultNote]
    skipped_paths: list[str]
    removed_paths: list[str]
    current_notes: dict[str, VaultNote]
    notes_by_path: dict[str, VaultNote]
    link_index: WikilinkIndex | None
    prior_fingerprints: dict[str, dict]

    @property
    def note_count(self) -> int:
        return self.vault_result.note_count


def build_wikilink_context(vault_result: VaultIndexResult) -> tuple[dict[str, VaultNote], WikilinkIndex | None]:
    notes_by_path = {note.path.as_posix(): note for note in vault_result.notes}
    link_index = (
        build_wikilink_index(vault_result.notes)
        if settings.transclude_depth > 0
        else None
    )
    return notes_by_path, link_index


def build_chunks_for_note(
    note: VaultNote,
    *,
    notes_by_path: dict[str, VaultNote] | None = None,
    link_index: WikilinkIndex | None = None,
) -> list[IndexedChunk]:
    note_chunks = chunk_text(
        embedding_text_for_note(
            note,
            notes_by_path=notes_by_path,
            link_index=link_index,
        ),
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        source_label=note.title,
    )
    rel = note.path.as_posix()
    return [
        IndexedChunk(
            chunk_id=f"{rel}::{chunk.index}",
            note_path=rel,
            note_title=note.title,
            text=chunk.text,
            heading=chunk.heading,
            wikilinks=list(note.wikilinks),
            tags=list(note.tags),
        )
        for chunk in note_chunks
    ]


def iter_vault_index_chunks(plan: VaultIndexPlan) -> Iterator[tuple[VaultNote, list[IndexedChunk]]]:
    for note in plan.to_index:
        yield note, build_chunks_for_note(
            note,
            notes_by_path=plan.notes_by_path,
            link_index=plan.link_index,
        )


def fingerprints_for_vault(vault_path: Path, notes: list[VaultNote]) -> dict[str, dict]:
    return {note.path.as_posix(): note_fingerprint(vault_path, note) for note in notes}


def merge_note_fingerprints(vault_path: Path, relative_paths: list[str]) -> dict[str, dict]:
    meta = load_index_meta() or {}
    fingerprints = dict(meta.get("note_fingerprints") or {})
    vault_path = vault_path.resolve()
    for rel in relative_paths:
        note = load_note(vault_path, rel)
        if note is None:
            fingerprints.pop(rel, None)
        else:
            fingerprints[rel] = note_fingerprint(vault_path, note)
    return fingerprints


def plan_vault_index(vault_path: Path) -> VaultIndexPlan:
    """Decide which notes to (re)index based on fingerprints and config."""
    vault_path = vault_path.resolve()
    vault_result = load_vault(vault_path)
    meta = load_index_meta()
    vault_meta = resolve_vault_meta(meta, vault_path) if meta else None
    fingerprints: dict[str, dict] = dict((vault_meta or {}).get("note_fingerprints") or {})

    same_vault = (
        vault_meta is not None
        and vault_meta.get("vault_path") == str(vault_path)
        and not index_config_changed(vault_meta)
        and bool(fingerprints)
    )
    full_rebuild = not same_vault

    current_notes = {note.path.as_posix(): note for note in vault_result.notes}
    notes_by_path, link_index = build_wikilink_context(vault_result)

    stored_paths = set(fingerprints.keys())
    current_paths = set(current_notes.keys())
    removed_paths = sorted(stored_paths - current_paths)

    to_index: list[VaultNote] = []
    skipped_paths: list[str] = []
    for rel, note in current_notes.items():
        fp = note_fingerprint(vault_path, note)
        if not full_rebuild and fingerprints.get(rel) == fp:
            skipped_paths.append(rel)
        else:
            to_index.append(note)

    return VaultIndexPlan(
        vault_path=vault_path,
        vault_result=vault_result,
        full_rebuild=full_rebuild,
        to_index=to_index,
        skipped_paths=skipped_paths,
        removed_paths=removed_paths,
        current_notes=current_notes,
        notes_by_path=notes_by_path,
        link_index=link_index,
        prior_fingerprints=fingerprints if not full_rebuild else {},
    )


def finalize_index_meta(
    *,
    vault_path: Path,
    chunk_count: int,
    note_count: int | None,
    note_fingerprints: dict[str, dict],
    index_mode: str,
) -> dict:
    return save_index_meta(
        vault_path=vault_path,
        chunk_count=chunk_count,
        note_count=note_count,
        note_fingerprints=note_fingerprints,
        index_mode=index_mode,
    )


def index_stats_from_plan(
    plan: VaultIndexPlan,
    *,
    chunk_count: int,
    indexed_note_count: int,
    chunk_delta: int,
) -> dict[str, Any]:
    return {
        "vault_path": str(plan.vault_path),
        "note_count": plan.note_count,
        "link_count": plan.vault_result.link_count,
        "chunk_count": chunk_count,
        "duplicate_stems": plan.vault_result.duplicate_stems,
        "index_mode": "full" if plan.full_rebuild else "incremental",
        "indexed_notes": indexed_note_count,
        "skipped_notes": len(plan.skipped_paths),
        "removed_notes": len(plan.removed_paths),
        "chunks_added": chunk_delta,
    }
