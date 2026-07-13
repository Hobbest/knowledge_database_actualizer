from __future__ import annotations

from app.novelty import Verdict, analyze_novelty
from app.vectorstore import SimilarChunk, VectorStore


def test_analyze_novelty_known_vs_novel(indexed_store: VectorStore, monkeypatch):
    """Verdict aggregation follows similarity thresholds (faked matches)."""

    def known_matches(texts: list[str], top_k: int = 3, query_tags=None):
        return [
            [
                SimilarChunk(
                    chunk_id="c1",
                    note_path="Python Basics.md",
                    note_title="Python Basics",
                    text="Python basics",
                    similarity=0.92,
                    tags=["python", "programming"],
                )
            ]
            for _ in texts
        ]

    def novel_matches(texts: list[str], top_k: int = 3, query_tags=None):
        return [
            [
                SimilarChunk(
                    chunk_id="c1",
                    note_path="Python Basics.md",
                    note_title="Python Basics",
                    text="unrelated",
                    similarity=0.20,
                )
            ]
            for _ in texts
        ]

    monkeypatch.setattr(indexed_store, "query_similar_many", known_matches)
    known = analyze_novelty("Any chunked source text about python frameworks.", indexed_store)
    assert known.verdict == Verdict.ALREADY_KNOWN
    assert known.novelty_score < 0.5
    assert known.overlapping_notes[0].tags == ["python", "programming"]

    def heading_matches(texts: list[str], top_k: int = 3, query_tags=None):
        return [
            [
                SimilarChunk(
                    chunk_id="c1",
                    note_path="Python Basics.md",
                    note_title="Python Basics",
                    text="Variables and types overview.",
                    similarity=0.88,
                    heading="Core concepts",
                    tags=["python"],
                )
            ]
            for _ in texts
        ]

    monkeypatch.setattr(indexed_store, "query_similar_many", heading_matches)
    overlap = analyze_novelty("Chunk about variables.", indexed_store)
    assert overlap.overlapping_notes[0].sample_heading == "Core concepts"

    monkeypatch.setattr(indexed_store, "query_similar_many", novel_matches)
    novel = analyze_novelty("Any chunked source text about rust ownership.", indexed_store)
    assert novel.verdict == Verdict.NOVEL
    assert novel.novelty_score > known.novelty_score


def test_empty_index_treats_as_novel(vector_store: VectorStore):
    result = analyze_novelty("Anything at all about quantum widgets.", vector_store)
    assert result.verdict == Verdict.NOVEL
    assert vector_store.chunk_count() == 0
