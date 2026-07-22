from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import settings
from app.json_extract import extract_json_array
from app.llm import call_with_retry, complete_with_usage, get_llm_provider
from app.novelty import NoveltyResult
from app.prompts import ARCHITECT_SYSTEM_PROMPT, topic_planning_prompt
from app.sources.base import LoadedSource, SourceLocation, SourceSegment, merge_locations
from app.text_limits import TEXT_LIMITS
from app.text_utils import (
    combine_segment_text,
    extract_topic_summary,
    truncate_with_ellipsis,
)
from app.titling import disambiguate_titles, refine_topic_title
from app.vector_protocol import VectorStoreProtocol as VectorStore

# Re-export for callers that imported the private helper from this module.
_extract_json_array = extract_json_array


def _title_for_split_part(base_title: str, body: str, part: int) -> str:
    """Title a structural chunk from its body (``part`` kept for call-site compat)."""
    del part  # Collision suffixes are applied later via disambiguate_titles.
    return refine_topic_title(body, hint=base_title or None)


def _disambiguate_topic_batch(topics: list[AtomicTopic]) -> list[AtomicTopic]:
    """Apply path-uniqueness suffixes after each chunk is body-grounded."""
    if len(topics) <= 1:
        return topics
    unique = disambiguate_titles([topic.title for topic in topics])
    for topic, title in zip(topics, unique, strict=True):
        topic.title = title
    return topics

if TYPE_CHECKING:
    from app.llm_budget import LLMBudget

logger = logging.getLogger(__name__)

HEADING_PATTERN = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
SENTENCE_END_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass
class SegmentNovelty:
    segment: SourceSegment
    best_similarity: float
    is_novel: bool
    # True when similarity failed unexpectedly (for example, a rate limit).
    is_unknown: bool = False


@dataclass
class AtomicTopic:
    title: str
    segments: list[SourceSegment]
    summary: str = ""
    is_novel: bool = True


def score_segment(
    segment: SourceSegment,
    vector_store: VectorStore,
    *,
    source_tags: list[str] | None = None,
) -> SegmentNovelty:
    """Score a single segment's novelty against the indexed vault."""
    return score_segments([segment], vector_store, source_tags=source_tags)[0]


def score_segments(
    segments: list[SourceSegment],
    vector_store: VectorStore,
    *,
    source_tags: list[str] | None = None,
    on_batch: Callable[..., None] | None = None,
) -> list[SegmentNovelty]:
    """Score segments for planning, judging novelty at ``chunk_size``.

    Each planning segment is split into chunk-sized units (the vault's index
    granularity) and scored; the chunk verdicts are aggregated back up so a
    segment counts as novel when any of its chunks carries new content. On an
    embedding rate-limit remaining chunks degrade to ``is_unknown`` (not novel)
    so planning never invents novelty.
    """
    # Imported here to avoid a module cycle (novelty imports SegmentNovelty).
    from app.novelty import _score_segments_via_chunks

    segment_scores, _chunk_scores, _rate_limited = _score_segments_via_chunks(
        segments,
        vector_store,
        source_tags=source_tags,
        top_k=1,
        on_batch=on_batch,
    )
    return segment_scores


