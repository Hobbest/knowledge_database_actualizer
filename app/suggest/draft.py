from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from queue import SimpleQueue
from typing import cast

import yaml

from app.atomic_notes import (
    AtomicTopic,
    SegmentNovelty,
    topic_location,
)
from app.checkpoint import SuggestionCheckpoint
from app.config import settings
from app.json_extract import extract_json_array
from app.llm import call_with_retry, complete_with_usage, get_llm_provider, is_rate_limit_error
from app.llm_budget import LLMBudget, estimate_run_cost_hint
from app.media import media_for_location, render_media_section
from app.note_intelligence import (
    annotate_note_intelligence,
    format_vault_context,
    retrieve_vault_context,
)
from app.note_output import (
    apply_note_template,
    default_note_path,
    moc_note_path,
    normalize_vault_relative_path,
    parse_append_target,
    topic_overlap_match,
    vault_relative_paths_equal,
)
from app.novelty import NoveltyResult, OverlappingNote, novelty_to_checkpoint
from app.progressive import build_evidence_pack, format_for_prompt, pack_to_budget
from app.prompts import NOTE_WRITER_SYSTEM_PROMPT, batch_note_draft_prompt, note_draft_prompt
from app.sources.base import LoadedSource, SourceLocation, SourceSegment
from app.suggest.models import NoteSuggestion
from app.suggest.plan import (
    _source_meta,
    _stream_plan_topics,
    analysis_fingerprint,
    topics_to_checkpoint,
)
from app.summarize import (
    compose_title,
    ensure_concept_heading,
    key_points,
    refine_note_body,
    summarize_text,
)
from app.text_limits import TEXT_LIMITS
from app.text_utils import combine_segment_text
from app.update_detection import detect_update
from app.vector_protocol import VectorStoreProtocol as VectorStore
from app.wikilinks import format_wikilink

logger = logging.getLogger(__name__)


def _evidence_for_topic(topic: AtomicTopic, *, max_chars: int) -> str:
    """Build budget-packed progressive evidence from the full topic body."""
    text = combine_segment_text(topic.segments) or topic.summary or ""
    pack = build_evidence_pack(
        text,
        planner_summary=topic.summary or None,
        title=topic.title,
    )
    packed = pack_to_budget(pack, max_chars)
    return format_for_prompt(packed)


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


def _topic_related_links(
    vector_store: VectorStore | None,
    topic: AtomicTopic,
    *,
    source_tags: list[str] | None,
    fallback: list[str],
) -> list[str]:
    """Wikilinks to the vault notes most related to *this* topic's concept.

    Each note links to concepts relevant to its own content instead of sharing
    one source-wide list. Matches below ``NOVEL_THRESHOLD`` are dropped so
    unrelated notes are not linked; a truly novel note therefore links nothing
    rather than the source's global overlaps. Falls back to the source-level
    related links only when the vault cannot be queried (no index / no text).
    """
    if vector_store is None or vector_store.chunk_count() == 0:
        return list(fallback)
    text = combine_segment_text(topic.segments).strip()
    if not text:
        return list(fallback)
    try:
        matches = vector_store.query_similar(
            text,
            top_k=TEXT_LIMITS.related_link_count,
            query_tags=source_tags,
        )
    except Exception:  # noqa: BLE001 - related links are best-effort
        return list(fallback)

    links: list[str] = []
    for match in matches:
        if match.content_similarity < settings.novel_threshold:
            continue
        if not match.note_path:
            continue
        link = format_wikilink(match.note_path)
        if link and link not in links:
            links.append(link)
    return links


_RELATED_SECTION_RE = re.compile(
    r"(?P<head>#{1,6}[ \t]*Related notes[ \t]*\n+)(?P<body>(?:[ \t]*-[ \t].*\n?)+)",
    re.IGNORECASE,
)


def _strip_related_notes_section(body: str) -> str:
    """Remove any 'Related notes' heading + list so the code can author its own."""
    return re.sub(
        r"\n#{1,6}[ \t]*Related notes\b[\s\S]*?(?=\n#{1,6}\s|\Z)",
        "\n",
        body.strip(),
        flags=re.IGNORECASE,
    ).strip()


