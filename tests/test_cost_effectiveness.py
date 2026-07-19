from __future__ import annotations

import pytest
from app.atomic_notes import AtomicTopic
from app.checkpoint import SuggestionCheckpoint
from app.embeddings import EmbeddingBackend, EmbeddingService
from app.llm import CompletionUsage, call_with_retry
from app.llm_budget import LLMBudget
from app.novelty import (
    NoveltyResult,
    Verdict,
    analyze_source_similarity,
    novelty_to_checkpoint,
    prepare_planning_segments,
)
from app.sources import SourceDispatcher
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.suggest import (
    NoteSuggestion,
    _batch_draft_prompt_chars,
    _llm_draft_topic_body,
    analysis_fingerprint,
    iter_note_suggestions,
    topics_to_checkpoint,
)
from app.vectorstore import SimilarChunk


def _source() -> LoadedSource:
    text = "A sufficiently detailed concept explains a useful technical idea."
    return LoadedSource(
        title="Source",
        text=text,
        source_type="text",
        source_ref="source.txt",
    )


def test_failed_retried_calls_are_all_recorded(monkeypatch, tmp_data_dir):
    attempts = {"count": 0}

    class RateLimitedProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            attempts["count"] += 1
            raise RuntimeError("429 rate limit")

    source = _source()
    topic = AtomicTopic("Concept", source.segments, "Concept")
    budget = LLMBudget(max_calls=0, max_input_chars=0)
    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: RateLimitedProvider())

    with pytest.raises(RuntimeError, match="429"):
        call_with_retry(
            lambda: _llm_draft_topic_body(source, topic, [], budget=budget)
        )

    assert attempts["count"] == 3  # initial attempt + two configured retries
    assert budget.calls == 3
    assert budget.input_chars > 0


def test_budget_summary_reports_provider_usage():
    budget = LLMBudget(max_calls=10, max_input_chars=10_000)
    budget.record(
        400,
        CompletionUsage(input_tokens=90, output_tokens=30),
    )

    summary = budget.summary()
    assert summary["actual_input_tokens"] == 90
    assert summary["actual_output_tokens"] == 30
    assert summary["calls_with_usage"] == 1
    assert summary["estimated_input_tokens"] == 100


def test_batch_drafting_shrinks_to_use_remaining_budget(monkeypatch, tmp_data_dir):
    """A full-size batch that would overshoot must shrink, not exhaust early."""
    source = LoadedSource(
        title="Src",
        text="alpha beta gamma content for extractive fallbacks " * 20,
        source_type="text",
        source_ref="a.txt",
    )
    topics = [
        AtomicTopic(
            title=title,
            segments=[
                SourceSegment(
                    text=f"{title} explains a distinct idea with enough words for drafting.",
                    location=SourceLocation(page=1),
                    index=index,
                )
            ],
            summary=title,
        )
        for index, title in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])
    ]
    novelty = NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )

    full_batch_chars = _batch_draft_prompt_chars(source, topics[:3], [])
    single_chars = _batch_draft_prompt_chars(source, topics[:1], [])
    budget_cap = full_batch_chars + single_chars + 50

    call_sizes: list[int] = []

    class FakeProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            import json
            import re

            ids = re.findall(r'"id":\s*"(\d+)"', prompt)
            size = len(ids) or 1
            call_sizes.append(size)
            return json.dumps(
                [
                    {
                        "id": str(i),
                        "title": f"T{i}",
                        "body": f"# Note {i}\\n\\nLLM body {i}.",
                    }
                    for i in range(size)
                ]
            )

    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.suggest.settings.llm_draft_batch_size", 3)
    monkeypatch.setattr("app.suggest.settings.generate_moc", False)
    monkeypatch.setattr("app.suggest.settings.include_media", False)
    monkeypatch.setattr("app.suggest.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.suggest.settings.llm_model", "gemini-test")
    monkeypatch.setattr("app.suggest.settings.llm_api_key", "test-key")
    monkeypatch.setattr("app.suggest.settings.llm_max_calls_per_run", 0)
    monkeypatch.setattr("app.suggest.settings.llm_max_input_chars_per_run", budget_cap)
    monkeypatch.setattr("app.suggest._plan_topics", lambda *_a, **_k: topics)

    events = list(iter_note_suggestions(source, novelty, vector_store=None))
    final = next(event for event in events if event.get("type") == "suggestions")
    budget = final["llm_budget"]

    assert call_sizes[0] == 3
    assert 1 in call_sizes[1:], f"expected a shrunk follow-up batch, got {call_sizes}"
    assert budget["input_chars"] <= budget_cap
    assert budget["input_chars"] > full_batch_chars
    assert len(final["suggestions"]) == 5


def test_refuse_message_mentions_leftover_budget():
    budget = LLMBudget(max_calls=10, max_input_chars=400_000)
    budget.record(284_002)
    reason = budget.refuse(150_000)
    assert "284,002/400,000" in reason
    assert "115,998 remaining" in reason
    assert "cannot fit" in reason.lower()


