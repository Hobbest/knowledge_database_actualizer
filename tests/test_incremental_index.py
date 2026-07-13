"""Incremental vault indexing and staleness detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.index_meta import collect_index_warnings, load_index_meta
from app.vault import load_note
from app.vault_fingerprints import count_stale_vault_notes, note_content_hash
from app.vectorstore import VectorStore


def test_incremental_index_skips_unchanged_notes(
    tmp_data_dir, vector_store: VectorStore, sample_vault: Path
):
    first = vector_store.index_vault(sample_vault)
    assert first["index_mode"] == "full"
    assert first["chunk_count"] > 0

    second = vector_store.index_vault(sample_vault)
    assert second["index_mode"] == "incremental"
    assert second["skipped_notes"] == first["note_count"]
    assert second["indexed_notes"] == 0
    assert second["chunk_count"] == first["chunk_count"]


def test_incremental_index_picks_up_changed_note(
    tmp_data_dir, vector_store: VectorStore, sample_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    vector_store.index_vault(sample_vault)

    note_path = sample_vault / "Python Basics.md"
    original = note_path.read_text(encoding="utf-8")
    note_path.write_text(original + "\n\nNew paragraph about asyncio event loops.\n", encoding="utf-8")
    try:
        result = vector_store.index_vault(sample_vault)
        assert result["index_mode"] == "incremental"
        assert result["indexed_notes"] == 1
        assert result["skipped_notes"] == result["note_count"] - 1
        assert result["chunk_count"] >= vector_store.chunk_count()
    finally:
        note_path.write_text(original, encoding="utf-8")


def test_staleness_warning_after_vault_edit(
    tmp_data_dir, vector_store: VectorStore, sample_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    vector_store.index_vault(sample_vault)
    monkeypatch.setattr("app.config.settings.vault_path", sample_vault)

    note_path = sample_vault / "Python Basics.md"
    original = note_path.read_text(encoding="utf-8")
    note_path.write_text(original + "\n\nEdited outside the app.\n", encoding="utf-8")
    try:
        warnings = collect_index_warnings(indexed_chunks=vector_store.chunk_count())
        assert any("changed since the last index" in w for w in warnings)
        meta = load_index_meta()
        stale = count_stale_vault_notes(sample_vault, meta.get("note_fingerprints") or {})
        assert stale >= 1
    finally:
        note_path.write_text(original, encoding="utf-8")


def test_note_hash_changes_with_content(sample_vault: Path):
    note = load_note(sample_vault, "Python Basics.md")
    assert note is not None
    h1 = note_content_hash(note)
    note.content += "\nextra"
    h2 = note_content_hash(note)
    assert h1 != h2
