from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from queue import Full, Queue

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.checkpoint import (
    SuggestionCheckpoint,
    checkpoint_matches_source,
    list_incomplete_checkpoints,
    load_checkpoint_for_source,
    load_latest_checkpoint,
)
from app.config import settings
from app.embeddings import chunk_size_error
from app.graph import KnowledgeGraph
from app.index_meta import collect_index_warnings, load_index_meta, stale_note_count
from app.note_output import normalize_vault_relative_path
from app.novelty import NoveltyResult, analyze_novelty
from app.obsidian_uri import obsidian_open_uri, obsidian_uri_available
from app.runtime import ANALYZE_POOL, INDEX_LOCK, WORKER_POOL
from app.sources import SourceDispatcher
from app.suggest import (
    NoteSuggestion,
    apply_suggestion,
    apply_suggestions,
    iter_note_suggestions,
)
from app.text_limits import TEXT_LIMITS
from app.threshold_calibration import calibrate_thresholds
from app.thresholds import recommended_thresholds_for, threshold_mismatch_warnings
from app.vault_watcher import vault_watch
from app.vectorstore import VectorStore

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Refuse to start with a chunk size the embedding model cannot actually embed;
# novelty verdicts would silently be based on truncated text.
_chunk_error = chunk_size_error()
if _chunk_error:
    raise RuntimeError(_chunk_error)

# The SPA is served same-origin from this app, so no CORS middleware exists on
# purpose: browsers then refuse cross-origin reads and preflighted requests from
# other websites. The Host allowlist below additionally blocks DNS-rebinding
# attacks, where an attacker's hostname resolves to 127.0.0.1 to bypass the
# browser's same-origin policy against a local server.
app = FastAPI(title="Knowledge Database Actualizer", version="0.1.0")


def _host_allowed(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.split(":", 1)[0]
    return host in settings.allowed_host_set


@app.middleware("http")
async def host_allowlist(request: Request, call_next):
    """Reject requests whose Host is not in ALLOWED_HOSTS (DNS-rebinding guard)."""
    if not settings.allowed_host_set:  # empty = check disabled
        return await call_next(request)
    if _host_allowed(request.headers.get("host", "")):
        return await call_next(request)
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Invalid Host header. Add the host to ALLOWED_HOSTS "
            "if you are serving beyond localhost."
        },
    )


def _token_matches(provided: str, expected: str) -> bool:
    # Constant-time comparison; bytes form also tolerates non-ASCII tokens.
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    """When API_TOKEN is set, require it for /api/* (SPA shell stays public)."""
    token = settings.api_token
    if not token:
        return await call_next(request)

    path = request.url.path
    if path == "/" or path.startswith("/static"):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    bearer = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    header_token = request.headers.get("X-API-Token", "")
    if (bearer and _token_matches(bearer, token)) or (
        header_token and _token_matches(header_token, token)
    ):
        return await call_next(request)
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


vector_store = VectorStore()
_vector_stores: dict[str, VectorStore] = {}
graph = KnowledgeGraph()
source_dispatcher = SourceDispatcher()

_STREAM_DONE = object()


def get_vector_store(vault_path: Path | None = None) -> VectorStore:
    """Return the vector store for a vault (supports optional multi-vault indexing)."""
    if not settings.multi_vault_index_enabled:
        return vector_store
    resolved = vault_path or settings.vault_path
    if resolved is None:
        return vector_store
    key = str(resolved.resolve())
    store = _vector_stores.get(key)
    if store is None:
        store = VectorStore(vault_path=resolved)
        _vector_stores[key] = store
    return store


class VaultIndexRequest(BaseModel):
    vault_path: str | None = None
    if_stale: bool = False


class VaultWatchRequest(BaseModel):
    enabled: bool


class AnalyzeUrlRequest(BaseModel):
    url: str


class ApplySuggestionRequest(BaseModel):
    note_path: str
    content: str
    mode: str = Field(default="write", pattern="^(write|append)$")
    overwrite: bool = False
    vault_path: str | None = None
    append_heading: str | None = None


class ApplySuggestionsBatchRequest(BaseModel):
    notes: list[ApplySuggestionRequest]
    vault_path: str | None = None


