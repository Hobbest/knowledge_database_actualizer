from __future__ import annotations

from app.atomic_notes import score_segments
from app.novelty import Verdict, analyze_novelty, analyze_source_similarity
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.vectorstore import SimilarChunk, VectorStore

# A known first paragraph followed by a genuinely novel second paragraph. Kept
# on single lines so a small chunk_size splits them into separate chunks.
_KNOWN_PARAGRAPH = "Python variables store values and the types stay dynamic in use."
_NOVEL_PARAGRAPH = "Quantumwidget flux lattices entangle resonance harmonics on contact."
_MIXED_TEXT = f"{_KNOWN_PARAGRAPH}\n\n{_NOVEL_PARAGRAPH}"


class _PerChunkStore:
    """Fake store returning a low similarity for the novel chunk, high otherwise."""

    def chunk_count(self) -> int:
        return 5

    def query_similar_many(self, texts, *, top_k, query_tags=None):
        results = []
        for text in texts:
            if "Quantumwidget" in text:
                results.append(
                    [SimilarChunk("c", "n.md", "N", "unrelated", 0.20)]
                )
            else:
                results.append(
                    [SimilarChunk("c", "Python.md", "Python", "Python basics", 0.90)]
                )
        return results


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

    segment_scores = score_segments(
        [SourceSegment("Anything at all about quantum widgets.", SourceLocation())],
        vector_store,
    )
    assert segment_scores[0].is_novel is True
    assert segment_scores[0].is_unknown is False


def test_score_segments_flags_segment_novel_from_a_single_novel_chunk(monkeypatch):
    """A novel chunk inside an otherwise-known segment must make it novel."""
    # Force chunk-sized splitting so the one planning segment yields two chunks.
    monkeypatch.setattr("app.novelty.settings.chunk_size", 80)
    monkeypatch.setattr("app.novelty.settings.chunk_overlap", 0)

    scores = score_segments(
        [SourceSegment(_MIXED_TEXT, SourceLocation(), index=0)],
        _PerChunkStore(),  # type: ignore[arg-type]
    )

    assert len(scores) == 1
    # Whole-segment scoring would have averaged toward the 0.90 known match and
    # reported "known"; chunk-level scoring keeps the 0.20 novel chunk visible.
    assert scores[0].is_novel is True
    assert scores[0].best_similarity == 0.20


def test_analyze_source_similarity_scores_chunks_but_plans_segments(monkeypatch):
    """Verdict is chunk-granular while planning stays at segment granularity."""
    monkeypatch.setattr("app.novelty.settings.chunk_size", 80)
    monkeypatch.setattr("app.novelty.settings.chunk_overlap", 0)
    # Keep the whole source as one planning segment and skip boilerplate drops.
    monkeypatch.setattr("app.novelty.settings.segment_target_chars", 100_000)
    monkeypatch.setattr("app.novelty.settings.filter_boilerplate", False)

    source = LoadedSource(
        title="Mixed",
        text=_MIXED_TEXT,
        source_type="text",
        source_ref="mixed.txt",
    )
    analysis = analyze_source_similarity(source, _PerChunkStore())  # type: ignore[arg-type]

    # One planning segment (for note planning) but two scored chunks (for the verdict).
    assert len(analysis.segment_scores) == 1
    assert len(analysis.novelty.chunk_results) == 2
    assert analysis.segment_scores[0].is_novel is True
    # One known + one novel chunk -> neither ratio dominates.
    assert analysis.novelty.verdict == Verdict.PARTIALLY_NEW
