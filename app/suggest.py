from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Generator, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import SimpleQueue
from typing import cast

import yaml

from app.atomic_notes import (
    AtomicTopic,
    SegmentNovelty,
    _extract_json_array,
    plan_atomic_topics,
    score_segments,
    topic_location,
)
from app.block_refs import inject_block_references
from app.checkpoint import SuggestionCheckpoint
from app.config import settings
from app.llm import call_with_retry, get_llm_provider, is_rate_limit_error
from app.llm_budget import LLMBudget, estimate_run_cost_hint
from app.media import media_for_location, render_media_section
from app.note_output import (
    apply_note_template,
    default_note_path,
    merge_append_into_note,
    moc_note_path,
    normalize_vault_relative_path,
    parse_append_target,
    topic_overlap_match,
    vault_relative_paths_equal,
)
from app.novelty import NoveltyResult, OverlappingNote
from app.prompts import NOTE_WRITER_SYSTEM_PROMPT, batch_note_draft_prompt, note_draft_prompt
from app.relevance import filter_relevant_segments, is_boilerplate_title, is_low_value_text
from app.segmentation import split_large_segments
from app.source_identity import normalize_source_key
from app.sources.base import LoadedSource, SourceLocation
from app.summarize import compose_title, key_points, refine_note_body, summarize_text
from app.text_limits import TEXT_LIMITS
from app.text_utils import combine_segment_text, extract_topic_summary
from app.vectorstore import VectorStore
from app.wikilinks import format_wikilink

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int, str], None]
_PLAN_DONE = object()


