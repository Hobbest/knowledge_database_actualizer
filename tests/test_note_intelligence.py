from __future__ import annotations

from app.note_intelligence import (
    detect_duplicates,
    format_vault_context,
    retrieve_vault_context,
    score_note_quality,
)
from app.prompts import batch_note_draft_prompt, note_draft_prompt
from app.sources.base import LoadedSource
from app.vectorstore import SimilarChunk


class _FakeStore:
    def __init__(self, matches: list[SimilarChunk], count: int = 5):
        self._matches = matches
        self._count = count

    def chunk_count(self) -> int:
        return self._count

    def query_similar(self, text, *, top_k, query_tags=None):
        return self._matches[:top_k]


def test_retrieve_vault_context_formats_excerpts(monkeypatch):
    monkeypatch.setattr("app.note_intelligence.settings.draft_rag_enabled", True)
    monkeypatch.setattr("app.note_intelligence.settings.novel_threshold", 0.55)
    store = _FakeStore(
        [
            SimilarChunk(
                "a",
                "topics/Related.md",
                "Related",
                "A long enough vault excerpt about the concept.",
                0.82,
                heading="Overview",
                base_similarity=0.82,
            ),
            SimilarChunk(
                "b",
                "topics/Weak.md",
                "Weak",
                "Too weak",
                0.2,
                base_similarity=0.2,
            ),
        ]
    )
    chunks = retrieve_vault_context(store, "concept query")  # type: ignore[arg-type]
    assert len(chunks) == 1
    assert chunks[0].note_path == "topics/Related.md"
    block = format_vault_context(chunks)
    assert "[[topics/Related.md]]" in block
    assert "Overview" in block


def test_score_note_quality_rewards_structure():
    good = score_note_quality(
        concept_title="Concept",
        content=(
            "---\ntype: atomic\n---\n"
            "# Concept\n\n"
            "A precise definition of the concept for the vault.\n\n"
            "- Point one\n- Point two\n- Point three\n\n"
            "## Related notes\n\n- [[topics/Other]]\n\n"
            "## Source\n\n- File: `a.md`\n"
        ),
    )
    assert good["quality_score"] is not None
    assert good["quality_score"] >= 0.8

    weak = score_note_quality(concept_title="Concept", content="# Wrong\n\nshort\n")
    assert weak["quality_score"] is not None
    assert weak["quality_score"] < good["quality_score"]
    assert "heading_mismatch" in weak["quality_flags"]


def test_detect_duplicates_marks_near_copies(monkeypatch):
    monkeypatch.setattr(
        "app.note_intelligence.settings.duplicate_detection_enabled", True
    )
    monkeypatch.setattr(
        "app.note_intelligence.settings.duplicate_similarity_threshold", 0.9
    )

    class Service:
        def embed_texts(self, texts, *, task_type=None):
            # First two nearly identical, third orthogonal.
            return [[1.0, 0.0], [0.999, 0.01], [0.0, 1.0]]

    class Store:
        embedding_service = Service()

    items = [
        {
            "concept_title": "Alpha",
            "note_path": "sources/a.md",
            "content": "# Alpha\n\nbody a",
            "is_novel": True,
        },
        {
            "concept_title": "Alpha copy",
            "note_path": "sources/b.md",
            "content": "# Alpha copy\n\nbody a almost",
            "is_novel": True,
        },
        {
            "concept_title": "Gamma",
            "note_path": "sources/c.md",
            "content": "# Gamma\n\nother",
            "is_novel": True,
        },
    ]
    detect_duplicates(items, Store())  # type: ignore[arg-type]
    assert items[1]["duplicate_of"] == "sources/a.md"
    assert items[1]["duplicate_similarity"] >= 0.9
    assert "duplicate_of" not in items[0]
    assert "duplicate_of" not in items[2]


def test_draft_prompts_include_vault_context():
    source = LoadedSource(
        title="Src",
        text="text",
        source_type="text",
        source_ref="a.txt",
    )
    single = note_draft_prompt(
        source=source,
        concept_title="Concept",
        location_display="page 1",
        excerpt="excerpt",
        related_links=["[[topics/Related]]"],
        max_note_lines=40,
        vault_context="- [[topics/Related]] (Related)\n  Existing definition.",
    )
    assert "Existing vault context" in single
    assert "[[topics/Related]]" in single

    batch = batch_note_draft_prompt(
        source=source,
        topics=[{"id": "0", "title": "Concept", "excerpt": "x"}],
        related_links=[],
        max_note_lines=30,
        vault_context="- [[topics/Related]]",
    )
    assert "Existing vault context" in batch
