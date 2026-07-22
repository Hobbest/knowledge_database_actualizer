"""Tests for shared vault indexing orchestration."""

from __future__ import annotations

from pathlib import Path

from app.indexing import (
    build_chunks_for_note,
    fingerprints_for_vault,
    plan_vault_index,
)
from app.vault import load_note


def test_build_chunks_for_note_includes_tags_and_wikilinks(sample_vault: Path):
    note = load_note(sample_vault, "Python Basics.md")
    assert note is not None
    chunks = build_chunks_for_note(note)
    assert chunks
    assert chunks[0].tags == note.tags
    assert chunks[0].wikilinks == note.wikilinks


def test_plan_vault_index_marks_first_run_full(sample_vault: Path, tmp_data_dir):
    plan = plan_vault_index(sample_vault)
    assert plan.full_rebuild is True
    assert plan.to_index
    assert plan.note_count >= 1


def test_fingerprints_for_vault_covers_all_notes(sample_vault: Path):
    plan = plan_vault_index(sample_vault)
    fps = fingerprints_for_vault(plan.vault_path, list(plan.current_notes.values()))
    assert set(fps.keys()) == set(plan.current_notes.keys())