def _inject_related_links(content: str, extra_links: list[str]) -> str:
    """Merge ``extra_links`` into an existing '## Related notes' bullet list."""
    if not extra_links:
        return content
    match = _RELATED_SECTION_RE.search(content)
    if not match:
        return content
    existing = [
        line.strip()[2:].strip()
        for line in match.group("body").splitlines()
        if line.strip().startswith("- ")
    ]
    existing = [link for link in existing if link and link.casefold() != "none"]
    merged = list(dict.fromkeys([*existing, *extra_links]))
    new_block = "".join(f"- {link}\n" for link in merged)
    return content[: match.start("body")] + new_block + content[match.end("body") :]


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _link_sibling_notes(
    pairs: list[tuple[NoteSuggestion, str]],
    vector_store: VectorStore | None,
) -> None:
    """Cross-link notes from this source to their most similar siblings.

    Builds a small local graph among the new notes (Zettelkasten style) on top
    of the source-level MOC. Best-effort: any embedding failure (including a
    rate limit) leaves the notes untouched. Topic texts reuse the cached query
    embeddings from ``_topic_related_links`` so this rarely costs extra calls.
    """
    if not settings.link_sibling_notes or vector_store is None:
        return
    notes = [(item, text) for item, text in pairs if item.note_path and text.strip()]
    if len(notes) < 2:
        return
    try:
        vectors = vector_store.embedding_service.embed_texts(
            [text for _item, text in notes],
            task_type="RETRIEVAL_QUERY",
        )
    except Exception:  # noqa: BLE001 - sibling links are best-effort
        return
    if len(vectors) != len(notes):
        return

    normalized = [_normalize_vector(vector) for vector in vectors]
    count = max(1, settings.sibling_link_count)
    for i, (suggestion, _text) in enumerate(notes):
        scored: list[tuple[float, NoteSuggestion]] = []
        for j, (other, _other_text) in enumerate(notes):
            if i == j or not other.note_path:
                continue
            similarity = _cosine(normalized[i], normalized[j])
            if similarity >= settings.novel_threshold:
                scored.append((similarity, other))
        scored.sort(key=lambda entry: entry[0], reverse=True)
        links: list[str] = []
        for _similarity, other in scored[:count]:
            link = format_wikilink(other.note_path)
            if link and link not in links:
                links.append(link)
        if links:
            suggestion.content = _inject_related_links(suggestion.content, links)


def _infer_note_tags(
    overlapping_notes: list[OverlappingNote],
    *,
    vector_store: VectorStore | None = None,
    topic_text: str = "",
) -> list[str]:
    tags: list[str] = list(settings.default_note_tags_list)
    for note in overlapping_notes[: settings.auto_tagging_top_k]:
        tags.extend(note.tags)
    if settings.auto_tagging_enabled and vector_store is not None and topic_text.strip():
        try:
            for match in vector_store.query_similar(topic_text, top_k=settings.auto_tagging_top_k):
                tags.extend(match.tags)
        except Exception:  # noqa: BLE001 - inferred tags are optional metadata
            pass
    return list(dict.fromkeys(tag for tag in tags if tag))[: settings.auto_tagging_max_tags]


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


def _vault_context_for_topic(
    vector_store: VectorStore | None,
    topic: AtomicTopic,
    *,
    source_tags: list[str] | None,
) -> str:
    text = combine_segment_text(topic.segments).strip() or topic.summary or topic.title
    chunks = retrieve_vault_context(
        vector_store,
        text,
        query_tags=source_tags,
    )
    return format_vault_context(chunks)


