from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from queue import Full, Queue
from time import perf_counter

from fastapi import HTTPException, UploadFile

from app.analytics import record_counts
from app.checkpoint import (
    SuggestionCheckpoint,
    checkpoint_matches_source,
    load_checkpoint_for_source,
)
from app.config import settings
from app.deps import get_vector_store as _deps_get_vector_store
from app.deps import graph, source_dispatcher
from app.git_integration import commit_written_paths
from app.index_meta import collect_index_warnings
from app.note_output import normalize_vault_relative_path
from app.novelty import (
    NoveltyResult,
    novelty_from_checkpoint,
    prepare_planning_segments,
)
from app.novelty import (
    analyze_source_similarity as _novelty_analyze_source_similarity,
)
from app.observability import metrics
from app.obsidian_uri import obsidian_open_uri
from app.runtime import INDEX_LOCK
from app.suggest import (
    NoteSuggestion,
    analysis_fingerprint,
    iter_note_suggestions,
    topics_from_checkpoint,
)
from app.text_limits import TEXT_LIMITS

logger = logging.getLogger(__name__)

_STREAM_DONE = object()


def get_vector_store(vault_path: Path | None = None):
    """Prefer ``app.main.get_vector_store`` when tests monkeypatch the composition root."""
    import sys

    main_mod = sys.modules.get("app.main")
    if main_mod is not None:
        getter = getattr(main_mod, "get_vector_store", None)
        if getter is not None and getter is not get_vector_store:
            return getter(vault_path)
    return _deps_get_vector_store(vault_path)


def analyze_source_similarity(*args, **kwargs):
    """Prefer ``app.main.analyze_source_similarity`` when monkeypatched on main."""
    import sys

    main_mod = sys.modules.get("app.main")
    if main_mod is not None:
        fn = getattr(main_mod, "analyze_source_similarity", None)
        if fn is not None and fn is not analyze_source_similarity:
            return fn(*args, **kwargs)
    return _novelty_analyze_source_similarity(*args, **kwargs)


def _vault_path_permitted(vault_path: Path) -> bool:
    """Whether ``vault_path`` is allowed by ALLOWED_VAULT_ROOTS / VAULT_PATH policy."""
    resolved = vault_path.resolve()
    roots = settings.allowed_vault_root_paths
    if settings.is_networked_profile and not roots:
        return False
    if roots:
        return any(
            resolved == root or resolved.is_relative_to(root) for root in roots
        )
    if settings.vault_path is not None:
        configured = settings.vault_path.expanduser().resolve()
        return resolved == configured
    return not settings.is_networked_profile


def _resolve_vault_path(requested: str | None = None) -> Path:
    """Prefer an explicit path (UI / request), else the configured settings path."""
    raw = (requested or "").strip() or None
    if raw:
        vault_path = Path(raw).expanduser().resolve()
    elif settings.vault_path:
        vault_path = settings.vault_path.resolve()
    else:
        raise HTTPException(
            status_code=400,
            detail="Vault path is not configured. Enter a vault path and index it first.",
        )
    if not vault_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Vault path does not exist: {vault_path}")
    if not _vault_path_permitted(vault_path):
        raise HTTPException(
            status_code=403,
            detail=(
                "Vault path is not allowed. Set ALLOWED_VAULT_ROOTS to a parent "
                "directory, or use the configured VAULT_PATH."
            ),
        )
    return vault_path


def _novelty_to_dict(novelty: NoveltyResult) -> dict:
    overlapping = [
        {
            "note_path": note.note_path,
            "note_title": note.note_title,
            "max_similarity": round(note.max_similarity, 3),
            "sample_text": note.sample_text,
            "sample_heading": note.sample_heading,
            "tags": list(note.tags),
            "obsidian_uri": obsidian_open_uri(note.note_path),
        }
        for note in novelty.overlapping_notes
    ]
    tag_overlap = sorted({tag for note in novelty.overlapping_notes for tag in note.tags})
    return {
        "verdict": novelty.verdict.value,
        "novelty_score": novelty.novelty_score,
        "overlapping_notes": overlapping,
        "tag_overlap": tag_overlap,
        "novel_chunks": novelty.novel_chunks[: TEXT_LIMITS.api_chunk_list_limit],
        "known_chunks": novelty.known_chunks[: TEXT_LIMITS.api_chunk_list_limit],
        "chunk_results": [
            {
                "chunk_index": chunk.chunk_index,
                "best_similarity": round(chunk.best_similarity, 3),
                "is_novel": chunk.is_novel,
                "is_known": chunk.is_known,
                "text_preview": chunk.text[: TEXT_LIMITS.api_chunk_preview_chars],
            }
            for chunk in novelty.chunk_results
        ],
    }