class RefreshNotesRequest(BaseModel):
    vault_path: str | None = None
    note_paths: list[str] = Field(default_factory=list)


def _ensure_vault_configured() -> Path:
    if not settings.vault_path:
        raise HTTPException(status_code=400, detail="Vault path is not configured")
    vault_path = settings.vault_path.resolve()
    if not vault_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Vault path does not exist: {vault_path}")
    return vault_path


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
    settings.vault_path = vault_path
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
        settings.vault_path = vault_path
        return {
            "vault_path": str(vault_path),
            "notes": vault_result.note_count,
            "links": vault_result.link_count,
            "duplicate_stems": vault_result.duplicate_stems,
            **index_stats,
        }


@app.on_event("startup")
def startup_vault_watch() -> None:
    vault_watch.configure(_run_full_index)
    vault_watch.start()


@app.on_event("shutdown")
def shutdown_vault_watch() -> None:
    vault_watch.stop()


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


def _iter_analyze_events(
    *,
    clean_url: str | None,
    file_name: str | None,
    file_bytes: bytes | None,
    resume: bool,
    vault_note_path: str | None = None,
) -> Iterator[dict]:
    """Synchronous analyze pipeline (runs in a worker thread)."""
    checkpoint: SuggestionCheckpoint | None = None
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

        normalized_vault_note = normalize_vault_relative_path(vault_note_path) if vault_note_path else None
        if normalized_vault_note and source.source_type == "markdown":
            source.source_ref = normalized_vault_note

        for warning in source.load_warnings:
            yield {"type": "warning", "message": warning}

        checkpoint = SuggestionCheckpoint.for_source(source.source_type, source.source_ref)

        resume_suggestions: list[dict] | None = None
        if resume:
            saved = load_checkpoint_for_source(source.source_type, source.source_ref)
            if (
                    saved
                    and not saved.get("completed")
                    and checkpoint_matches_source(
                        saved,
                        source.source_ref,
                        source_type=source.source_type,
                    )
                ):
                resume_suggestions = saved.get("suggestions") or []
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
            saved = load_checkpoint_for_source(source.source_type, source.source_ref)
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

        active_store = get_vector_store(settings.vault_path)
        with INDEX_LOCK:
            indexed_chunks = active_store.chunk_count()
        for warning in collect_index_warnings(
            indexed_chunks=indexed_chunks,
            vault_path=settings.vault_path,
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
        novelty = analyze_novelty(
            source.text,
            active_store,
            source_tags=list(source.tags) if source.tags else None,
        )

        suggestions: list[NoteSuggestion] = []
        warnings: list[str] = []
        for event in iter_note_suggestions(
            source,
            novelty,
            active_store,
            checkpoint=checkpoint,
            resume_suggestions=resume_suggestions,
            vault_note_path=normalized_vault_note,
        ):
            if event.get("type") == "suggestions":
                suggestions = event["suggestions"]
                warnings = event.get("warnings", [])
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


@app.get("/api/status")
def get_status():
    active_store = get_vector_store(settings.vault_path)
    with INDEX_LOCK:
        indexed_chunks = active_store.chunk_count()
        graph_nodes = graph.graph.number_of_nodes()
        graph_edges = graph.graph.number_of_edges()
        if graph_nodes == 0 and settings.graph_cache_path.exists():
            graph.load(settings.graph_cache_path)
            graph_nodes = graph.graph.number_of_nodes()
            graph_edges = graph.graph.number_of_edges()

    vault_path = str(settings.vault_path) if settings.vault_path else None
    stale = stale_note_count()
    warnings = collect_index_warnings(
        indexed_chunks=indexed_chunks,
        vault_path=settings.vault_path,
    )
    warnings.extend(threshold_mismatch_warnings())
    return {
        "vault_path": vault_path,
        "indexed_chunks": indexed_chunks,
        "stale_note_count": stale,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "graph_loaded": graph_nodes > 0,
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider,
        "auth_required": bool(settings.api_token),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "thresholds": {
            "novel": settings.novel_threshold,
            "known": settings.known_threshold,
            "recommended": recommended_thresholds_for(),
            "calibration_available": indexed_chunks >= 10,
        },
        "llm_budget": {
            "max_calls_per_run": settings.llm_max_calls_per_run,
            "max_input_chars_per_run": settings.llm_max_input_chars_per_run,
        },
        "index_meta": load_index_meta(),
        "incomplete_checkpoints": list_incomplete_checkpoints(),
        "warnings": warnings,
        "obsidian_vault_name": settings.obsidian_vault_name,
        "obsidian_uri_enabled": obsidian_uri_available(),
        "vault_watch": vault_watch.status(),
        "note_output": {
            "folder": settings.note_output_folder,
            "pattern": settings.note_output_pattern,
            "layout": settings.note_output_layout,
        },
        "analyze_in_place_enabled": settings.analyze_in_place_enabled,
        "llm_draft_batch_size": settings.llm_draft_batch_size,
        "multi_vault_index_enabled": settings.multi_vault_index_enabled,
        "append_under_overlap_heading": settings.append_under_overlap_heading,
    }


@app.post("/api/vault/watch")
async def set_vault_watch(request: VaultWatchRequest):
    """Enable or disable automatic re-index when vault markdown files change."""
    vault_watch.set_enabled(request.enabled)
    if request.enabled and settings.vault_path is None:
        raise HTTPException(
            status_code=400,
            detail="Configure VAULT_PATH before enabling vault watch",
        )
    return vault_watch.status()


@app.post("/api/vault/index")
async def index_vault(request: VaultIndexRequest):
    vault_path = Path(request.vault_path) if request.vault_path else settings.vault_path
    if not vault_path:
        raise HTTPException(status_code=400, detail="vault_path is required")

    vault_path = vault_path.resolve()
    if not vault_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Vault path does not exist: {vault_path}")

    if request.if_stale:
        stale = stale_note_count(vault_path)
        if stale == 0:
            with INDEX_LOCK:
                indexed_chunks = get_vector_store(vault_path).chunk_count()
                graph_nodes = graph.graph.number_of_nodes()
                graph_edges = graph.graph.number_of_edges()
            meta = load_index_meta() or {}
            return {
                "skipped": True,
                "reason": "index is fresh",
                "stale_note_count": 0,
                "vault_path": str(vault_path),
                "indexed_chunks": indexed_chunks,
                "notes": meta.get("note_count", graph_nodes),
                "links": graph_edges,
                "index_mode": "skipped",
            }

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            WORKER_POOL,
            _run_full_index,
            vault_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result["stale_note_count"] = 0
    return result


@app.get("/api/vault/note")
def get_vault_note(note_path: str, vault_path: str | None = None):
    """Read an existing vault note (for append diff preview)."""
    if not note_path.strip():
        raise HTTPException(status_code=400, detail="note_path is required")
    vault = _resolve_vault_path(vault_path)
    from app.suggest import _resolve_vault_target

    try:
        target = _resolve_vault_target(vault, note_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rel = target.relative_to(vault.resolve()).as_posix()
    if not target.is_file():
        return {"exists": False, "note_path": rel, "content": ""}
    return {
        "exists": True,
        "note_path": rel,
        "content": target.read_text(encoding="utf-8"),
    }


@app.get("/api/vault/thresholds/calibrate")
def calibrate_vault_thresholds():
    """Suggest NOVEL/KNOWN thresholds from indexed vault chunk similarities."""
    with INDEX_LOCK:
        store = get_vector_store(settings.vault_path)
        if store.chunk_count() == 0:
            raise HTTPException(status_code=400, detail="Index is empty — index the vault first.")
        calibration = calibrate_thresholds(store)
    return calibration.to_dict()


@app.get("/api/vault/graph")
def get_graph(highlight: str | None = None):
    with INDEX_LOCK:
        if graph.graph.number_of_nodes() == 0 and settings.graph_cache_path.exists():
            graph.load(settings.graph_cache_path)
        highlight_nodes = [item.strip() for item in (highlight or "").split(",") if item.strip()]
        return graph.to_vis_json(highlight_nodes=highlight_nodes)


@app.post("/api/sources/analyze")
async def analyze_source(
    url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    resume: bool = Form(default=False),
    vault_note_path: str | None = Form(default=None),
):
    _ensure_vault_configured()

    if not url and not file:
        raise HTTPException(status_code=400, detail="Provide either url or file")

    # Read the upload before streaming; the worker thread must not touch UploadFile.
    file_bytes = await file.read() if file else None
    file_name = (file.filename or "upload.txt") if file else None
    clean_url = url.strip() if url else None

    limit_mb = settings.max_upload_mb
    if file_bytes is not None and limit_mb > 0 and len(file_bytes) > limit_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Upload is {len(file_bytes) / (1024 * 1024):.1f} MB; the limit is "
                f"{limit_mb} MB (MAX_UPLOAD_MB, 0 = unlimited)."
            ),
        )

    queue: Queue = Queue(maxsize=32)
    cancelled = threading.Event()

    def iterator_factory():
        return _iter_analyze_events(
            clean_url=clean_url,
            file_name=file_name,
            file_bytes=file_bytes,
            resume=resume,
            vault_note_path=vault_note_path.strip() if vault_note_path else None,
        )

    ANALYZE_POOL.submit(_ndjson_worker, queue, iterator_factory, cancelled)
    return StreamingResponse(
        _queue_to_async_stream(queue, cancelled),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/suggestions/checkpoint")
def get_suggestions_checkpoint(source_key: str | None = None):
    from app.checkpoint import load_checkpoint_by_key

    data = load_checkpoint_by_key(source_key) if source_key else load_latest_checkpoint()
    if not data:
        return {"exists": False}
    return {"exists": True, **data}


@app.post("/api/vault/refresh-notes")
async def refresh_vault_notes(request: RefreshNotesRequest):
    """Re-embed notes already written to disk (e.g. via the Obsidian plugin vault API)."""
    vault_path = _resolve_vault_path(request.vault_path)
    if not request.note_paths:
        raise HTTPException(status_code=400, detail="note_paths is required")

    def _refresh() -> dict:
        return _refresh_written_notes(vault_path, request.note_paths)

    return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _refresh)


