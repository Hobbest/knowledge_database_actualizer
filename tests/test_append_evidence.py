"""Stricter append-target evidence (margin + tag confirmation)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.atomic_notes import AtomicTopic
from app.config import settings
from app.note_output import topic_overlap_match
from app.sources.base import SourceLocation, SourceSegment
from app.vectorstore import SimilarChunk


def _topic(text: str = "vector search basics") -> AtomicTopic:
    return AtomicTopic(
        title="Vector search",
        segments=[
            SourceSegment(text=text, location=SourceLocation(), index=0),
        ],
        summary=text,
        is_novel=False,
    )


def test_topic_overlap_requires_content_known_threshold(monkeypatch):
    monkeypatch.setattr(settings, "known_threshold", 0.85)
    monkeypatch.setattr(settings, "append_overlap_margin", 0.0)
    monkeypatch.setattr(settings, "append_require_tag_overlap", False)

    store = SimpleNamespace(
        chunk_count=lambda: 1,
        query_similar=lambda *_a, **_k: [
            SimilarChunk(
                chunk_id="a",
                note_path="notes/a.md",
                note_title="A",
                text="x",
                similarity=0.99,
                base_similarity=0.5,
                tags=["ml"],
            )
        ],
    )
    assert topic_overlap_match(store, _topic(), query_tags=["ml"]) is None


def test_topic_overlap_requires_margin_over_runner_up(monkeypatch):
    monkeypatch.setattr(settings, "known_threshold", 0.7)
    monkeypatch.setattr(settings, "append_overlap_margin", 0.1)
    monkeypatch.setattr(settings, "append_require_tag_overlap", False)

    store = SimpleNamespace(
        chunk_count=lambda: 2,
        query_similar=lambda *_a, **_k: [
            SimilarChunk(
                chunk_id="a",
                note_path="notes/a.md",
                note_title="A",
                text="x",
                similarity=0.9,
                base_similarity=0.82,
            ),
            SimilarChunk(
                chunk_id="b",
                note_path="notes/b.md",
                note_title="B",
                text="y",
                similarity=0.88,
                base_similarity=0.8,
            ),
        ],
    )
    assert topic_overlap_match(store, _topic()) is None

    store.query_similar = lambda *_a, **_k: [
        SimilarChunk(
            chunk_id="a",
            note_path="notes/a.md",
            note_title="A",
            text="x",
            similarity=0.95,
            base_similarity=0.92,
        ),
        SimilarChunk(
            chunk_id="b",
            note_path="notes/b.md",
            note_title="B",
            text="y",
            similarity=0.8,
            base_similarity=0.75,
        ),
    ]
    match = topic_overlap_match(store, _topic())
    assert match is not None
    assert match[0] == "notes/a.md"


def test_topic_overlap_requires_shared_tag_when_both_sides_tagged(monkeypatch):
    monkeypatch.setattr(settings, "known_threshold", 0.7)
    monkeypatch.setattr(settings, "append_overlap_margin", 0.0)
    monkeypatch.setattr(settings, "append_require_tag_overlap", True)

    store = SimpleNamespace(
        chunk_count=lambda: 1,
        query_similar=lambda *_a, **_k: [
            SimilarChunk(
                chunk_id="a",
                note_path="notes/a.md",
                note_title="A",
                text="x",
                similarity=0.95,
                base_similarity=0.9,
                tags=["history"],
            )
        ],
    )
    assert topic_overlap_match(store, _topic(), query_tags=["programming"]) is None
    match = topic_overlap_match(store, _topic(), query_tags=["history"])
    assert match is not None