def _run_full_index(vault_path: Path) -> dict:
    """Index vault + rebuild graph under the shared index lock."""
    with INDEX_LOCK:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        index_stats = get_vector_store(vault_path).index_vault(vault_path)
        vault_result = graph.build_from_vault(vault_path)
        graph.save(settings.graph_cache_path)
        return {
            "vault_path": str(vault_path),
            "notes": vault_result.note_count,
            "links": vault_result.link_count,
            "duplicate_stems": vault_result.duplicate_stems,
            **index_stats,
        }


def _refresh_written_notes(vault_path: Path, written_paths: list[str]) -> dict:
    """Incrementally re-embed and update graph nodes for notes just written.

    Failures here must not undo a successful write — Gemini/embedding outages are
    common, and the vault files are already on disk by this point.
    """
    if not written_paths:
        store = get_vector_store(vault_path)
        return {"indexed_notes": 0, "chunk_count": store.chunk_count()}
    try:
        with INDEX_LOCK:
            store = get_vector_store(vault_path)
            vector_stats = store.upsert_notes(vault_path, written_paths)
            graph_stats = graph.upsert_notes(vault_path, written_paths)
            graph.save(settings.graph_cache_path)
            return {**vector_stats, **graph_stats}
    except Exception as exc:  # noqa: BLE001 - surface as soft failure on the write response
        logger.exception("Post-write index refresh failed for %d note(s)", len(written_paths))
        return {
            "indexed_notes": 0,
            "error": str(exc),
            "warning": (
                "Notes were written to the vault, but the search index was not updated. "
                "Re-index the vault when embeddings are available."
            ),
        }


def _record_analytics(**counts: int) -> None:
    """Keep local telemetry failures from breaking analysis or vault writes."""
    try:
        record_counts(**counts)
    except OSError as exc:
        logger.warning("Could not record local analytics: %s", exc)


def _git_commit_for_paths(
    vault_path: Path, paths: list[str], source_title: str | None
) -> dict | None:
    if not settings.git_auto_commit_on_apply or not paths:
        return None
    source = (source_title or "Actualizer").replace("\n", " ").strip()
    try:
        message = settings.git_commit_message.format(source=source)
    except (KeyError, ValueError):
        message = f"Actualize notes from {source}"
    return commit_written_paths(vault_path, paths, message=message).to_dict()