@dataclass
class NoteSuggestion:
    concept_title: str
    note_path: str
    content: str
    location: dict
    segment_indices: list[int]
    write_mode: str = "write"
    append_target: str | None = None
    append_heading: str | None = None
    overlap_similarity: float | None = None
    is_moc: bool = False

    def to_dict(self) -> dict:
        return {
            "concept_title": self.concept_title,
            "note_path": self.note_path,
            "content": self.content,
            "location": self.location,
            "segment_indices": self.segment_indices,
            "write_mode": self.write_mode,
            "append_target": self.append_target,
            "append_heading": self.append_heading,
            "overlap_similarity": self.overlap_similarity,
            "is_moc": self.is_moc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NoteSuggestion:
        return cls(
            concept_title=data.get("concept_title", ""),
            note_path=data.get("note_path", ""),
            content=data.get("content", ""),
            location=data.get("location") or {},
            segment_indices=list(data.get("segment_indices") or []),
            write_mode=data.get("write_mode", "write"),
            append_target=data.get("append_target"),
            append_heading=data.get("append_heading"),
            overlap_similarity=data.get("overlap_similarity"),
            is_moc=bool(data.get("is_moc")),
        )


def _note_identity(segment_indices: list[int], concept_title: str) -> tuple:
    """Stable key for matching a planned topic to an already-drafted note.

    Uses the source segments the note covers plus its (composed) title, so the
    same concept is recognized across runs regardless of note-path de-duplication.
    """
    indices = tuple(sorted(int(i) for i in segment_indices))
    return (indices, concept_title.strip().lower())


def _related_links(overlapping_notes: list[OverlappingNote]) -> list[str]:
    links: list[str] = []
    for note in overlapping_notes[: TEXT_LIMITS.related_link_count]:
        if not note.note_path:
            continue
        link = format_wikilink(note.note_path)
        if link and link not in links:
            links.append(link)
    return links


def _infer_note_tags(overlapping_notes: list[OverlappingNote]) -> list[str]:
    tags: list[str] = list(settings.default_note_tags_list)
    for note in overlapping_notes[: TEXT_LIMITS.related_link_count]:
        tags.extend(note.tags)
    return list(dict.fromkeys(tag for tag in tags if tag))


def _build_moc_suggestion(
    source: LoadedSource,
    suggestions: list[NoteSuggestion],
    *,
    vault_path: Path | None = None,
) -> NoteSuggestion:
    concept_notes = [item for item in suggestions if not item.is_moc]
    links = [format_wikilink(item.note_path) for item in concept_notes if item.note_path]
    links = list(dict.fromkeys(links))

    meta = {
        "type": "moc",
        "status": settings.note_frontmatter_status or "draft",
        "source_type": source.source_type,
        "source_ref": source.source_ref,
        "source": source.source_ref,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": list(dict.fromkeys([*settings.default_note_tags_list, "moc"])),
    }
    dumped = yaml.safe_dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    body_lines = [
        f"# {source.title} — notes",
        "",
        f"Index of {len(concept_notes)} note(s) imported from this source.",
        "",
        "## Notes",
        "",
        *[f"- {link}" for link in links],
        "",
    ]
    content = f"---\n{dumped.strip()}\n---\n" + "\n".join(body_lines)
    content = apply_note_template(
        content,
        vault_path=vault_path,
        title=f"{source.title} — notes",
        concept="MOC",
        tags=list(meta["tags"]),
        source=source,
        location=SourceLocation(),
    )

    return NoteSuggestion(
        concept_title=f"{source.title} — index",
        note_path=moc_note_path(source),
        content=content,
        location={},
        segment_indices=[],
        is_moc=True,
    )


def _build_frontmatter(
    source: LoadedSource,
    concept_title: str,
    location: SourceLocation,
    *,
    tags: list[str] | None = None,
) -> str:
    """Build Obsidian-compatible YAML frontmatter with safe quoting."""
    meta: dict = {
        "concept": concept_title,
        "source_type": source.source_type,
        "source_ref": source.source_ref,
        "source": source.source_ref,
        "source_location": location.display(),
        "created": datetime.now(timezone.utc).isoformat(),
    }

    if settings.note_frontmatter_type:
        meta["type"] = settings.note_frontmatter_type
    if settings.note_frontmatter_status:
        meta["status"] = settings.note_frontmatter_status

    note_tags = tags or []
    if note_tags:
        meta["tags"] = note_tags

    if location.page is not None:
        meta["source_page"] = location.page
        if location.page_end and location.page_end != location.page:
            meta["source_page_end"] = location.page_end
    if location.line_start is not None:
        meta["source_line_start"] = location.line_start
        meta["source_line_end"] = location.line_end or location.line_start
    if location.timestamp_start is not None:
        meta["source_timestamp_start"] = round(location.timestamp_start, 3)
        end = location.timestamp_end or location.timestamp_start
        meta["source_timestamp_end"] = round(end, 3)

    dumped = yaml.safe_dump(
        meta,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{dumped}\n---\n"


def _source_section(source: LoadedSource, location: SourceLocation) -> str:
    location_text = location.display()
    if source.source_type == "youtube":
        timestamp = location.timestamp_start
        if timestamp is not None:
            seconds = int(timestamp)
            return (
                "## Source\n\n"
                f"- Video: {source.source_ref}\n"
                f"- Timestamp: {location_text} (`t={seconds}`)\n"
            )
    if source.source_type == "pdf":
        return (
            "## Source\n\n"
            f"- Document: `{source.source_ref}`\n"
            f"- Location: {location_text}\n"
        )
    if source.source_type == "web":
        return (
            "## Source\n\n"
            f"- Article: {source.source_ref}\n"
            f"- Location: {location_text} (in extracted article text)\n"
        )
    if source.source_type == "epub":
        return (
            "## Source\n\n"
            f"- Book: `{source.source_ref}`\n"
            f"- Location: {location_text}\n"
        )

    return (
        "## Source\n\n"
        f"- File: `{source.source_ref}`\n"
        f"- Location: {location_text}\n"
    )


def _llm_draft_topic_body(
    source: LoadedSource,
    topic: AtomicTopic,
    related_links: list[str],
    *,
    budget: LLMBudget | None = None,
) -> str | None:
    provider = get_llm_provider()
    if not provider:
        return None

    location = topic_location(topic)
    excerpt = extract_topic_summary(
        topic.segments,
        max_chars=TEXT_LIMITS.note_draft_excerpt_chars,
    )
    prompt = note_draft_prompt(
        source=source,
        concept_title=topic.title,
        location_display=location.display(),
        excerpt=excerpt,
        related_links=related_links,
        max_note_lines=settings.max_note_lines,
    )
    prompt_chars = len(prompt) + len(NOTE_WRITER_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        raise RuntimeError(budget.refuse(prompt_chars))

    text = provider.complete(prompt, system=NOTE_WRITER_SYSTEM_PROMPT).strip() or None
    if budget is not None and text is not None:
        budget.record(prompt_chars)
    return text


def _llm_draft_topics_batch(
    source: LoadedSource,
    topics: list[AtomicTopic],
    related_links: list[str],
    *,
    budget: LLMBudget | None = None,
) -> dict[str, str]:
    """Draft several topics in one LLM call; returns title -> body."""
    provider = get_llm_provider()
    if not provider or not topics:
        return {}

    payload: list[dict] = []
    for topic in topics:
        location = topic_location(topic)
        excerpt = extract_topic_summary(
            topic.segments,
            max_chars=TEXT_LIMITS.note_draft_excerpt_chars,
        )
        payload.append(
            {
                "title": compose_title(topic.title),
                "location": location.display(),
                "excerpt": excerpt,
            }
        )

    prompt = batch_note_draft_prompt(
        source=source,
        topics=payload,
        related_links=related_links,
        max_note_lines=settings.max_note_lines,
    )
    prompt_chars = len(prompt) + len(NOTE_WRITER_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        raise RuntimeError(budget.refuse(prompt_chars))

    raw = provider.complete(prompt, system=NOTE_WRITER_SYSTEM_PROMPT).strip()
    if budget is not None and raw:
        budget.record(prompt_chars)

    bodies: dict[str, str] = {}
    for item in _extract_json_array(raw or ""):
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if title and body:
            bodies[title.casefold()] = body
    return bodies


def _fallback_topic_body(topic: AtomicTopic) -> str:
    # Summarize from the full topic text (not the pre-truncated topic.summary) so
    # the extractive scorer sees every sentence before picking the salient ones.
    full_text = combine_segment_text(topic.segments) or topic.summary or ""
    title = compose_title(topic.title)

    summary = summarize_text(
        full_text,
        max_sentences=TEXT_LIMITS.summary_sentence_count,
        max_chars=TEXT_LIMITS.summary_max_chars,
    )

    # Key points must add information beyond the summary, not restate it.
    points = key_points(
        full_text,
        max_points=TEXT_LIMITS.fallback_bullet_count,
        min_chars=TEXT_LIMITS.key_point_min_chars,
        exclude=summary,
    )

    sections = [f"# {title}", "", "## Summary", "", summary or title]
    if points:
        sections += ["", "## Key points", ""]
        sections += [f"- {point}" for point in points]

    return refine_note_body("\n".join(sections))


def draft_note_suggestion(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None = None,
) -> list[NoteSuggestion]:
    return draft_note_suggestions(source, novelty, vector_store)[:1]


def _plan_topics(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None,
    *,
    progress: ProgressFn | None = None,
    warnings: list[str] | None = None,
    budget: LLMBudget | None = None,
) -> list[AtomicTopic]:
    # Break large source units (e.g. full PDF pages) into bounded planning units
    # so multi-topic pages are not collapsed into a single note.
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

    if indexed and vector_store is not None:
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

    topics = plan_atomic_topics(source, segment_scores, novelty, budget=budget)

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


def _apply_analyze_in_place(
    suggestion: NoteSuggestion,
    vault_note_path: str | None,
) -> None:
    """Prefer appending to the analyzed vault note when overlap targets it."""
    if not vault_note_path or not settings.analyze_in_place_enabled:
        return
    rel = normalize_vault_relative_path(vault_note_path)
    if not rel:
        return
    if suggestion.append_target and vault_relative_paths_equal(suggestion.append_target, rel):
        suggestion.write_mode = "append"
        suggestion.note_path = rel


def _build_suggestion(
    source: LoadedSource,
    topic: AtomicTopic,
    related_links: list[str],
    used_paths: set[str],
    *,
    overlapping_notes: list[OverlappingNote] | None = None,
    use_llm: bool = True,
    budget: LLMBudget | None = None,
    vector_store: VectorStore | None = None,
    vault_note_path: str | None = None,
    vault_path: Path | None = None,
    pre_drafted_body: str | None = None,
) -> NoteSuggestion:
    location = topic_location(topic)
    title = compose_title(topic.title)

    body = pre_drafted_body
    if body is None and use_llm:
        # May raise (e.g. rate limit / budget); the caller decides how to recover.
        body = _llm_draft_topic_body(source, topic, related_links, budget=budget)
    body = body or _fallback_topic_body(topic)
    body = _strip_source_section(body)

    # Single refinement pass gives LLM and extractive notes the same consistent
    # structure (heading spacing, deduped bullets, no ragged blank lines).
    body = refine_note_body(body)

    # Attach any tables/figures that live on this note's page or line range.
    if settings.include_media and source.media and "## Tables & figures" not in body:
        media_items = media_for_location(location, source.media)
        media_section = render_media_section(media_items)
        if media_section:
            body = body.rstrip() + "\n\n" + media_section

    related_section = "\n".join(f"- {link}" for link in related_links) if related_links else "- none"
    if "## Related notes" not in body:
        body = body.rstrip() + f"\n\n## Related notes\n\n{related_section}\n"

    body = body.rstrip() + "\n\n" + _source_section(source, location)
    note_tags = _infer_note_tags(overlapping_notes or [])
    content = _build_frontmatter(source, title, location, tags=note_tags) + body
    content = apply_note_template(
        content,
        vault_path=vault_path,
        title=title,
        concept=title,
        tags=note_tags,
        source=source,
        location=location,
    )

    note_path = default_note_path(source, title)
    append_target: str | None = None
    append_heading: str | None = None
    overlap_similarity: float | None = None
    match = topic_overlap_match(
        vector_store,
        topic,
        query_tags=list(source.tags) if source.tags else None,
    )
    if match:
        append_target, overlap_similarity, overlap_heading = match
        if settings.append_under_overlap_heading and overlap_heading:
            append_heading = overlap_heading
    target_path, target_heading = parse_append_target(append_target)
    if target_path:
        append_target = target_path
    if target_heading and not append_heading:
        append_heading = target_heading
    if note_path in used_paths:
        suffix = 2
        while f"{note_path[:-3]}-{suffix}.md" in used_paths:
            suffix += 1
        note_path = f"{note_path[:-3]}-{suffix}.md"
    used_paths.add(note_path)

    suggestion = NoteSuggestion(
        concept_title=title,
        note_path=note_path,
        content=content,
        location=location.to_dict(),
        segment_indices=[segment.index for segment in topic.segments],
        write_mode="write",
        append_target=append_target,
        append_heading=append_heading,
        overlap_similarity=overlap_similarity,
    )
    _apply_analyze_in_place(suggestion, vault_note_path)
    return suggestion


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


def iter_note_suggestions(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None = None,
    *,
    checkpoint: SuggestionCheckpoint | None = None,
    resume_suggestions: list[dict] | None = None,
    vault_note_path: str | None = None,
    vault_path: Path | None = None,
) -> Iterator[dict]:
    """Yield progress events while drafting notes; end with a 'suggestions' event.

    Every drafted note is persisted to ``checkpoint`` (if provided) as soon as it
    is created, so a mid-run failure -- for example an LLM rate limit -- never
    discards the notes already produced. On a rate limit the remaining notes are
    completed with extractive fallbacks instead of aborting.

    When ``resume_suggestions`` (notes recovered from a prior interrupted run) are
    supplied, topics that already have a matching note are reused as-is and only
    the still-missing notes are drafted -- so continuing a run never re-generates
    work that was already done.

    Progress events look like {"type": "progress", "stage": ..., "current": ...,
    "total": ..., "message": ...}. Non-fatal issues emit {"type": "warning",
    "message": ...}. The final event is {"type": "suggestions",
    "suggestions": [NoteSuggestion, ...], "warnings": [...]}.
    """
    related_links = _related_links(novelty.overlapping_notes)
    resume_suggestions = resume_suggestions or []
    done_by_identity = {
        _note_identity(item.get("segment_indices") or [], item.get("concept_title", "")): item
        for item in resume_suggestions
    }

    warnings: list[str] = []
    budget = LLMBudget.from_settings()
    topics = yield from _stream_plan_topics(
        source,
        novelty,
        vector_store,
        warnings=warnings,
        budget=budget,
    )

    if settings.llm_enabled and topics:
        yield {
            "type": "progress",
            "stage": "drafting",
            "current": 0,
            "total": len(topics),
            "message": estimate_run_cost_hint(
                note_count=len(topics),
                planning=budget.calls > 0,
            ),
        }

    if checkpoint is not None:
        if resume_suggestions:
            checkpoint.resume(_source_meta(source), resume_suggestions)
        else:
            checkpoint.start(_source_meta(source))

    suggestions: list[NoteSuggestion] = []
    # Reserve every recovered note's path so freshly drafted notes never collide
    # with (and overwrite) a note we are about to preserve.
    used_paths: set[str] = {
        item.get("note_path", "") for item in resume_suggestions if item.get("note_path")
    }
    matched_identities: set[tuple] = set()
    total = len(topics)
    llm_disabled = budget.exhausted
    consecutive_failures = 0

    def record_warning(message: str) -> dict:
        warnings.append(message)
        if checkpoint is not None:
            checkpoint.add_warning(message)
        return {"type": "warning", "message": message}

    batch_bodies: dict[tuple, str] = {}
    batch_size = max(1, settings.llm_draft_batch_size)
    if batch_size > 1 and not llm_disabled and get_llm_provider():
        pending: list[tuple[tuple, AtomicTopic]] = []
        for topic in topics:
            identity = _note_identity(
                [segment.index for segment in topic.segments], compose_title(topic.title)
            )
            if identity in done_by_identity:
                continue
            pending.append((identity, topic))

        for batch_index, start in enumerate(range(0, len(pending), batch_size), start=1):
            batch = pending[start : start + batch_size]
            batch_topics = [item[1] for item in batch]
            yield {
                "type": "progress",
                "stage": "drafting",
                "current": min(start + len(batch), total),
                "total": total,
                "message": (
                    f"Batch drafting notes {start + 1}-{start + len(batch)} "
                    f"({batch_index}/{(len(pending) + batch_size - 1) // batch_size})"
                ),
            }
            try:
                def draft_batch() -> dict[str, str]:
                    return _llm_draft_topics_batch(
                        source,
                        batch_topics,
                        related_links,
                        budget=budget,
                    )

                bodies = call_with_retry(draft_batch)
                for (identity, topic) in batch:
                    title = compose_title(topic.title)
                    body = bodies.get(title.casefold())
                    if body:
                        batch_bodies[identity] = body
            except Exception as exc:  # noqa: BLE001
                if budget.exhausted:
                    llm_disabled = True
                    yield record_warning(budget.exhausted_reason or str(exc))
                else:
                    yield record_warning(
                        f"Batch draft failed for notes {start + 1}-{start + len(batch)}; "
                        f"falling back to per-note drafting ({exc})."
                    )

    for position, topic in enumerate(topics, start=1):
        identity = _note_identity(
            [segment.index for segment in topic.segments], compose_title(topic.title)
        )
        # Reuse a note that was already drafted in the interrupted run.
        already_done = done_by_identity.get(identity)
        if already_done is not None:
            matched_identities.add(identity)
            used_paths.add(already_done.get("note_path", ""))
            suggestions.append(NoteSuggestion.from_dict(already_done))
            yield {
                "type": "progress",
                "stage": "drafting",
                "current": position,
                "total": total,
                "message": f"Reusing recovered note {position}/{total}: {topic.title}",
            }
            continue

        yield {
            "type": "progress",
            "stage": "drafting",
            "current": position,
            "total": total,
            "message": f"Drafting note {position}/{total}: {topic.title}",
        }

        suggestion: NoteSuggestion | None = None
        draft_events: SimpleQueue = SimpleQueue()
        _DRAFT_DONE = object()
        draft_result: dict[str, object] = {}

        def on_rate_limit_wait(attempt: int, delay: float, _exc: BaseException) -> None:
            draft_events.put(
                {
                    "type": "progress",
                    "stage": "drafting",
                    "current": position,
                    "total": total,
                    "message": (
                        f"Rate limited — waiting {int(delay)}s before retrying note "
                        f"{position}/{total} (attempt {attempt}/{settings.llm_max_retries})"
                    ),
                }
            )

        pre_body = batch_bodies.get(identity)

        if not llm_disabled:
            def draft_worker() -> None:
                try:
                    draft_result["suggestion"] = call_with_retry(
                        lambda: _build_suggestion(
                            source,
                            topic,
                            related_links,
                            used_paths,
                            overlapping_notes=novelty.overlapping_notes,
                            use_llm=True,
                            budget=budget,
                            vector_store=vector_store,
                            vault_note_path=vault_note_path,
                            vault_path=vault_path,
                            pre_drafted_body=pre_body,
                        ),
                        on_wait=on_rate_limit_wait,
                    )
                except Exception as exc:  # noqa: BLE001 - handled by the consumer
                    draft_result["error"] = exc
                finally:
                    draft_events.put(_DRAFT_DONE)

            draft_thread = threading.Thread(
                target=draft_worker, name=f"draft-note-{position}", daemon=True
            )
            draft_thread.start()
            while True:
                item = draft_events.get()
                if item is _DRAFT_DONE:
                    break
                yield item
            draft_thread.join()

            if "suggestion" in draft_result:
                suggestion = cast(NoteSuggestion, draft_result["suggestion"])
                consecutive_failures = 0
            elif "error" in draft_result:
                stored_error = cast(BaseException, draft_result["error"])
                if budget.exhausted:
                    llm_disabled = True
                    yield record_warning(budget.exhausted_reason or str(stored_error))
                elif not is_rate_limit_error(stored_error):
                    logger.warning(
                        "LLM drafting failed for '%s'; using fallback: %s",
                        topic.title,
                        stored_error,
                    )
                    yield record_warning(
                        f"Note '{topic.title}' used an extractive fallback after an LLM error."
                    )
                else:
                    consecutive_failures += 1
                    yield record_warning(
                        f"Note {position}/{total} could not be drafted after "
                        f"{settings.llm_max_retries} retries; using an extractive summary."
                    )
                    if consecutive_failures >= settings.llm_disable_after_failures:
                        llm_disabled = True
                        yield record_warning(
                            "Repeated rate limits; remaining notes use extractive summaries. "
                            "Re-run later to regenerate them with the LLM."
                        )

        if suggestion is None:
            suggestion = _build_suggestion(
                source,
                topic,
                related_links,
                used_paths,
                overlapping_notes=novelty.overlapping_notes,
                use_llm=False,
                budget=budget,
                vector_store=vector_store,
                vault_note_path=vault_note_path,
                vault_path=vault_path,
                pre_drafted_body=pre_body,
            )

        suggestions.append(suggestion)
        if checkpoint is not None:
            checkpoint.add(suggestion.to_dict())

    # Preserve any recovered notes that did not map to a re-planned topic (e.g.
    # if planning produced slightly different topics) so resuming never loses
    # work. These are already in the checkpoint from resume(); just surface them.
    seen_identities = set(matched_identities)
    for item in resume_suggestions:
        identity = _note_identity(item.get("segment_indices") or [], item.get("concept_title", ""))
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        suggestions.append(NoteSuggestion.from_dict(item))

    if (
        settings.generate_moc
        and len([s for s in suggestions if not s.is_moc]) >= settings.moc_min_notes
        and not any(item.is_moc for item in suggestions)
    ):
        moc = _build_moc_suggestion(source, suggestions, vault_path=vault_path)
        if moc.note_path not in used_paths:
            suggestions.append(moc)
            if checkpoint is not None:
                checkpoint.add(moc.to_dict())
            yield {
                "type": "progress",
                "stage": "drafting",
                "current": len(suggestions),
                "total": len(suggestions),
                "message": f"Added map-of-content index: {moc.note_path}",
            }

    if checkpoint is not None:
        checkpoint.finish(completed=True)

    if budget.calls:
        warnings.append(
            f"LLM usage this run: {budget.calls} call(s), "
            f"~{budget.input_chars // 4:,} input tokens "
            f"({budget.input_chars:,} chars)."
        )

    yield {
        "type": "suggestions",
        "suggestions": suggestions,
        "warnings": warnings,
        "llm_budget": budget.summary(),
    }


def draft_note_suggestions(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None = None,
    *,
    checkpoint: SuggestionCheckpoint | None = None,
) -> list[NoteSuggestion]:
    suggestions: list[NoteSuggestion] = []
    for event in iter_note_suggestions(source, novelty, vector_store, checkpoint=checkpoint):
        if event.get("type") == "suggestions":
            suggestions = event["suggestions"]
    return suggestions


def _strip_source_section(body: str) -> str:
    return re.sub(r"\n## Source\b[\s\S]*$", "", body.strip(), flags=re.IGNORECASE).strip()


@dataclass
class ApplyNoteResult:
    """Per-note outcome from writing a suggestion into the vault."""

    note_path: str
    status: str  # written | appended | skipped_exists | error
    written_path: str | None = None
    error: str | None = None
    overwritten: bool = False
    backup_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MergedNotePreview:
    note_path: str
    mode: str
    exists: bool
    will_write: bool
    existing_content: str
    final_content: str

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_vault_target(vault_path: Path, note_path: str) -> Path:
    vault_path = vault_path.resolve()
    target = (vault_path / note_path).resolve()
    if not target.is_relative_to(vault_path):
        raise ValueError("Refusing to write outside the configured vault path")
    return target


def _backup_existing_note(target: Path, vault_path: Path) -> str:
    """Copy an existing note to ``*.md.bak`` before overwrite; return vault-relative path."""
    backup = target.with_name(target.name + ".bak")
    shutil.copy2(target, backup)
    return backup.relative_to(vault_path.resolve()).as_posix()


def _atomic_write_text(path: Path, content: str) -> None:
    """Write UTF-8 text via temp file + rename so crashes cannot truncate notes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def preview_suggestion_merge(
    vault_path: Path,
    note_path: str,
    content: str,
    mode: str = "write",
    *,
    overwrite: bool = False,
    append_heading: str | None = None,
) -> MergedNotePreview:
    """Return the exact bytes-as-text that apply would write, without mutating the vault."""
    if mode not in {"write", "append"}:
        raise ValueError("mode must be 'write' or 'append'")
    target = _resolve_vault_target(vault_path, note_path)
    rel = target.relative_to(vault_path.resolve()).as_posix()
    exists = target.is_file()
    existing = target.read_text(encoding="utf-8") if exists else ""
    prepared = inject_block_references(content)
    if mode == "append" and exists:
        final = merge_append_into_note(
            existing,
            prepared,
            target_heading=append_heading,
            fallback_heading="Update",
        )
    elif mode == "append":
        final = inject_block_references(content.strip()) + "\n"
    else:
        final = existing if exists and not overwrite else prepared
    will_write = mode == "append" or not exists or overwrite
    return MergedNotePreview(
        note_path=rel,
        mode=mode,
        exists=exists,
        will_write=will_write,
        existing_content=existing,
        final_content=final,
    )


def apply_suggestion(
    vault_path: Path,
    note_path: str,
    content: str,
    mode: str = "write",
    *,
    overwrite: bool = False,
    append_heading: str | None = None,
) -> ApplyNoteResult:
    """Write one note into the vault.

    For ``mode="write"``, an existing file is left untouched unless ``overwrite``
    is True. Overwrites keep a ``.bak`` sibling copy of the previous content.
    """
    try:
        target = _resolve_vault_target(vault_path, note_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        preview = preview_suggestion_merge(
            vault_path,
            note_path,
            content,
            mode,
            overwrite=overwrite,
            append_heading=append_heading,
        )
        rel = preview.note_path

        if mode == "append" and target.exists():
            _atomic_write_text(target, preview.final_content)
            return ApplyNoteResult(note_path=note_path, status="appended", written_path=rel)

        if mode == "append" and not target.exists():
            _atomic_write_text(target, preview.final_content)
            return ApplyNoteResult(note_path=note_path, status="written", written_path=rel)

        if mode == "write" and target.exists() and not overwrite:
            return ApplyNoteResult(
                note_path=note_path,
                status="skipped_exists",
                error="Note already exists; pass overwrite=true to replace (a .bak backup is kept)",
            )

        backup_path: str | None = None
        overwritten = False
        if mode == "write" and target.exists() and overwrite:
            backup_path = _backup_existing_note(target, vault_path)
            overwritten = True

        _atomic_write_text(target, preview.final_content)
        return ApplyNoteResult(
            note_path=note_path,
            status="written",
            written_path=rel,
            overwritten=overwritten,
            backup_path=backup_path,
        )
    except Exception as exc:  # noqa: BLE001 - surface per-note failures to the batch API
        return ApplyNoteResult(note_path=note_path, status="error", error=str(exc))


def apply_suggestions(
    vault_path: Path,
    notes: list[dict],
) -> list[ApplyNoteResult]:
    """Apply many notes, collecting per-note results instead of aborting on the first failure."""
    results: list[ApplyNoteResult] = []
    for note in notes:
        result = apply_suggestion(
            vault_path=vault_path,
            note_path=note["note_path"],
            content=note["content"],
            mode=note.get("mode", "write"),
            overwrite=bool(note.get("overwrite", False)),
            append_heading=note.get("append_heading"),
        )
        results.append(result)
    return results