def test_single_similarity_pass_builds_verdict_and_segment_scores(monkeypatch):
    calls = {"count": 0}

    class FakeStore:
        def chunk_count(self):
            return 1

        def query_similar_many(self, texts, *, top_k, query_tags=None):
            calls["count"] += 1
            assert top_k == 3
            return [
                [
                    SimilarChunk(
                        chunk_id="known::0",
                        note_path="known.md",
                        note_title="Known",
                        text="Known concept",
                        similarity=0.9,
                    )
                ]
                for _ in texts
            ]

    monkeypatch.setattr("app.novelty.settings.embedding_query_batch_size", 32)
    analysis = analyze_source_similarity(_source(), FakeStore())  # type: ignore[arg-type]

    assert calls["count"] == 1
    assert analysis.novelty.verdict == Verdict.ALREADY_KNOWN
    assert analysis.segment_scores
    assert analysis.segment_scores[0].best_similarity == 0.9
    assert analysis.segment_scores[0].is_novel is False


def test_query_embeddings_are_cached():
    class CountingBackend(EmbeddingBackend):
        def __init__(self):
            self.calls = 0

        def embed_texts(self, texts, *, task_type=None):
            self.calls += 1
            return [[float(len(text)), 1.0] for text in texts]

    backend = CountingBackend()
    service = EmbeddingService(backend=backend)
    first = service.embed_texts(["cache-only-test-text"], task_type="RETRIEVAL_QUERY")
    second = service.embed_texts(["cache-only-test-text"], task_type="RETRIEVAL_QUERY")

    assert first == second
    assert backend.calls == 1


def _interrupted_checkpoint(
    body: bytes,
    *,
    reusable: bool,
) -> tuple[LoadedSource, SuggestionCheckpoint]:
    source = SourceDispatcher().load_from_bytes("resume.md", body)
    segments = prepare_planning_segments(source)
    topic = AtomicTopic("Alpha", [segments[0]], "Alpha concept", is_novel=True)
    novelty = NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[segments[0].text],
        known_chunks=[],
    )
    checkpoint = SuggestionCheckpoint.for_source(
        source.source_type,
        source.source_ref,
        source_key=source.source_key,
    )
    checkpoint.start(
        {
            "title": source.title,
            "source_type": source.source_type,
            "source_ref": source.source_ref,
            "source_key": source.source_key,
        }
    )
    if reusable:
        checkpoint.set_analysis(
            plan=topics_to_checkpoint([topic]),
            novelty=novelty_to_checkpoint(novelty),
            plan_fingerprint=analysis_fingerprint(source),
        )
    checkpoint.add(
        NoteSuggestion(
            concept_title="Alpha",
            note_path="sources/alpha.md",
            content="# Alpha\n\nRecovered body.",
            location={},
            segment_indices=[segments[0].index],
        ).to_dict()
    )
    checkpoint.finish(completed=False)
    return source, checkpoint


def test_resume_reuses_similarity_and_plan_without_calls(
    monkeypatch,
    tmp_data_dir,
    vector_store,
):
    import app.main as main_module

    body = b"# Alpha\n\nA detailed alpha concept with enough explanatory text.\n"
    _interrupted_checkpoint(body, reusable=True)
    monkeypatch.setattr(main_module, "get_vector_store", lambda _path=None: vector_store)
    monkeypatch.setattr(
        main_module,
        "analyze_source_similarity",
        lambda *_args, **_kwargs: pytest.fail("resume must not embed source"),
    )
    monkeypatch.setattr(
        "app.suggest._plan_topics",
        lambda *_args, **_kwargs: pytest.fail("resume must not re-plan topics"),
    )

    events = list(
        main_module._iter_analyze_events(
            clean_url=None,
            file_name="resume.md",
            file_bytes=body,
            resume=True,
        )
    )

    assert not [event for event in events if event["type"] == "error"]
    result = next(event for event in events if event["type"] == "result")
    assert result["suggestions"][0]["content"] == "# Alpha\n\nRecovered body."


@pytest.mark.parametrize("reusable", [False, True])
def test_resume_recomputes_for_legacy_or_fingerprint_mismatch(
    monkeypatch,
    tmp_data_dir,
    vector_store,
    reusable,
):
    import app.main as main_module

    body = b"# Alpha\n\nA distinct alpha concept with sufficient explanatory text.\n"
    _source_value, checkpoint = _interrupted_checkpoint(body, reusable=reusable)
    if reusable:
        # Preserve a valid plan but invalidate its compatibility key.
        checkpoint.set_analysis(
            plan=checkpoint._state["plan"],
            novelty=checkpoint._state["novelty"],
            plan_fingerprint="stale-fingerprint",
        )

    original = analyze_source_similarity
    calls = {"count": 0}

    def counted_analysis(source, store):
        calls["count"] += 1
        return original(source, store)

    monkeypatch.setattr(main_module, "get_vector_store", lambda _path=None: vector_store)
    monkeypatch.setattr(main_module, "analyze_source_similarity", counted_analysis)
    events = list(
        main_module._iter_analyze_events(
            clean_url=None,
            file_name="resume.md",
            file_bytes=body,
            resume=True,
        )
    )

    assert calls["count"] == 1
    assert any(event["type"] == "result" for event in events)
