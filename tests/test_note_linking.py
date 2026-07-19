from __future__ import annotations

from app.atomic_notes import AtomicTopic, score_segments
from app.novelty import NoveltyResult, Verdict
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.suggest import (
    NoteSuggestion,
    _inject_related_links,
    _link_sibling_notes,
    _strip_related_notes_section,
    _topic_related_links,
    iter_note_suggestions,
)
from app.summarize import compose_title
from app.vectorstore import SimilarChunk


def _topic(title: str, index: int, *, is_novel: bool = True) -> AtomicTopic:
    return AtomicTopic(
        title=title,
        segments=[
            SourceSegment(
                text=f"{title} explains a distinct idea with enough words for drafting.",
                location=SourceLocation(page=1),
                index=index,
            )
        ],
        summary=title,
        is_novel=is_novel,
    )


class _FakeStore:
    def __init__(self, matches: list[SimilarChunk], count: int = 5):
        self._matches = matches
        self._count = count

    def chunk_count(self) -> int:
        return self._count

    def query_similar(self, text, *, top_k, query_tags=None):
        return self._matches


def test_topic_related_links_filter_and_fallback():
    """Per-topic links keep only related matches; below NOVEL_THRESHOLD is dropped."""
    store = _FakeStore(
        [
            SimilarChunk("a", "topics/Related.md", "Related", "t", 0.80, base_similarity=0.80),
            SimilarChunk("b", "topics/Weak.md", "Weak", "t", 0.30, base_similarity=0.30),
        ]
    )
    topic = _topic("Concept", 0)
    links = _topic_related_links(store, topic, source_tags=None, fallback=["[[Fallback]]"])
    assert links == ["[[topics/Related]]"]

    empty = _FakeStore([], count=0)
    fallback = _topic_related_links(empty, topic, source_tags=None, fallback=["[[Fallback]]"])
    assert fallback == ["[[Fallback]]"]


def test_strip_and_inject_related_notes():
    body = "# A\n\n## Related notes\n\n- [[old]]\n\n## Source\n\n- x\n"
    stripped = _strip_related_notes_section(body)
    assert "Related notes" not in stripped
    assert "## Source" in stripped

    content = "# A\n\n## Related notes\n\n- none\n\n## Source\n\n- x\n"
    injected = _inject_related_links(content, ["[[sources/b]]"])
    assert "- [[sources/b]]" in injected
    assert "- none" not in injected
    assert "## Source" in injected


def test_score_segments_uses_raw_cosine_not_tag_boost(monkeypatch):
    """A tag-boosted similarity must not hide a novel segment during planning."""

    class TagBoostedStore:
        def chunk_count(self):
            return 5

        def query_similar_many(self, texts, *, top_k, query_tags=None):
            # Raw cosine below NOVEL_THRESHOLD, but the ranking score is boosted.
            return [
                [SimilarChunk("c", "n.md", "N", "t", 0.80, base_similarity=0.50)]
                for _ in texts
            ]

    monkeypatch.setattr("app.atomic_notes.settings.novel_threshold", 0.55)
    scores = score_segments(
        [SourceSegment("A concept about something.", SourceLocation(), index=0)],
        TagBoostedStore(),  # type: ignore[arg-type]
    )
    assert scores[0].best_similarity == 0.50
    assert scores[0].is_novel is True


def test_link_sibling_notes_connects_similar_notes():
    def note(path: str) -> NoteSuggestion:
        return NoteSuggestion(
            concept_title=path,
            note_path=path,
            content=f"# {path}\n\n## Related notes\n\n- none\n\n## Source\n\n- x\n",
            location={},
            segment_indices=[],
        )

    a, b, c = note("sources/a.md"), note("sources/b.md"), note("sources/c.md")

    class SiblingService:
        def embed_texts(self, texts, *, task_type=None):
            return [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]]

    class SiblingStore:
        embedding_service = SiblingService()

    _link_sibling_notes(
        [(a, "alpha"), (b, "beta"), (c, "gamma")],
        SiblingStore(),  # type: ignore[arg-type]
    )

    assert "[[sources/b]]" in a.content
    assert "[[sources/a]]" in b.content
    assert "- none" in c.content  # too dissimilar to link


def test_novel_topics_drafted_before_known(monkeypatch, tmp_data_dir):
    """Limited LLM budget is spent on novel topics first (source order preserved)."""
    source = LoadedSource(
        title="Src",
        text="content for drafting " * 20,
        source_type="text",
        source_ref="a.txt",
    )
    specs = [("Known one", False), ("Novel one", True), ("Known two", False), ("Novel two", True)]
    topics = [_topic(title, i, is_novel=is_novel) for i, (title, is_novel) in enumerate(specs)]
    expected_order = [
        compose_title("Novel one"),
        compose_title("Novel two"),
        compose_title("Known one"),
        compose_title("Known two"),
    ]
    novelty = NoveltyResult(
        verdict=Verdict.PARTIALLY_NEW,
        novelty_score=0.5,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )

    class OrderProvider:
        def __init__(self):
            self.titles: list[str] = []

        def complete(self, prompt, *, system=None, json_mode=False):
            import json
            import re

            self.titles.extend(re.findall(r'"title":\s*"([^"]+)"', prompt))
            ids = re.findall(r'"id":\s*"(\d+)"', prompt)
            return json.dumps(
                [{"id": i, "title": "T", "body": f"# T{i}\\n\\nbody {i}."} for i in ids]
            )

    provider = OrderProvider()
    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: provider)
    monkeypatch.setattr("app.suggest.settings.llm_draft_batch_size", 4)
    monkeypatch.setattr("app.suggest.settings.generate_moc", False)
    monkeypatch.setattr("app.suggest.settings.include_media", False)
    monkeypatch.setattr("app.suggest.settings.link_sibling_notes", False)
    monkeypatch.setattr("app.suggest.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.suggest.settings.llm_model", "gemini-test")
    monkeypatch.setattr("app.suggest.settings.llm_api_key", "test-key")
    monkeypatch.setattr("app.suggest.settings.llm_max_calls_per_run", 0)
    monkeypatch.setattr("app.suggest.settings.llm_max_input_chars_per_run", 0)
    monkeypatch.setattr("app.suggest._plan_topics", lambda *_a, **_k: topics)

    events = list(iter_note_suggestions(source, novelty, vector_store=None))
    final = next(event for event in events if event.get("type") == "suggestions")

    assert provider.titles == expected_order
    # Output order still follows the source (reading) order.
    assert [s.concept_title for s in final["suggestions"]] == [
        compose_title(title) for title, _ in specs
    ]
