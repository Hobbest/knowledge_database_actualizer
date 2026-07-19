from __future__ import annotations

import dataclasses
import json
import re

from app.atomic_notes import (
    AtomicTopic,
    SegmentNovelty,
    _plan_windows,
    _reconcile_topics,
    _structural_plan_topics,
    plan_atomic_topics,
)
from app.novelty import NoveltyResult, Verdict
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.text_limits import TEXT_LIMITS


def _segments(count: int) -> list[SourceSegment]:
    return [
        SourceSegment(
            text=f"Segment {i} explains a distinct idea number {i} with enough words.",
            location=SourceLocation(page=i + 1),
            index=i,
        )
        for i in range(count)
    ]


def _novelty() -> NoveltyResult:
    return NoveltyResult(
        verdict=Verdict.NOVEL,
        novelty_score=1.0,
        chunk_results=[],
        overlapping_notes=[],
        novel_chunks=[],
        known_chunks=[],
    )


def _scores(segments: list[SourceSegment]) -> list[SegmentNovelty]:
    return [
        SegmentNovelty(segment=segment, best_similarity=0.0, is_novel=True, is_unknown=False)
        for segment in segments
    ]


def test_plan_windows_partitions_by_segment_cap(monkeypatch):
    small = dataclasses.replace(TEXT_LIMITS, llm_planning_max_segments=2)
    monkeypatch.setattr("app.atomic_notes.TEXT_LIMITS", small)
    windows = _plan_windows(_segments(5))
    assert [len(window) for window in windows] == [2, 2, 1]


def test_reconcile_fills_uncovered_segments():
    segments = _segments(4)
    llm_topics = [AtomicTopic(title="Grouped", segments=[segments[0], segments[1]], summary="s")]
    structural = _structural_plan_topics(segments)

    reconciled = _reconcile_topics(llm_topics, structural, segments)
    covered = {segment.index for topic in reconciled for segment in topic.segments}

    assert covered == {0, 1, 2, 3}  # dropped segments 2,3 were filled structurally
    # Reading order preserved (grouped LLM topic first, fills after).
    firsts = [min(s.index for s in topic.segments) for topic in reconciled]
    assert firsts == sorted(firsts)


def test_reconcile_falls_back_when_under_segmented():
    segments = _segments(6)
    # A single giant LLM topic covering everything is degenerate.
    llm_topics = [AtomicTopic(title="Everything", segments=list(segments), summary="s")]
    structural = _structural_plan_topics(segments)

    reconciled = _reconcile_topics(llm_topics, structural, segments)
    assert len(reconciled) == len(structural)
    assert len(reconciled) > 1


def test_plan_atomic_topics_windows_cover_whole_source(monkeypatch):
    """Windowed planning + reconciliation cover every segment even when the LLM
    drops some and the source spans multiple planning windows."""
    small = dataclasses.replace(TEXT_LIMITS, llm_planning_max_segments=2)
    monkeypatch.setattr("app.atomic_notes.TEXT_LIMITS", small)

    class PlanningProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt, *, system=None, json_mode=False):
            self.calls += 1
            indices = [int(i) for i in re.findall(r'"index":\s*(\d+)', prompt)]
            # Drop the first index of each multi-segment window to force fill.
            keep = indices[1:] if len(indices) > 1 else indices
            return json.dumps(
                [
                    {"title": f"Concept {i}", "segment_indices": [i], "summary": "s"}
                    for i in keep
                ]
            )

    provider = PlanningProvider()
    monkeypatch.setattr("app.atomic_notes.get_llm_provider", lambda: provider)

    source = LoadedSource(title="Src", text="body", source_type="text", source_ref="a.txt")
    segments = _segments(5)
    topics = plan_atomic_topics(source, _scores(segments), _novelty())

    covered = {segment.index for topic in topics for segment in topic.segments}
    assert covered == {0, 1, 2, 3, 4}
    assert provider.calls == 3  # three windows: [0,1] [2,3] [4]


def test_planning_call_cap_limits_llm_windows(monkeypatch):
    small = dataclasses.replace(TEXT_LIMITS, llm_planning_max_segments=1)
    monkeypatch.setattr("app.atomic_notes.TEXT_LIMITS", small)
    monkeypatch.setattr("app.atomic_notes.settings.llm_max_planning_calls", 1)

    class PlanningProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt, *, system=None, json_mode=False):
            self.calls += 1
            indices = [int(i) for i in re.findall(r'"index":\s*(\d+)', prompt)]
            return json.dumps(
                [{"title": f"C{i}", "segment_indices": [i], "summary": "s"} for i in indices]
            )

    provider = PlanningProvider()
    monkeypatch.setattr("app.atomic_notes.get_llm_provider", lambda: provider)

    source = LoadedSource(title="Src", text="body", source_type="text", source_ref="a.txt")
    segments = _segments(4)
    topics = plan_atomic_topics(source, _scores(segments), _novelty())

    # Only one window planned by the LLM; the rest filled structurally.
    assert provider.calls == 1
    covered = {segment.index for topic in topics for segment in topic.segments}
    assert covered == {0, 1, 2, 3}
