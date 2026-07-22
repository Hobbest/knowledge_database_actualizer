from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.api.common import _resolve_vault_path, _run_full_index
from app.api.schemas import (
    RefreshNotesRequest,
    ThresholdUpdateRequest,
    VaultIndexRequest,
    VaultWatchRequest,
)
from app.config import settings
from app.deps import FRONTEND_DIR, get_vector_store, graph
from app.index_meta import active_vault_path, load_index_meta, stale_note_count
from app.obsidian_uri import obsidian_open_uri
from app.runtime import INDEX_LOCK, WORKER_POOL
from app.settings_persistence import update_env_values as _persist_update_env_values
from app.text_limits import TEXT_LIMITS
from app.threshold_calibration import calibrate_thresholds
from app.vault_watcher import vault_watch

router = APIRouter()


def update_env_values(*args, **kwargs):
    """Prefer ``app.main.update_env_values`` when monkeypatched on the composition root."""
    import sys

    main_mod = sys.modules.get("app.main")
    if main_mod is not None:
        fn = getattr(main_mod, "update_env_values", None)
        if fn is not None and fn is not update_env_values:
            return fn(*args, **kwargs)
    return _persist_update_env_values(*args, **kwargs)


@router.post("/api/vault/watch")
async def set_vault_watch(request: VaultWatchRequest):
    """Enable or disable automatic re-index when vault markdown files change."""
    vault_watch.set_enabled(request.enabled)
    if request.enabled and active_vault_path() is None:
        raise HTTPException(
            status_code=400,
            detail="Index a vault first (or configure VAULT_PATH) before enabling vault watch",
        )
    return vault_watch.status()


@router.post("/api/vault/index")
async def index_vault(request: VaultIndexRequest):
    try:
        vault_path = _resolve_vault_path(request.vault_path)
    except HTTPException:
        raise

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


@router.get("/api/vault/note")
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
    if target.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only .md notes can be read")
    rel = target.relative_to(vault.resolve()).as_posix()
    if not target.is_file():
        return {"exists": False, "note_path": rel, "content": ""}
    return {
        "exists": True,
        "note_path": rel,
        "content": target.read_text(encoding="utf-8"),
    }


@router.get("/api/vault/search")
def search_vault(
    q: str = Query(min_length=1, max_length=500),
    mode: str = Query(default="semantic", pattern="^(semantic|keyword)$"),
    top_k: int = Query(default=10, ge=1, le=50),
    vault_path: str | None = None,
):
    """Search indexed vault chunks by meaning or literal terms."""
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query is required")
    vault = _resolve_vault_path(vault_path)
    store = get_vector_store(vault)
    if store.chunk_count() == 0:
        raise HTTPException(status_code=400, detail="Index is empty — index the vault first.")
    try:
        matches = (
            store.search_keyword(query, top_k=top_k)
            if mode == "keyword"
            else store.query_similar(query, top_k=top_k)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vault search failed: {exc}") from exc
    return {
        "query": query,
        "mode": mode,
        "results": [
            {
                "note_path": match.note_path,
                "note_title": match.note_title,
                "heading": match.heading,
                "snippet": match.text[: TEXT_LIMITS.api_chunk_preview_chars],
                "score": round(match.similarity, 3),
                "tags": match.tags,
                "obsidian_uri": obsidian_open_uri(match.note_path),
            }
            for match in matches
        ],
    }


@router.get("/api/vault/index/export")
def export_index_metadata(
    vault_path: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
):
    """Export portable index metadata; vectors are rebuilt with the target backend."""
    vault = _resolve_vault_path(vault_path)
    store = get_vector_store(vault)
    samples = store.sample_chunks(limit=limit)
    return {
        "format": "actualizer-index-metadata-v1",
        "vector_backend": settings.vector_backend,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "vault_path": str(vault),
        "index_meta": load_index_meta(),
        "chunks": [
            {
                "chunk_id": item.chunk_id,
                "note_path": item.note_path,
                "text": item.text,
            }
            for item in samples
        ],
        "truncated": store.chunk_count() > len(samples),
    }


@router.get("/api/vault/thresholds/calibrate")
def calibrate_vault_thresholds():
    """Suggest NOVEL/KNOWN thresholds from indexed vault chunk similarities."""
    with INDEX_LOCK:
        store = get_vector_store(settings.vault_path)
        if store.chunk_count() == 0:
            raise HTTPException(status_code=400, detail="Index is empty — index the vault first.")
        calibration = calibrate_thresholds(store)
    return calibration.to_dict()


@router.post("/api/vault/thresholds")
def update_vault_thresholds(request: ThresholdUpdateRequest):
    """Apply validated novelty thresholds and optionally persist them to .env."""
    if request.novel >= request.known:
        raise HTTPException(
            status_code=400,
            detail="Novel threshold must be lower than known threshold.",
        )
    if request.persist:
        try:
            update_env_values(
                FRONTEND_DIR.parent / ".env",
                {
                    "NOVEL_THRESHOLD": str(request.novel),
                    "KNOWN_THRESHOLD": str(request.known),
                },
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not update .env: {exc}") from exc
    settings.novel_threshold = request.novel
    settings.known_threshold = request.known
    return {
        "novel": settings.novel_threshold,
        "known": settings.known_threshold,
        "persisted": request.persist,
    }


@router.get("/api/vault/graph")
def get_graph(highlight: str | None = None):
    with INDEX_LOCK:
        if graph.graph.number_of_nodes() == 0 and settings.graph_cache_path.exists():
            graph.load(settings.graph_cache_path)
        highlight_nodes = [item.strip() for item in (highlight or "").split(",") if item.strip()]
        return graph.to_vis_json(highlight_nodes=highlight_nodes)


@router.post("/api/vault/refresh-notes")
async def refresh_vault_notes(request: RefreshNotesRequest):
    """Re-embed notes already written to disk (e.g. via the Obsidian plugin vault API)."""
    vault_path = _resolve_vault_path(request.vault_path)
    if not request.note_paths:
        raise HTTPException(status_code=400, detail="note_paths is required")

    def _refresh() -> dict:
        import app.main as main_module

        return main_module._refresh_written_notes(vault_path, request.note_paths)

    return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _refresh)