def _iter_analyze_events(
    *,
    clean_url: str | None,
    file_name: str | None,
    file_bytes: bytes | None,
    resume: bool,
    vault_note_path: str | None = None,
    vault_path: Path | None = None,
) -> Iterator[dict]:
    """Synchronous analyze pipeline (runs in a worker thread)."""
    started = perf_counter()
    checkpoint: SuggestionCheckpoint | None = None
    analyze_failed = True
    drafted_count = 0
    llm_calls = 0
    cache_hits = 0
    try:
        yield {
            "type": "progress",
            "stage": "loading",
            "current": 0,
            "total": 0,
            "message": "Loading source...",
        }
        if clean_url:
            source = source_dispatcher.load_from_url(clean_url)
        elif file_name is not None and file_bytes is not None:
            source = source_dispatcher.load_from_bytes(file_name, file_bytes)
        else:
            raise ValueError("Provide either a source URL or an uploaded file.")

        normalized_vault_note = (
            normalize_vault_relative_path(vault_note_path, vault_path)
            if vault_note_path
            else None
        )
        if normalized_vault_note and source.source_type == "markdown":
            source.source_ref = normalized_vault_note

        for warning in source.load_warnings:
            yield {"type": "warning", "message": warning}

        checkpoint = SuggestionCheckpoint.for_source(
            source.source_type,
            source.source_ref,
            source_key=source.source_key,
        )

        resume_suggestions: list[dict] | None = None
        saved: dict | None = None
        if resume:
            saved = load_checkpoint_for_source(
                source.source_type,
                source.source_ref,
                source_key=source.source_key,
            )
            if (
                    saved
                    and not saved.get("completed")
                    and checkpoint_matches_source(
                        saved,
                        source.source_ref,
                        source_type=source.source_type,
                        source_key=source.source_key,
                    )
                ):
                resume_suggestions = saved.get("suggestions") or []
                cache_hits += len(resume_suggestions)
                yield {
                    "type": "progress",
                    "stage": "loading",
                    "current": 0,
                    "total": 0,
                    "message": (
                        f"Resuming: {len(resume_suggestions)} note(s) already saved..."
                    ),
                }
            else:
                yield {
                    "type": "error",
                    "message": (
                        "No matching interrupted run for this source. "
                        "Saved notes were left untouched — click Analyze to start fresh "
                        "(that replaces the checkpoint), or recover notes from the last run."
                    ),
                    "partial_suggestions": (saved or {}).get("suggestions") or [],
                }
                return
        else:
            saved = load_checkpoint_for_source(
                source.source_type,
                source.source_ref,
                source_key=source.source_key,
            )
            if saved and not saved.get("completed") and saved.get("suggestions"):
                n = len(saved["suggestions"])
                saved_ref = (saved.get("source") or {}).get("source_ref") or "unknown source"
                yield {
                    "type": "warning",
                    "message": (
                        f"Starting fresh replaces an interrupted run for this source with {n} "
                        f"saved note(s) ({saved_ref}). Use Continue interrupted run to keep them."
                    ),
                }

        active_store = get_vector_store(vault_path)
        with INDEX_LOCK:
            indexed_chunks = active_store.chunk_count()
        for warning in collect_index_warnings(
            indexed_chunks=indexed_chunks,
            vault_path=vault_path,
        ):
            yield {"type": "warning", "message": warning}

        yield {
            "type": "progress",
            "stage": "novelty",
            "current": 0,
            "total": 0,
            "message": "Assessing novelty against the vault...",
        }

        # Per-query locking lives in VectorStore; avoid holding INDEX_LOCK across
        # the whole novelty pass so /api/status stays responsive.
        planning_segments = None
        segment_scores = None
        precomputed_topics = None
        novelty: NoveltyResult
        reusable_analysis = bool(
            resume
            and saved
            and saved.get("plan") is not None
            and saved.get("novelty")
            and saved.get("plan_fingerprint") == analysis_fingerprint(source)
        )
        if reusable_analysis:
            cache_hits += 1
            assert saved is not None
            planning_segments = prepare_planning_segments(source)
            precomputed_topics = topics_from_checkpoint(
                saved.get("plan") or [],
                planning_segments,
            )
            if precomputed_topics:
                novelty = novelty_from_checkpoint(saved["novelty"])
            else:
                reusable_analysis = False
                yield {
                    "type": "warning",
                    "message": (
                        "Saved topic plan no longer maps to the source; recomputing similarity."
                    ),
                }
        elif resume and saved and saved.get("plan"):
            yield {
                "type": "warning",
                "message": (
                    "Source or analysis settings changed; recomputing similarity and topic plan."
                ),
            }

        if not reusable_analysis:
            similarity = analyze_source_similarity(source, active_store)
            novelty = similarity.novelty
            planning_segments = similarity.planning_segments
            segment_scores = similarity.segment_scores
            for warning in similarity.warnings:
                yield {"type": "warning", "message": warning}

        suggestions: list[NoteSuggestion] = []
        warnings: list[str] = []
        for event in iter_note_suggestions(
            source,
            novelty,
            active_store,
            checkpoint=checkpoint,
            resume_suggestions=resume_suggestions,
            vault_note_path=normalized_vault_note,
            vault_path=vault_path,
            planning_segments=planning_segments,
            segment_scores=segment_scores,
            precomputed_topics=precomputed_topics,
        ):
            if event.get("type") == "suggestions":
                suggestions = event["suggestions"]
                warnings = event.get("warnings", [])
                llm_calls = int((event.get("llm_budget") or {}).get("calls", 0))
            else:
                yield event

        with INDEX_LOCK:
            related_paths = graph.related_note_paths(
                [note.note_title for note in novelty.overlapping_notes]
            )
            graph_data = graph.to_vis_json(highlight_nodes=related_paths)

        vault_wikilinks: list[dict] = []
        if source.wikilinks:
            for link in source.wikilinks:
                resolved = graph.resolve_wikilink(link)
                vault_wikilinks.append(
                    {
                        "target": link,
                        "note_path": resolved,
                        "resolved": resolved is not None,
                    }
                )
                if resolved:
                    related_paths.append(resolved)
            if vault_wikilinks:
                graph_data = graph.to_vis_json(
                    highlight_nodes=sorted(set(related_paths))
                )

        drafted_count = len(suggestions)
        analyze_failed = False
        _record_analytics(analyzed_sources=1)
        yield {
            "type": "result",
            "source": {
                "title": source.title,
                "source_type": source.source_type,
                "source_ref": source.source_ref,
                "vault_note_path": normalized_vault_note,
                "text_length": len(source.text),
                "segment_count": len(source.segments),
                "wikilinks": vault_wikilinks,
                "tags": list(source.tags),
            },
            "novelty": _novelty_to_dict(novelty),
            "suggestions": [item.to_dict() for item in suggestions],
            "warnings": warnings,
            "graph": graph_data,
        }
    except Exception as exc:  # noqa: BLE001 - surface failures, keep partial work
        partial: list[dict] = []
        if checkpoint is not None:
            partial = checkpoint.suggestions
        yield {
            "type": "error",
            "message": str(exc),
            "partial_suggestions": partial,
        }
    finally:
        duration_ms = (perf_counter() - started) * 1000
        metrics.record_analyze(
            duration_ms,
            error=analyze_failed,
            notes_drafted=drafted_count,
            llm_calls=llm_calls,
            cache_hits=cache_hits,
        )
        logger.info(
            "analyze completed error=%s notes=%d llm_calls=%d cache_hits=%d duration_ms=%.1f",
            analyze_failed,
            drafted_count,
            llm_calls,
            cache_hits,
            duration_ms,
        )