@app.post("/api/suggestions/apply-batch")
async def apply_note_suggestions_batch(request: ApplySuggestionsBatchRequest):
    vault_path = _resolve_vault_path(request.vault_path)

    if not request.notes:
        raise HTTPException(status_code=400, detail="No notes provided")

    def _apply_and_refresh() -> dict:
        results = apply_suggestions(
            vault_path=vault_path,
            notes=[note.model_dump() for note in request.notes],
        )
        written_paths = [r.written_path for r in results if r.written_path]
        skipped_existing = [r.note_path for r in results if r.status == "skipped_exists"]
        errors = [
            {"note_path": r.note_path, "error": r.error}
            for r in results
            if r.status == "error"
        ]
        index_refresh = _refresh_written_notes(vault_path, written_paths)
        return {
            "results": [r.to_dict() for r in results],
            "written_paths": written_paths,
            "count": len(written_paths),
            "skipped_existing": skipped_existing,
            "errors": errors,
            "index_refresh": index_refresh,
            "vault_path": str(vault_path),
        }

    return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _apply_and_refresh)


@app.post("/api/suggestions/apply")
async def apply_note_suggestion(request: ApplySuggestionRequest):
    vault_path = _resolve_vault_path(request.vault_path)

    def _apply_one() -> dict:
        result = apply_suggestion(
            vault_path=vault_path,
            note_path=request.note_path,
            content=request.content,
            mode=request.mode,
            overwrite=request.overwrite,
            append_heading=request.append_heading,
        )
        if result.status == "error":
            raise ValueError(result.error or "Write failed")
        if result.status == "skipped_exists":
            raise FileExistsError(
                result.error or "Note already exists; pass overwrite=true to replace"
            )
        index_refresh = {}
        if result.written_path:
            index_refresh = _refresh_written_notes(vault_path, [result.written_path])
        return {
            "written_path": result.written_path,
            "mode": request.mode,
            "status": result.status,
            "overwritten": result.overwritten,
            "backup_path": result.backup_path,
            "index_refresh": index_refresh,
            "vault_path": str(vault_path),
        }

    try:
        return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _apply_one)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