def _llm_draft_topic_body(
    source: LoadedSource,
    topic: AtomicTopic,
    related_links: list[str],
    *,
    budget: LLMBudget | None = None,
    vault_context: str = "",
) -> str | None:
    provider = get_llm_provider()
    if not provider:
        return None

    location = topic_location(topic)
    title = compose_title(topic.title)
    evidence = _evidence_for_topic(
        topic,
        max_chars=TEXT_LIMITS.note_draft_excerpt_chars,
    )
    prompt = note_draft_prompt(
        source=source,
        concept_title=title,
        location_display=location.display(),
        evidence=evidence,
        related_links=related_links,
        max_note_lines=settings.max_note_lines,
        vault_context=vault_context,
    )
    prompt_chars = len(prompt) + len(NOTE_WRITER_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        raise RuntimeError(budget.refuse(prompt_chars))

    logger.info(
        "LLM draft note '%s' (budget calls %s/%s)",
        title,
        (budget.calls + 1) if budget is not None else "?",
        budget.max_calls if budget is not None and budget.max_calls > 0 else "∞",
    )
    # Record even on failure: retries / empty replies still consume provider quota.
    usage = None
    try:
        raw, usage = complete_with_usage(
            provider,
            prompt,
            system=NOTE_WRITER_SYSTEM_PROMPT,
        )
        return raw.strip() or None
    finally:
        if budget is not None:
            budget.record(prompt_chars, usage)


def _batch_draft_payload(
    topics: list[AtomicTopic],
    *,
    vector_store: VectorStore | None = None,
    source_tags: list[str] | None = None,
) -> list[dict]:
    """Build the JSON payload for a batch draft prompt."""
    batch_excerpt_chars = min(
        TEXT_LIMITS.batch_draft_excerpt_chars,
        TEXT_LIMITS.note_draft_excerpt_chars,
    )
    payload: list[dict] = []
    for index, topic in enumerate(topics):
        location = topic_location(topic)
        title = compose_title(topic.title)
        evidence = _evidence_for_topic(topic, max_chars=batch_excerpt_chars)
        item: dict = {
            "id": str(index),
            "title": title,
            "location": location.display(),
            "evidence": evidence,
        }
        vault_context = _vault_context_for_topic(
            vector_store,
            topic,
            source_tags=source_tags,
        )
        if vault_context:
            item["vault_context"] = vault_context
        payload.append(item)
    return payload


def _batch_draft_prompt_chars(
    source: LoadedSource,
    topics: list[AtomicTopic],
    related_links: list[str],
    *,
    vector_store: VectorStore | None = None,
    source_tags: list[str] | None = None,
) -> int:
    """Estimate input chars for drafting ``topics`` in one batch call."""
    batch_max_lines = min(30, settings.max_note_lines)
    prompt = batch_note_draft_prompt(
        source=source,
        topics=_batch_draft_payload(
            topics,
            vector_store=vector_store,
            source_tags=source_tags,
        ),
        related_links=related_links,
        max_note_lines=batch_max_lines,
    )
    return len(prompt) + len(NOTE_WRITER_SYSTEM_PROMPT)


def _largest_batch_that_fits(
    source: LoadedSource,
    topics: list[AtomicTopic],
    related_links: list[str],
    budget: LLMBudget,
    *,
    max_size: int,
    vector_store: VectorStore | None = None,
    source_tags: list[str] | None = None,
) -> int:
    """Return how many leading topics fit in the remaining input-char budget."""
    if not topics or max_size < 1:
        return 0
    if budget.exhausted or budget.remaining_calls <= 0:
        return 0
    take = min(max_size, len(topics))
    while take > 0:
        if budget.can_call(
            _batch_draft_prompt_chars(
                source,
                topics[:take],
                related_links,
                vector_store=vector_store,
                source_tags=source_tags,
            )
        ):
            return take
        take -= 1
    return 0


_BATCH_NOTE_MARKER_RE = re.compile(
    r"^[ \t]*={2,}\s*NOTE\s*[:#]?\s*(\d+)\s*={2,}[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_batch_fences(body: str) -> str:
    """Drop a stray code fence a model may wrap a single note body in."""
    cleaned = body.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", cleaned, count=1)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned, count=1)
    return cleaned.strip()


def _parse_delimited_notes(raw: str) -> dict[str, str]:
    """Parse '===NOTE <id>===' delimited markdown blocks into id -> body.

    Bodies are raw markdown between markers, so quotes, backslashes, brackets and
    newlines need no escaping -- the failure mode that made JSON batch replies
    unparseable whenever a note body contained a `"` or `\\`.
    """
    markers = list(_BATCH_NOTE_MARKER_RE.finditer(raw))
    bodies: dict[str, str] = {}
    for position, marker in enumerate(markers):
        note_id = marker.group(1)
        start = marker.end()
        end = markers[position + 1].start() if position + 1 < len(markers) else len(raw)
        body = _strip_batch_fences(raw[start:end])
        if body and note_id not in bodies:
            bodies[note_id] = body
    return bodies


def _llm_draft_topics_batch(
    source: LoadedSource,
    topics: list[AtomicTopic],
    related_links: list[str],
    *,
    budget: LLMBudget | None = None,
    vector_store: VectorStore | None = None,
    source_tags: list[str] | None = None,
) -> dict[str, str]:
    """Draft several topics in one LLM call; returns topic id -> body.

    The response is delimited markdown (``===NOTE <id>===`` blocks), not JSON, so
    note bodies never need escaping. A JSON array is still accepted as a fallback
    for models that ignore the format. Not provider json_mode: Gemini json_mode
    often returns empty/blocked payloads for large batches.
    """
    provider = get_llm_provider()
    if not provider or not topics:
        return {}

    # Keep prompts/outputs small so replies are less likely to truncate.
    batch_max_lines = min(30, settings.max_note_lines)
    payload = _batch_draft_payload(
        topics,
        vector_store=vector_store,
        source_tags=source_tags,
    )

    prompt = batch_note_draft_prompt(
        source=source,
        topics=payload,
        related_links=related_links,
        max_note_lines=batch_max_lines,
    )
    prompt_chars = len(prompt) + len(NOTE_WRITER_SYSTEM_PROMPT)
    if budget is not None and not budget.can_call(prompt_chars):
        raise RuntimeError(budget.refuse(prompt_chars))

    logger.info(
        "LLM batch draft %s topic(s) (budget calls %s/%s)",
        len(topics),
        (budget.calls + 1) if budget is not None else "?",
        budget.max_calls if budget is not None and budget.max_calls > 0 else "∞",
    )
    # Always record the call — provider bills even empty/failed responses.
    usage = None
    try:
        response, usage = complete_with_usage(
            provider,
            prompt,
            system=NOTE_WRITER_SYSTEM_PROMPT,
        )
        raw = response.strip()
    finally:
        if budget is not None:
            budget.record(prompt_chars, usage)
    if not raw:
        raise RuntimeError("LLM returned an empty batch draft response")

    # Primary format: plain-markdown blocks delimited by '===NOTE <id>==='.
    bodies = _parse_delimited_notes(raw)

    if not bodies:
        # Back-compat: some models still answer with a JSON array/wrapper.
        title_to_id = {
            compose_title(topic.title).casefold(): str(index)
            for index, topic in enumerate(topics)
        }
        try:
            payload = extract_json_array(raw)
        except Exception:  # noqa: BLE001 - treated as an unparseable batch below
            payload = []
        for item in payload:
            body = str(item.get("body", "")).strip()
            if not body:
                continue
            topic_id = str(item.get("id", "")).strip()
            if topic_id and topic_id not in bodies:
                bodies[topic_id] = body
                continue
            if not topic_id:
                mapped = title_to_id.get(str(item.get("title", "")).strip().casefold())
                if mapped and mapped not in bodies:
                    bodies[mapped] = body

    if not bodies:
        preview = raw[:200].replace("\n", "\\n")
        raise RuntimeError(
            f"Batch draft returned no usable note bodies (preview={preview!r})"
        )
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

    # Related links specific to this concept, so each note points at the vault
    # notes relevant to *it* rather than to the source as a whole.
    source_tags = list(source.tags) if source.tags else None
    topic_links = _topic_related_links(
        vector_store,
        topic,
        source_tags=source_tags,
        fallback=related_links,
    )

    body = pre_drafted_body
    if body is None and use_llm:
        # May raise (e.g. rate limit / budget); the caller decides how to recover.
        vault_context = _vault_context_for_topic(
            vector_store,
            topic,
            source_tags=source_tags,
        )
        body = _llm_draft_topic_body(
            source,
            topic,
            topic_links,
            budget=budget,
            vault_context=vault_context,
        )
    body = body or _fallback_topic_body(topic)
    body = _strip_source_section(body)

    # Single refinement pass gives LLM and extractive notes the same consistent
    # structure (heading spacing, deduped bullets, no ragged blank lines).
    body = refine_note_body(body)
    # Keep frontmatter concept and body H1 aligned (LLM drafts often diverge).
    body = ensure_concept_heading(body, title)

    # Attach any tables/figures that live on this note's page or line range.
    if settings.include_media and source.media and "## Tables & figures" not in body:
        media_items = media_for_location(location, source.media)
        media_section = render_media_section(media_items)
        if media_section:
            body = body.rstrip() + "\n\n" + media_section

    # Author the Related notes section from per-topic links so it is accurate
    # and never duplicated by a section the LLM may have emitted itself.
    body = _strip_related_notes_section(body)
    related_section = "\n".join(f"- {link}" for link in topic_links) if topic_links else "- none"
    body = body.rstrip() + f"\n\n## Related notes\n\n{related_section}\n"

    body = body.rstrip() + "\n\n" + _source_section(source, location)
    topic_text = combine_segment_text(topic.segments)
    note_tags = _infer_note_tags(
        overlapping_notes or [],
        vector_store=vector_store,
        topic_text=topic_text,
    )
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

    update_type: str | None = None
    update_reason: str | None = None
    update_target: str | None = None
    if vector_store is not None and topic_text.strip():
        try:
            closest = vector_store.query_similar(topic_text, top_k=1)
            if closest:
                detection = detect_update(
                    topic_text,
                    closest[0].text,
                    similarity=closest[0].content_similarity,
                )
                update_type = detection.update_type
                update_reason = detection.update_reason
                if update_type:
                    update_target = closest[0].note_path
                    if not append_target:
                        append_target = closest[0].note_path
                        append_heading = closest[0].heading
                        overlap_similarity = round(
                            closest[0].content_similarity, 3
                        )
        except Exception:  # noqa: BLE001 - update hints are best-effort
            pass

    suggestion = NoteSuggestion(
        concept_title=title,
        note_path=note_path,
        content=content,
        location=location.to_dict(),
        segment_indices=[segment.index for segment in topic.segments],
        write_mode="append" if update_type and append_target else "write",
        append_target=append_target,
        append_heading=append_heading,
        overlap_similarity=overlap_similarity,
        is_novel=bool(topic.is_novel),
        update_type=update_type,
        update_target=update_target,
        update_reason=update_reason,
    )
    _apply_analyze_in_place(suggestion, vault_note_path)
    return suggestion


def iter_note_suggestions(
    source: LoadedSource,
    novelty: NoveltyResult,
    vector_store: VectorStore | None = None,
    *,
    checkpoint: SuggestionCheckpoint | None = None,
    resume_suggestions: list[dict] | None = None,
    vault_note_path: str | None = None,
    vault_path: Path | None = None,
    planning_segments: list[SourceSegment] | None = None,
    segment_scores: list[SegmentNovelty] | None = None,
    precomputed_topics: list[AtomicTopic] | None = None,
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
    if precomputed_topics is not None:
        topics = precomputed_topics
        yield {
            "type": "progress",
            "stage": "scoring",
            "current": len(topics),
            "total": len(topics),
            "message": "Reusing saved similarity scores and topic plan",
        }
    else:
        topics = yield from _stream_plan_topics(
            source,
            novelty,
            vector_store,
            warnings=warnings,
            budget=budget,
            planning_segments=planning_segments,
            segment_scores=segment_scores,
        )

    if settings.llm_enabled and topics:
        batch_size_hint = max(1, settings.llm_draft_batch_size)
        pending_count = len(topics)
        est_rounds = (pending_count + batch_size_hint - 1) // batch_size_hint
        remaining = budget.remaining_calls
        capped_rounds = est_rounds if remaining <= 0 else min(est_rounds, remaining)
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
        if get_llm_provider() and capped_rounds > 0:
            provider_name = (settings.llm_provider or "llm").lower()
            yield {
                "type": "warning",
                "message": (
                    f"About to run up to {capped_rounds} {provider_name} chat completion(s) "
                    f"for {pending_count} planned note(s) "
                    f"(batch size {batch_size_hint}, budget {budget.calls}/{budget.max_calls or '∞'}). "
                    "Local models can take many minutes — progress updates between calls."
                ),
            }
            logger.info(
                "Starting LLM drafting: %s topics, ~%s completion rounds, provider=%s",
                pending_count,
                capped_rounds,
                provider_name,
            )

    if checkpoint is not None:
        if resume_suggestions:
            checkpoint.resume(_source_meta(source), resume_suggestions)
        else:
            checkpoint.start(_source_meta(source))
        checkpoint.set_analysis(
            plan=topics_to_checkpoint(topics),
            novelty=novelty_to_checkpoint(novelty),
            plan_fingerprint=analysis_fingerprint(source),
        )

    suggestions: list[NoteSuggestion] = []
    # Freshly drafted (suggestion, topic text) pairs used to cross-link siblings.
    sibling_candidates: list[tuple[NoteSuggestion, str]] = []
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
    # When batch size > 1, LLM drafting happens only in the batch phase.
    # Missing/failed items use extractive bodies — never a second per-note call.
    batch_mode = batch_size > 1
    consecutive_batch_failures = 0

    if batch_mode and not llm_disabled and get_llm_provider():
        pending: list[tuple[tuple, AtomicTopic]] = []
        for topic in topics:
            identity = _note_identity(
                [segment.index for segment in topic.segments], compose_title(topic.title)
            )
            if identity in done_by_identity:
                continue
            pending.append((identity, topic))

        # Draft novel topics first so a limited LLM budget (or an early rate
        # limit) is spent where the tool adds the most value; known/partial
        # topics degrade to extractive summaries instead of the novel ones.
        # A stable sort preserves source order within each novelty group, and
        # the final note ordering is untouched (the main loop below still
        # iterates ``topics`` in source order).
        pending.sort(key=lambda item: 0 if item[1].is_novel else 1)

        # Shrink batches as the char budget runs low so leftover capacity is used
        # instead of marking the run exhausted on the first oversized batch.
        cursor = 0
        batch_index = 0
        while cursor < len(pending):
            if consecutive_batch_failures >= settings.llm_disable_after_failures:
                yield record_warning(
                    "Repeated batch draft failures; remaining notes use extractive summaries."
                )
                break
            if budget.exhausted:
                llm_disabled = True
                break

            remaining_topics = [item[1] for item in pending[cursor:]]
            take = _largest_batch_that_fits(
                source,
                remaining_topics,
                related_links,
                budget,
                max_size=batch_size,
                vector_store=vector_store,
                source_tags=list(source.tags) if source.tags else None,
            )
            if take == 0:
                next_chars = _batch_draft_prompt_chars(
                    source,
                    remaining_topics[:1],
                    related_links,
                    vector_store=vector_store,
                    source_tags=list(source.tags) if source.tags else None,
                )
                llm_disabled = True
                yield record_warning(budget.refuse(next_chars))
                break

            batch = pending[cursor : cursor + take]
            batch_topics = [item[1] for item in batch]
            batch_index += 1
            note_start = cursor + 1
            note_end = cursor + take
            yield {
                "type": "progress",
                "stage": "drafting",
                "current": min(note_end, total),
                "total": total,
                "message": (
                    f"Batch drafting notes {note_start}-{note_end} "
                    f"(batch {batch_index}, size {take}; "
                    f"LLM call {budget.calls + 1}"
                    f"{f'/{budget.max_calls}' if budget.max_calls > 0 else ''})"
                ),
            }
            try:
                def draft_batch(
                    topics_for_call: list[AtomicTopic] = batch_topics,
                ) -> dict[str, str]:
                    return _llm_draft_topics_batch(
                        source,
                        topics_for_call,
                        related_links,
                        budget=budget,
                        vector_store=vector_store,
                        source_tags=list(source.tags) if source.tags else None,
                    )

                bodies = call_with_retry(draft_batch)
                filled = 0
                for offset, (identity, _topic) in enumerate(batch):
                    body = bodies.get(str(offset))
                    if body:
                        batch_bodies[identity] = body
                        filled += 1
                consecutive_batch_failures = 0
                missing = len(batch) - filled
                if missing:
                    yield record_warning(
                        f"Batch draft returned {filled}/{len(batch)} note bodies for notes "
                        f"{note_start}-{note_end}; missing notes use extractive summaries."
                    )
            except Exception as exc:  # noqa: BLE001
                consecutive_batch_failures += 1
                if budget.exhausted:
                    llm_disabled = True
                    yield record_warning(budget.exhausted_reason or str(exc))
                    break
                yield record_warning(
                    f"Batch draft failed for notes {note_start}-{note_end}; "
                    f"those notes use extractive summaries ({exc})."
                )
            cursor += take

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
        # Per-note LLM only when batch drafting is off (size == 1).
        use_llm_for_note = (not batch_mode) and (not llm_disabled)

        if use_llm_for_note:
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
        if not suggestion.is_moc:
            sibling_candidates.append((suggestion, combine_segment_text(topic.segments)))
        if checkpoint is not None:
            checkpoint.add(suggestion.to_dict())

    # Cross-link the new notes to their most similar siblings (Zettelkasten
    # style), in addition to the source-level MOC. Best-effort and additive.
    _link_sibling_notes(sibling_candidates, vector_store)

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

    # Quality scores + near-duplicate detection across the proposed set.
    annotate_note_intelligence(suggestions, vector_store)

    if checkpoint is not None:
        checkpoint.replace_suggestions([item.to_dict() for item in suggestions])
        checkpoint.finish(completed=True)

    if budget.calls:
        if budget.calls_with_usage:
            usage_text = (
                f"{budget.actual_input_tokens:,} input / "
                f"{budget.actual_output_tokens:,} output tokens reported"
            )
            if budget.calls_with_usage < budget.calls:
                usage_text += f" for {budget.calls_with_usage}/{budget.calls} call(s)"
        else:
            usage_text = f"~{budget.input_chars // 4:,} estimated input tokens"
        warnings.append(f"LLM usage this run: {budget.calls} call(s), {usage_text}.")

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

