from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Generator
from queue import SimpleQueue
from typing import cast

from app.atomic_notes import (
    AtomicTopic,
    SegmentNovelty,
    plan_atomic_topics,
    score_segments,
)
from app.config import settings
from app.llm_budget import LLMBudget
from app.novelty import NoveltyResult
from app.relevance import filter_relevant_segments, is_boilerplate_title, is_low_value_text
from app.segmentation import split_large_segments
from app.source_identity import normalize_source_key
from app.sources.base import LoadedSource, SourceSegment
from app.suggest.models import ProgressFn
from app.text_utils import combine_segment_text, extract_topic_summary
from app.titling import TITLE_ALGORITHM_VERSION
from app.vector_protocol import VectorStoreProtocol as VectorStore

logger = logging.getLogger(__name__)

_PLAN_DONE = object()


def analysis_fingerprint(source: LoadedSource) -> str:
    shaping_settings = {
        "segment_target_chars": settings.segment_target_chars,
        "filter_boilerplate": settings.filter_boilerplate,
        "novel_threshold": settings.novel_threshold,
        "known_threshold": settings.known_threshold,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "tag_similarity_enabled": settings.tag_similarity_enabled,
        "tag_similarity_boost_per_tag": settings.tag_similarity_boost_per_tag,
        "tag_similarity_max_boost": settings.tag_similarity_max_boost,
        "max_notes_per_source": settings.max_notes_per_source,
        "atomic_note_line_limit": settings.atomic_note_line_limit,
        "atomic_note_char_limit": settings.atomic_note_char_limit,
        # Invalidate reused checkpoint plans when body-grounded titling changes.
        "title_algorithm_version": TITLE_ALGORITHM_VERSION,
    }
    payload = json.dumps(shaping_settings, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update(source.text.encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


def topics_to_checkpoint(topics: list[AtomicTopic]) -> list[dict]:
    return [
        {
            "title": topic.title,
            "segment_indices": [segment.index for segment in topic.segments],
            "summary": topic.summary,
            "is_novel": topic.is_novel,
        }
        for topic in topics
    ]


def topics_from_checkpoint(
    payload: list[dict],
    planning_segments: list[SourceSegment],
) -> list[AtomicTopic]:
    by_index = {segment.index: segment for segment in planning_segments}
    topics: list[AtomicTopic] = []
    for item in payload:
        indices = item.get("segment_indices") or []
        segments = [by_index[index] for index in indices if index in by_index]
        if not segments or len(segments) != len(indices):
            return []
        topics.append(
            AtomicTopic(
                title=str(item.get("title", "")).strip() or "Untitled concept",
                segments=segments,
                summary=str(item.get("summary", "")),
                is_novel=bool(item.get("is_novel", True)),
            )
        )
    return topics


def _plan_topics(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None,
    *,
    progress: ProgressFn | None = None,
    warnings: list[str] | None = None,
    budget: LLMBudget | None = None,
    planning_segments: list[SourceSegment] | None = None,
    segment_scores: list[SegmentNovelty] | None = None,
) -> list[AtomicTopic]:
    # Break large source units (e.g. full PDF pages) into bounded planning units
    # so multi-topic pages are not collapsed into a single note.
    if planning_segments is None:
        base_segments = source.segments
        if settings.filter_boilerplate:
            base_segments = filter_relevant_segments(base_segments)
        planning_segments = split_large_segments(
            base_segments,
            target_chars=settings.segment_target_chars,
        )

    indexed = vector_store is not None and vector_store.chunk_count() > 0
    total = len(planning_segments)
    warning_sink = warnings if warnings is not None else []

    def on_batch(current: int, batch_total: int, *, rate_limited: bool = False) -> None:
        if progress:
            message = (
                "Similarity scoring rate-limited; marking remaining as unknown"
                if rate_limited
                else "Scanning source for distinct concepts"
            )
            progress("scoring", current, batch_total, message)
        if rate_limited:
            msg = (
                "Embedding rate limit during similarity scoring; unscored segments "
                "were marked unknown (not novel) so the run can continue."
            )
            if msg not in warning_sink:
                warning_sink.append(msg)
            logger.warning(msg)

    if segment_scores is not None:
        if progress and total:
            progress("scoring", total, total, "Reusing precomputed similarity scores")
    elif indexed and vector_store is not None:
        segment_scores = score_segments(
            planning_segments,
            vector_store,
            source_tags=list(source.tags) if source.tags else None,
            on_batch=on_batch,
        )
        unknown = sum(1 for item in segment_scores if item.is_unknown)
        if unknown and progress:
            progress(
                "scoring",
                total,
                total,
                f"Scored with {unknown} unknown segment(s)",
            )
    else:
        segment_scores = [
            SegmentNovelty(
                segment=segment,
                best_similarity=0.0,
                is_novel=True,
                is_unknown=False,
            )
            for segment in planning_segments
            if segment.text.strip()
        ]
        if progress and total:
            progress("scoring", total, total, "No index — treating source segments as novel")

    topics = plan_atomic_topics(
        source,
        segment_scores,
        novelty,
        budget=budget,
        progress=progress,
    )

    if settings.filter_boilerplate:
        # Drop topics whose title is boilerplate or whose body is a link/reference
        # dump -- planning can split a page so a reference block becomes its own
        # topic that segment-level filtering never saw.
        filtered = [
            topic
            for topic in topics
            if not is_boilerplate_title(topic.title)
            and not is_low_value_text(combine_segment_text(topic.segments))
        ]
        topics = filtered or topics

    if not topics:
        fallback_segments = source.segments[:1]
        topics = [
            AtomicTopic(
                title=source.title,
                segments=fallback_segments,
                summary=extract_topic_summary(fallback_segments),
            )
        ]

    return topics


def _source_meta(source: LoadedSource) -> dict:
    return {
        "title": source.title,
        "source_type": source.source_type,
        "source_ref": source.source_ref,
        "source_key": source.source_key
        or normalize_source_key(source.source_type, source.source_ref),
    }


def _stream_plan_topics(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None,
    *,
    warnings: list[str],
    budget: LLMBudget,
    planning_segments: list[SourceSegment] | None = None,
    segment_scores: list[SegmentNovelty] | None = None,
) -> Generator[dict, None, list[AtomicTopic]]:
    """Run topic planning while yielding progress events as they happen.

    ``_plan_topics`` is blocking; without a side channel the UI would only see
    scoring updates after the whole planning phase finished.
    """
    events: SimpleQueue = SimpleQueue()
    result: dict[str, object] = {}

    def progress(stage: str, current: int, total: int, message: str) -> None:
        events.put(
            {
                "type": "progress",
                "stage": stage,
                "current": current,
                "total": total,
                "message": message,
            }
        )

    def worker() -> None:
        try:
            result["topics"] = _plan_topics(
                source,
                novelty,
                vector_store,
                progress=progress,
                warnings=warnings,
                budget=budget,
                planning_segments=planning_segments,
                segment_scores=segment_scores,
            )
            for message in budget.warnings:
                if message not in warnings:
                    warnings.append(message)
                    events.put({"type": "warning", "message": message})
        except Exception as exc:  # noqa: BLE001 - re-raised after the stream drains
            result["error"] = exc
        finally:
            events.put(_PLAN_DONE)

    thread = threading.Thread(target=worker, name="plan-topics", daemon=True)
    thread.start()
    while True:
        item = events.get()
        if item is _PLAN_DONE:
            break
        yield item
    thread.join()
    if "error" in result:
        raise cast(BaseException, result["error"])
    return cast(list[AtomicTopic], result["topics"])

