"""Concept title must stay aligned with note body content."""

from __future__ import annotations

from app.atomic_notes import AtomicTopic, _title_for_split_part, _topics_from_paragraphs
from app.prompts import batch_note_draft_prompt
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.suggest import _build_suggestion, _llm_draft_topics_batch
from app.summarize import ensure_concept_heading


def test_ensure_concept_heading_replaces_mismatched_h1():
    body = "# Overview\n\nGradient descent minimizes loss.\n"
    fixed = ensure_concept_heading(body, "Gradient Descent")
    assert fixed.startswith("# Gradient Descent\n")
    assert "minimizes loss" in fixed


def test_ensure_concept_heading_prepends_when_missing():
    body = "A short definition without a heading.\n"
    fixed = ensure_concept_heading(body, "My Concept")
    assert fixed.startswith("# My Concept\n\nA short definition")


def test_ensure_concept_heading_leaves_subheadings():
    body = "# Wrong\n\n## Summary\n\nDetails.\n"
    fixed = ensure_concept_heading(body, "Right")
    assert fixed.startswith("# Right\n")
    assert "## Summary" in fixed


def test_title_for_split_part_keeps_heading_on_part_one():
    assert _title_for_split_part("Optimization", "Later text about Adam.", 1) == "Optimization"


def test_title_for_split_part_derives_from_body_after_part_one():
    title = _title_for_split_part(
        "Optimization",
        "Adam optimizer adapts learning rates per parameter.",
        2,
    )
    assert "Adam" in title
    assert "(2)" in title or "part 2" in title.lower()


def test_topics_from_paragraphs_retitles_later_chunks(monkeypatch):
    from app import atomic_notes as mod

    monkeypatch.setattr(mod, "_atomic_line_limit", lambda: 2)
    segment = SourceSegment(
        text="ignored",
        location=SourceLocation(page=1),
        index=0,
    )
    paragraphs = [
        "First paragraph about alpha stays under the heading.",
        "Second paragraph.",
        "Beta method uses a different approach entirely and needs its own title.",
        "More beta details continue here for length.",
    ]
    topics = _topics_from_paragraphs("Alpha Heading", segment, paragraphs)
    assert len(topics) >= 2
    assert topics[0].title == "Alpha Heading"
    # Later chunk should not be stuck as only "Alpha Heading (N)" if body differs.
    later = topics[-1]
    assert "Alpha Heading" not in later.title or "Beta" in later.title or later.title != "Alpha Heading"


def test_batch_prompt_requires_stable_id():
    source = LoadedSource(
        title="Src",
        text="x",
        source_type="web",
        source_ref="https://example.com",
    )
    prompt = batch_note_draft_prompt(
        source=source,
        topics=[{"id": "0", "title": "A", "excerpt": "a"}, {"id": "1", "title": "B", "excerpt": "b"}],
        related_links=[],
        max_note_lines=40,
    )
    assert '"id"' in prompt
    assert "Do not swap bodies" in prompt


def test_llm_draft_topics_batch_matches_by_id(monkeypatch):
    source = LoadedSource(
        title="Src",
        text="x",
        source_type="text",
        source_ref="a.txt",
    )
    topics = [
        AtomicTopic(
            title="Alpha",
            segments=[SourceSegment(text="alpha text", location=SourceLocation(), index=0)],
            summary="About alpha",
        ),
        AtomicTopic(
            title="Beta",
            segments=[SourceSegment(text="beta text", location=SourceLocation(), index=1)],
            summary="About beta",
        ),
    ]

    class FakeProvider:
        def complete(self, prompt, *, system=None, json_mode=False):
            # Deliberately swap titles but keep correct ids — old matcher would mis-bind.
            return """[
              {"id": "0", "title": "Beta", "body": "# Alpha\\n\\nAlpha body"},
              {"id": "1", "title": "Alpha", "body": "# Beta\\n\\nBeta body"}
            ]"""

    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: FakeProvider())

    bodies = _llm_draft_topics_batch(source, topics, [])
    assert "Alpha body" in bodies["0"]
    assert "Beta body" in bodies["1"]


def test_build_suggestion_syncs_h1_to_concept(monkeypatch):
    monkeypatch.setattr("app.suggest.get_llm_provider", lambda: None)
    monkeypatch.setattr("app.suggest.settings.include_media", False)
    source = LoadedSource(
        title="Src",
        text="x",
        source_type="text",
        source_ref="a.txt",
    )
    topic = AtomicTopic(
        title="Gradient Descent",
        segments=[
            SourceSegment(
                text="Gradient descent iteratively updates parameters to minimize loss.",
                location=SourceLocation(page=1),
                index=0,
            )
        ],
        summary="Gradient descent",
    )
    suggestion = _build_suggestion(
        source,
        topic,
        [],
        set(),
        use_llm=False,
        pre_drafted_body="# Completely Wrong Title\n\nUpdates parameters.\n",
        vector_store=None,
    )
    assert suggestion.concept_title == "Gradient Descent"
    assert "# Gradient Descent" in suggestion.content
    assert "# Completely Wrong Title" not in suggestion.content