def _put_unless_cancelled(queue: Queue, item, cancelled: threading.Event) -> bool:
    """Put ``item`` on the queue, giving up when the consumer has gone away.

    A plain blocking ``put`` would deadlock the worker thread forever once the
    client disconnects and the bounded queue fills up.
    """
    while not cancelled.is_set():
        try:
            queue.put(item, timeout=1.0)
            return True
        except Full:
            continue
    return False


def _ndjson_worker(queue: Queue, iterator_factory, cancelled: threading.Event) -> None:
    from app.runtime import release_analyze_slot

    events = iterator_factory()
    try:
        for payload in events:
            if not _put_unless_cancelled(queue, json.dumps(payload) + "\n", cancelled):
                logger.info(
                    "Analyze client disconnected; stopping the run early "
                    "(drafted notes remain in the checkpoint)"
                )
                return
    except Exception as exc:  # noqa: BLE001
        _put_unless_cancelled(
            queue,
            json.dumps(
                {"type": "error", "message": str(exc), "partial_suggestions": []}
            )
            + "\n",
            cancelled,
        )
    finally:
        release_analyze_slot()
        events.close()
        # Wake a consumer that may still be blocked on queue.get(). If the
        # queue is full the consumer is not blocked, so dropping DONE is safe.
        try:
            queue.put_nowait(_STREAM_DONE)
        except Full:
            pass


async def _queue_to_async_stream(queue: Queue, cancelled: threading.Event) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    try:
        while True:
            item = await loop.run_in_executor(None, queue.get)
            if item is _STREAM_DONE:
                break
            yield item
    finally:
        # Runs on normal completion and on client disconnect (generator close);
        # tells the producer to stop instead of blocking on a full queue.
        cancelled.set()


async def _read_upload_bounded(upload: UploadFile, limit_mb: int) -> bytes:
    """Read in bounded chunks and stop as soon as the configured cap is exceeded."""
    chunk_size = 1024 * 1024
    limit_bytes = limit_mb * 1024 * 1024 if limit_mb > 0 else None
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if limit_bytes is not None and total > limit_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload exceeds the {limit_mb} MB limit "
                    "(MAX_UPLOAD_MB, 0 = unlimited)."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)
