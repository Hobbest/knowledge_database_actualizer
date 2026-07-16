from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import settings
from app.llm import call_with_retry, get_llm_provider, is_rate_limit_error
from app.novelty import NoveltyResult
from app.prompts import ARCHITECT_SYSTEM_PROMPT, topic_planning_prompt
from app.sources.base import LoadedSource, SourceLocation, SourceSegment, merge_locations
from app.text_limits import TEXT_LIMITS
from app.text_utils import (
    combine_segment_text,
    extract_topic_summary,
    title_from_text,
    truncate_with_ellipsis,
)
from app.vectorstore import VectorStore

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
    """Score many segments with batched embedding + Chroma queries.

    On embedding rate-limit after retries, remaining segments are marked
    ``is_unknown`` (not novel) so planning does not invent novelty.
    """
    nonempty = [segment for segment in segments if segment.text.strip()]
    if not nonempty:
        return []

    if vector_store.chunk_count() == 0:
        return [
            SegmentNovelty(
                segment=segment,
                best_similarity=0.0,
                is_novel=True,
                is_unknown=False,
            )
            for segment in nonempty
        ]

    results: list[SegmentNovelty] = []
    batch_size = max(1, settings.embedding_query_batch_size)
    total = len(nonempty)

    for start in range(0, total, batch_size):
        batch = nonempty[start : start + batch_size]
        texts = [segment.text.strip() for segment in batch]
        try:
            matches_batch = vector_store.query_similar_many(
                texts, top_k=1, query_tags=source_tags
            )
        except Exception as exc:  # noqa: BLE001 - degrade remaining to unknown
            if not is_rate_limit_error(exc):
                raise
            for segment in nonempty[start:]:
                results.append(
                    SegmentNovelty(
                        segment=segment,
                        best_similarity=0.0,
                        is_novel=False,
                        is_unknown=True,
                    )
                )
            if on_batch is not None:
                on_batch(len(nonempty), total, rate_limited=True)
            return results

        for segment, matches in zip(batch, matches_batch, strict=True):
            best_similarity = matches[0].similarity if matches else 0.0
            results.append(
                SegmentNovelty(
                    segment=segment,
                    best_similarity=best_similarity,
                    is_novel=best_similarity < settings.novel_threshold,
                    is_unknown=False,
                )
            )
        if on_batch is not None:
            on_batch(min(start + len(batch), total), total, rate_limited=False)

    return results


def plan_atomic_topics(
    source: LoadedSource,
    segment_scores: list[SegmentNovelty],
    novelty: NoveltyResult,
    *,
    budget: LLMBudget | None = None,
) -> list[AtomicTopic]:
    """Plan one note per distinct concept across the full source."""
    all_segments = [item.segment for item in segment_scores] or source.segments
    if not all_segments:
        return []

    novelty_by_index = {item.segment.index: item for item in segment_scores}
    structural_topics = _structural_plan_topics(all_segments)

    llm_topics = _llm_plan_topics(source, all_segments, novelty, budget=budget)
    if llm_topics and len(llm_topics) >= _minimum_topic_count(len(all_segments), len(structural_topics)):
        topics = llm_topics
    else:
        topics = structural_topics

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
    return AtomicTopic(
        title=title.strip() or "Untitled concept",
        segments=[SourceSegment(text=cleaned, location=segment.location, index=segment.index)],
        summary=extract_topic_summary([SourceSegment(text=cleaned, location=segment.location, index=segment.index)]),
    )


def _atomic_line_limit() -> int:
    return min(settings.max_note_lines, settings.atomic_note_line_limit)


def _atomic_char_limit() -> int:
    return settings.atomic_note_char_limit


def _structural_plan_topics(segments: list[SourceSegment]) -> list[AtomicTopic]:
    topics: list[AtomicTopic] = []
    for segment in segments:
        topics.extend(_split_segment_into_atomic_topics(segment))
    return _dedupe_topics(topics)


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
    return [
        _atomic_topic(f"{topic.title} (part 1)", segment, text[:midpoint]),
        _atomic_topic(f"{topic.title} (part 2)", segment, text[midpoint:]),
    ]


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
        title = base_title if part == 1 and not topics else f"{base_title} ({part})"
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
    return topics


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
        title = base_title if part == 1 and not topics else f"{base_title} ({part})"
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
    return topics


def _split_segment_by_heading(segment: SourceSegment) -> list[AtomicTopic]:
    matches = list(HEADING_PATTERN.finditer(segment.text))
    if not matches:
        return [_atomic_topic(title_from_text(segment.text), segment, segment.text)]

    topics: list[AtomicTopic] = []
    preamble = segment.text[: matches[0].start()].strip()
    if preamble:
        topics.append(_atomic_topic(title_from_text(preamble), segment, preamble))

    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(segment.text)
        body = segment.text[start:end].strip()
        if body:
            topics.append(_atomic_topic(title, segment, body))

    return topics or [_atomic_topic(title_from_text(segment.text), segment, segment.text)]


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

    target_min = _minimum_topic_count(len(segments), len(_structural_plan_topics(segments)))
    prompt = topic_planning_prompt(
        source=source,
        segment_outline=outline,
        target_min_notes=target_min,
        novelty=novelty,
    )
    prompt_chars = len(prompt) + len(ARCHITECT_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        budget.refuse(prompt_chars)
        logger.info("Skipping LLM topic planning: %s", budget.exhausted_reason)
        return None

    try:
        raw = call_with_retry(lambda: provider.complete(prompt, system=ARCHITECT_SYSTEM_PROMPT))
        if budget is not None:
            budget.record(prompt_chars)
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


def _extract_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []

    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
