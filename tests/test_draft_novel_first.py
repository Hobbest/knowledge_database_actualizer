"""Novel-first topic capping."""

from __future__ import annotations

from app.atomic_notes import AtomicTopic
from app.config import settings
from app.sources.base import SourceLocation, SourceSegment


def test_draft_novel_first_prefers_novel_when_capping(monkeypatch):
    monkeypatch.setattr(settings, "max_notes_per_source", 2)
    monkeypatch.setattr(settings, "draft_novel_first", True)

    known = AtomicTopic(
        title="Known",
        segments=[SourceSegment(text="k", location=SourceLocation(), index=0)],
        is_novel=False,
    )
    novel_a = AtomicTopic(
        title="Novel A",
        segments=[SourceSegment(text="a", location=SourceLocation(), index=1)],
        is_novel=True,
    )
    novel_b = AtomicTopic(
        title="Novel B",
        segments=[SourceSegment(text="b", location=SourceLocation(), index=2)],
        is_novel=True,
    )

    topics = [known, novel_a, novel_b]
    if settings.draft_novel_first:
        topics = sorted(topics, key=lambda topic: (0 if topic.is_novel else 1))
    capped = topics[: settings.max_notes_per_source]
    assert [topic.title for topic in capped] == ["Novel A", "Novel B"]
