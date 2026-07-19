"""Batch drafting must not double-spend with per-note LLM calls on failure."""

from __future__ import annotations

from app.atomic_notes import AtomicTopic
from app.novelty import NoveltyResult, Verdict
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.suggest import _parse_delimited_notes, iter_note_suggestions


def _topic(title: str, index: int, text: str) -> AtomicTopic:
    return AtomicTopic(
        title=title,
        segments=[
            SourceSegment(
                text=text,
                location=SourceLocation(page=1),
                index=index,
            )
        ],
        summary=title,
    )


def test_batch_failure_does_not_call_per_note_llm(monkeypatch):
    source = LoadedSource(
        title="Src",
        text="alpha beta gamma content for extractive fallbacks",
        source_type="text",
        source_ref="a.txt",
    )
    topics = [
        _topic("Alpha", 0, "Alpha explains the first idea in enough words."),
        _topic("Beta", 1, "Beta covers the second idea with more detail."),
        _topic("Gamma", 2, "Gamma finishes with a third distinct concept."),
    ]
    novelty = NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )

    calls: list[str] = []

    class FakeProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            calls.append(prompt[:80])
            raise RuntimeError("simulated batch parse failure")

    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.suggest.settings.llm_draft_batch_size", 3)
    monkeypatch.setattr("app.suggest.settings.llm_disable_after_failures", 2)
    monkeypatch.setattr("app.suggest.settings.generate_moc", False)
    monkeypatch.setattr("app.suggest.settings.include_media", False)
    monkeypatch.setattr("app.suggest.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.suggest.settings.llm_model", "gemini-test")
    monkeypatch.setattr("app.suggest.settings.llm_api_key", "test-key")
    monkeypatch.setattr("app.suggest.settings.llm_max_calls_per_run", 0)
    monkeypatch.setattr("app.suggest.settings.llm_max_input_chars_per_run", 0)

    def fake_plan(*_args, **_kwargs):
        return topics

    monkeypatch.setattr("app.suggest._plan_topics", fake_plan)

    events = list(iter_note_suggestions(source, novelty, vector_store=None))
    final = next(event for event in events if event.get("type") == "suggestions")
    suggestions = final["suggestions"]
    warnings = " ".join(final.get("warnings") or [])

    assert len(suggestions) == 3
    assert len(calls) == 1, f"expected one batch call, got {len(calls)}"
    assert "extractive" in warnings.lower()
    assert "per-note drafting" not in warnings.lower()


def test_batch_partial_success_skips_llm_for_missing(monkeypatch):
    source = LoadedSource(
        title="Src",
        text="alpha beta",
        source_type="text",
        source_ref="a.txt",
    )
    topics = [
        _topic("Alpha", 0, "Alpha idea one with sufficient extractive text."),
        _topic("Beta", 1, "Beta idea two with sufficient extractive text."),
    ]
    novelty = NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )

    calls = {"n": 0}

    class FakeProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            calls["n"] += 1
            return '[{"id": "0", "title": "Alpha", "body": "# Alpha\\n\\nLLM alpha body."}]'

    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.suggest.settings.llm_draft_batch_size", 2)
    monkeypatch.setattr("app.suggest.settings.generate_moc", False)
    monkeypatch.setattr("app.suggest.settings.include_media", False)
    monkeypatch.setattr("app.suggest.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.suggest.settings.llm_model", "gemini-test")
    monkeypatch.setattr("app.suggest.settings.llm_api_key", "test-key")
    monkeypatch.setattr("app.suggest.settings.llm_max_calls_per_run", 0)
    monkeypatch.setattr("app.suggest.settings.llm_max_input_chars_per_run", 0)
    monkeypatch.setattr("app.suggest._plan_topics", lambda *_a, **_k: topics)

    events = list(iter_note_suggestions(source, novelty, vector_store=None))
    final = next(event for event in events if event.get("type") == "suggestions")
    by_title = {s.concept_title: s.content for s in final["suggestions"]}

    assert calls["n"] == 1
    assert "LLM alpha body" in by_title["Alpha"]
    assert "LLM alpha body" not in by_title["Beta"]


def test_parse_delimited_notes_handles_quotes_and_backslashes():
    raw = (
        '===NOTE 0===\n'
        '# Caching\n\n'
        'The "cache" stores results in C:\\Users\\x.\n\n'
        '===NOTE 1===\n'
        '# Beta\n\n'
        'Beta body with a [[wikilink]] and a "quote".\n'
    )
    bodies = _parse_delimited_notes(raw)
    assert set(bodies) == {"0", "1"}
    assert '"cache"' in bodies["0"]
    assert "C:\\Users\\x" in bodies["0"]
    assert "[[wikilink]]" in bodies["1"]


def test_batch_delimited_bodies_with_special_chars_are_used(monkeypatch):
    """A body with quotes/backslashes must draft from the LLM, not fall back."""
    source = LoadedSource(
        title="Src",
        text="alpha beta",
        source_type="text",
        source_ref="a.txt",
    )
    topics = [
        _topic("Alpha", 0, "Alpha idea one with sufficient extractive text."),
        _topic("Beta", 1, "Beta idea two with sufficient extractive text."),
    ]
    novelty = NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )

    calls = {"n": 0}

    class FakeProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            calls["n"] += 1
            return (
                '===NOTE 0===\n'
                '# Alpha\n\n'
                'The "alpha" value lives at C:\\Users\\x — see it.\n\n'
                '===NOTE 1===\n'
                '# Beta\n\n'
                'Beta explains the "beta" concept.\n'
            )

    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.suggest.settings.llm_draft_batch_size", 2)
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
    by_title = {s.concept_title: s.content for s in final["suggestions"]}
    warnings = " ".join(final.get("warnings") or [])

    assert calls["n"] == 1
    assert '"alpha" value lives at C:\\Users\\x' in by_title["Alpha"]
    assert '"beta" concept' in by_title["Beta"]
    assert "extractive" not in warnings.lower()