def plan_atomic_topics(
    source: LoadedSource,
    segment_scores: list[SegmentNovelty],
    novelty: NoveltyResult,
    *,
    budget: LLMBudget | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> list[AtomicTopic]:
    """Plan one note per distinct concept across the full source."""
    all_segments = [item.segment for item in segment_scores] or source.segments
    if not all_segments:
        return []

    novelty_by_index = {item.segment.index: item for item in segment_scores}
    structural_topics = _structural_plan_topics(all_segments)

    # Plan across the whole source in bounded windows (map), then reconcile with
    # structural coverage (reduce): keep the LLM's concept grouping while making
    # sure no segment is ever dropped from note generation.
    llm_topics = _llm_plan_topics_windowed(
        source,
        all_segments,
        novelty,
        budget=budget,
        progress=progress,
    )
    topics = _reconcile_topics(llm_topics, structural_topics, all_segments)

    for topic in topics:
        scores = [
            novelty_by_index[segment.index]
            for segment in topic.segments
            if segment.index in novelty_by_index
        ]
        known_scores = [score for score in scores if not score.is_unknown]
        if known_scores:
            topic.is_novel = any(score.is_novel for score in known_scores)
        else:
            # No reliable similarity — do not claim novelty.
            topic.is_novel = False

    capped = topics[: settings.max_notes_per_source]
    return _dedupe_topics(capped)


def topic_location(topic: AtomicTopic) -> SourceLocation:
    return merge_locations([segment.location for segment in topic.segments])


def _minimum_topic_count(segment_count: int, structural_count: int) -> int:
    if segment_count <= 2:
        return 1
    return max(2, min(structural_count, max(segment_count // 3, 3)))


def _atomic_topic(title: str, segment: SourceSegment, body: str) -> AtomicTopic:
    cleaned = body.strip()
    grounded = refine_topic_title(cleaned, hint=title.strip() or None)
    return AtomicTopic(
        title=grounded,
        segments=[SourceSegment(text=cleaned, location=segment.location, index=segment.index)],
        summary=extract_topic_summary(
            [SourceSegment(text=cleaned, location=segment.location, index=segment.index)]
        ),
    )


def _atomic_line_limit() -> int:
    return min(settings.max_note_lines, settings.atomic_note_line_limit)


def _atomic_char_limit() -> int:
    return settings.atomic_note_char_limit


def _structural_plan_topics(segments: list[SourceSegment]) -> list[AtomicTopic]:
    topics: list[AtomicTopic] = []
    for segment in segments:
        topics.extend(_split_segment_into_atomic_topics(segment))
    return _dedupe_topics(_disambiguate_topic_batch(topics))


def _split_segment_into_atomic_topics(segment: SourceSegment) -> list[AtomicTopic]:
    topics: list[AtomicTopic] = []
    for topic in _split_segment_by_heading(segment):
        topics.extend(_split_oversized_topic(topic))
    return topics


def _split_oversized_topic(topic: AtomicTopic) -> list[AtomicTopic]:
    # Use the full segment text (never a truncated summary) so no content is
    # dropped before splitting -- otherwise large pages collapse into 1-2 notes.
    text = combine_segment_text(topic.segments)
    line_count = text.count("\n") + 1

    if line_count <= _atomic_line_limit() and len(text) <= _atomic_char_limit():
        return [topic]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) > 1:
        return _topics_from_paragraphs(topic.title, topic.segments[0], paragraphs)

    sentences = [part.strip() for part in SENTENCE_END_PATTERN.split(text) if part.strip()]
    if len(sentences) > 1:
        return _topics_from_sentence_groups(topic.title, topic.segments[0], sentences)

    midpoint = max(1, len(text) // 2)
    segment = topic.segments[0]
    return _disambiguate_topic_batch(
        [
            _atomic_topic(topic.title, segment, text[:midpoint]),
            _atomic_topic(topic.title, segment, text[midpoint:]),
        ]
    )


def _topics_from_paragraphs(
    base_title: str,
    segment: SourceSegment,
    paragraphs: list[str],
) -> list[AtomicTopic]:
    topics: list[AtomicTopic] = []
    chunk: list[str] = []
    chunk_lines = 0
    part = 1
    line_limit = _atomic_line_limit()

    def flush() -> None:
        nonlocal chunk, chunk_lines, part
        if not chunk:
            return
        body = "\n\n".join(chunk).strip()
        title = _title_for_split_part(base_title, body, part)
        topics.append(_atomic_topic(title, segment, body))
        part += 1
        chunk = []
        chunk_lines = 0

    for paragraph in paragraphs:
        paragraph_lines = paragraph.count("\n") + 1
        if chunk and chunk_lines + paragraph_lines > line_limit:
            flush()
        chunk.append(paragraph)
        chunk_lines += paragraph_lines

    flush()
    return _disambiguate_topic_batch(topics)


def _topics_from_sentence_groups(
    base_title: str,
    segment: SourceSegment,
    sentences: list[str],
) -> list[AtomicTopic]:
    topics: list[AtomicTopic] = []
    group: list[str] = []
    group_chars = 0
    part = 1
    char_limit = _atomic_char_limit()

    def flush() -> None:
        nonlocal group, group_chars, part
        if not group:
            return
        body = " ".join(group).strip()
        title = _title_for_split_part(base_title, body, part)
        topics.append(_atomic_topic(title, segment, body))
        part += 1
        group = []
        group_chars = 0

    for sentence in sentences:
        if group and group_chars + len(sentence) > char_limit:
            flush()
        group.append(sentence)
        group_chars += len(sentence) + 1

    flush()
    return _disambiguate_topic_batch(topics)


def _split_segment_by_heading(segment: SourceSegment) -> list[AtomicTopic]:
    matches = list(HEADING_PATTERN.finditer(segment.text))
    if not matches:
        return [_atomic_topic("", segment, segment.text)]

    topics: list[AtomicTopic] = []
    preamble = segment.text[: matches[0].start()].strip()
    if preamble:
        topics.append(_atomic_topic("", segment, preamble))

    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(segment.text)
        body = segment.text[start:end].strip()
        if body:
            topics.append(_atomic_topic(title, segment, body))

    return topics or [_atomic_topic("", segment, segment.text)]


def _dedupe_topics(topics: list[AtomicTopic]) -> list[AtomicTopic]:
    seen: set[str] = set()
    unique: list[AtomicTopic] = []
    for topic in topics:
        key = f"{topic.title.lower()}::{topic.segments[0].index if topic.segments else 0}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(topic)
    return unique


def _llm_plan_topics(
    source: LoadedSource,
    segments: list[SourceSegment],
    novelty: NoveltyResult,
    *,
    structural_count: int | None = None,
    budget: LLMBudget | None = None,
) -> list[AtomicTopic] | None:
    provider = get_llm_provider()
    if not provider:
        return None

    outline: list[dict] = []
    budget_remaining = TEXT_LIMITS.llm_planning_total_chars_budget
    for segment in segments[: TEXT_LIMITS.llm_planning_max_segments]:
        text = truncate_with_ellipsis(segment.text, TEXT_LIMITS.llm_planning_segment_chars)
        budget_remaining -= len(text)
        if budget_remaining < 0:
            # Too large to fit in one planning request; structural planning
            # (which covers the full source) will be used as the fallback.
            break
        outline.append(
            {
                "index": segment.index,
                "location": segment.location.display(),
                "text": text,
            }
        )

    if not outline:
        return None

    if structural_count is None:
        structural_count = len(_structural_plan_topics(segments))
    target_min = _minimum_topic_count(len(segments), structural_count)
    prompt = topic_planning_prompt(
        source=source,
        segment_outline=outline,
        target_min_notes=target_min,
        novelty=novelty,
    )
    prompt_chars = len(prompt) + len(ARCHITECT_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        # Do not mark the run exhausted: drafting can still use leftover budget
        # with smaller per-batch prompts.
        logger.info(
            "Skipping LLM topic planning: prompt needs %s chars but only %s remain",
            prompt_chars,
            budget.remaining_chars,
        )
        return None

    def planning_attempt() -> str:
        if budget is not None and not budget.can_call(prompt_chars):
            raise RuntimeError(
                "LLM planning prompt no longer fits the remaining input budget"
            )
        usage = None
        try:
            text, usage = complete_with_usage(
                provider,
                prompt,
                system=ARCHITECT_SYSTEM_PROMPT,
            )
            return text
        finally:
            if budget is not None:
                budget.record(prompt_chars, usage)

    try:
        raw = call_with_retry(planning_attempt)
        payload = _extract_json_array(raw)
        if not payload:
            return None

        by_index = {segment.index: segment for segment in segments}
        topics: list[AtomicTopic] = []
        for item in payload:
            title = str(item.get("title", "")).strip() or "Untitled concept"
            indices = item.get("segment_indices") or []
            matched_segments = [by_index[idx] for idx in indices if idx in by_index]
            if not matched_segments:
                continue
            llm_summary = str(item.get("summary", "")).strip()
            topics.append(
                AtomicTopic(
                    title=title,
                    segments=matched_segments,
                    summary=llm_summary or extract_topic_summary(matched_segments),
                )
            )
        return topics or None
    except Exception as exc:  # noqa: BLE001 - structural planning is the fallback
        logger.warning("LLM topic planning failed: %s", exc)
        return None


def _plan_windows(segments: list[SourceSegment]) -> list[list[SourceSegment]]:
    """Partition segments into contiguous windows that each fit one planning call.

    The single-call planner could only ever see a prefix of a large source (the
    outline was truncated to the char budget). Windowing lets the planner cover
    the whole source across multiple bounded calls instead.
    """
    max_segments = max(1, TEXT_LIMITS.llm_planning_max_segments)
    segment_chars = TEXT_LIMITS.llm_planning_segment_chars
    total_budget = TEXT_LIMITS.llm_planning_total_chars_budget

    windows: list[list[SourceSegment]] = []
    current: list[SourceSegment] = []
    current_chars = 0
    for segment in segments:
        seg_chars = min(len(segment.text), segment_chars)
        if current and (
            len(current) >= max_segments or current_chars + seg_chars > total_budget
        ):
            windows.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += seg_chars
    if current:
        windows.append(current)
    return windows


def _llm_plan_topics_windowed(
    source: LoadedSource,
    segments: list[SourceSegment],
    novelty: NoveltyResult,
    *,
    budget: LLMBudget | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> list[AtomicTopic]:
    """Plan topics across the whole source in bounded LLM windows (the map step).

    Every segment is covered by some window, subject to a per-run planning-call
    cap (``LLM_MAX_PLANNING_CALLS``) so planning cannot starve the drafting
    budget. Windows that are not planned (cap reached, budget too low, or the LLM
    failed) contribute nothing here and are filled structurally by the caller.
    """
    provider = get_llm_provider()
    if not provider:
        return []

    windows = _plan_windows(segments)
    max_calls = settings.llm_max_planning_calls
    topics: list[AtomicTopic] = []
    calls = 0
    window_total = len(windows)
    for window_index, window in enumerate(windows, start=1):
        if max_calls and calls >= max_calls:
            logger.info(
                "Planning call cap (%s) reached; remaining window(s) use structural planning",
                max_calls,
            )
            if progress:
                progress(
                    "planning",
                    window_index - 1,
                    window_total,
                    (
                        f"Planning call cap ({max_calls}) reached; "
                        "remaining sections use structural planning"
                    ),
                )
            break
        if budget is not None and budget.exhausted:
            break

        if progress:
            progress(
                "planning",
                window_index,
                window_total,
                (
                    f"LLM topic planning window {window_index}/{window_total} "
                    f"({len(window)} segments, call {calls + 1}"
                    + (f"/{max_calls}" if max_calls else "")
                    + ")"
                ),
            )
        logger.info(
            "LLM planning window %s/%s (%s segments)",
            window_index,
            window_total,
            len(window),
        )

        before = budget.calls if budget is not None else -1
        window_topics = _llm_plan_topics(source, window, novelty, budget=budget)
        made_call = budget is None or budget.calls > before
        if made_call:
            calls += 1
        if window_topics:
            topics.extend(window_topics)
        elif not made_call:
            # The planning prompt no longer fits the remaining budget; it never
            # will (budget only shrinks), so stop trying further windows.
            break
    return topics


def _order_topics(topics: list[AtomicTopic]) -> list[AtomicTopic]:
    """Order topics by their earliest source segment to preserve reading order."""
    return sorted(topics, key=lambda topic: min((s.index for s in topic.segments), default=0))


def _reconcile_topics(
    llm_topics: list[AtomicTopic],
    structural_topics: list[AtomicTopic],
    all_segments: list[SourceSegment],
) -> list[AtomicTopic]:
    """Combine LLM concept grouping with structural coverage (the reduce step).

    Segments the LLM never placed in a topic -- dropped from its response, or in
    a window that was never planned -- are filled from structural planning, so
    content is never lost. Falls back to pure structural planning when there is
    no LLM plan or it is degenerately under-segmented (few oversized topics).
    """
    if not llm_topics:
        return structural_topics

    covered = {segment.index for topic in llm_topics for segment in topic.segments}
    uncovered = [segment for segment in all_segments if segment.index not in covered]
    fill = _structural_plan_topics(uncovered) if uncovered else []
    combined = _order_topics(llm_topics + fill)

    min_topics = _minimum_topic_count(len(all_segments), len(structural_topics))
    if len(combined) < min_topics:
        # The LLM merged the source into too few large notes; structural planning
        # keeps notes atomic instead.
        return structural_topics
    return combined


