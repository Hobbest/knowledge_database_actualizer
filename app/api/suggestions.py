from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.api.common import (
    _git_commit_for_paths,
    _record_analytics,
    _resolve_vault_path,
)
from app.api.schemas import ApplySuggestionRequest, ApplySuggestionsBatchRequest
from app.checkpoint import (
    export_checkpoints,
    import_checkpoints,
    load_checkpoint_by_key,
    load_latest_checkpoint,
)
from app.runtime import WORKER_POOL
from app.suggest import apply_suggestion, apply_suggestions, preview_suggestion_merge

router = APIRouter()


@router.get("/api/suggestions/checkpoint")
def get_suggestions_checkpoint(source_key: str | None = None):
    data = load_checkpoint_by_key(source_key) if source_key else load_latest_checkpoint()
    if not data:
        return {"exists": False}
    return {"exists": True, **data}


@router.get("/api/suggestions/checkpoint/export")
def export_suggestion_checkpoints(source_key: str | None = None):
    """Download one checkpoint or a bundle of all checkpoint history."""
    payload = export_checkpoints(source_key)
    if source_key and not payload["checkpoints"]:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="actualizer-checkpoints.json"'
        },
    )


@router.post("/api/suggestions/checkpoint/import")
def import_suggestion_checkpoints(payload: dict):
    """Import a validated checkpoint bundle."""
    try:
        return import_checkpoints(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/suggestions/apply-batch")
async def apply_note_suggestions_batch(request: ApplySuggestionsBatchRequest):
    vault_path = _resolve_vault_path(request.vault_path)

    if not request.notes:
        raise HTTPException(status_code=400, detail="No notes provided")

    def _apply_and_refresh() -> dict:
        import app.main as main_module

        results = apply_suggestions(
            vault_path=vault_path,
            notes=[note.model_dump() for note in request.notes],
        )
        written_paths = [r.written_path for r in results if r.written_path]
        _record_analytics(written_notes=len(written_paths))
        skipped_existing = [r.note_path for r in results if r.status == "skipped_exists"]
        errors = [
            {"note_path": r.note_path, "error": r.error}
            for r in results
            if r.status == "error"
        ]
        index_refresh = main_module._refresh_written_notes(vault_path, written_paths)
        git_commit = _git_commit_for_paths(
            vault_path, written_paths, request.source_title
        )
        return {
            "results": [r.to_dict() for r in results],
            "written_paths": written_paths,
            "count": len(written_paths),
            "skipped_existing": skipped_existing,
            "errors": errors,
            "index_refresh": index_refresh,
            "git_commit": git_commit,
            "vault_path": str(vault_path),
        }

    return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _apply_and_refresh)


@router.post("/api/suggestions/apply")
async def apply_note_suggestion(request: ApplySuggestionRequest):
    vault_path = _resolve_vault_path(request.vault_path)

    def _apply_one() -> dict:
        import app.main as main_module

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
            _record_analytics(written_notes=1)
            index_refresh = main_module._refresh_written_notes(
                vault_path, [result.written_path]
            )
        git_commit = _git_commit_for_paths(
            vault_path,
            [result.written_path] if result.written_path else [],
            request.source_title,
        )
        return {
            "written_path": result.written_path,
            "mode": request.mode,
            "status": result.status,
            "overwritten": result.overwritten,
            "backup_path": result.backup_path,
            "index_refresh": index_refresh,
            "git_commit": git_commit,
            "vault_path": str(vault_path),
        }

    try:
        return await asyncio.get_running_loop().run_in_executor(WORKER_POOL, _apply_one)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/suggestions/preview")
async def preview_note_suggestion(request: ApplySuggestionRequest):
    """Preview the exact final note content without writing or refreshing the index."""
    vault_path = _resolve_vault_path(request.vault_path)
    try:
        preview = preview_suggestion_merge(
            vault_path=vault_path,
            note_path=request.note_path,
            content=request.content,
            mode=request.mode,
            overwrite=request.overwrite,
            append_heading=request.append_heading,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**preview.to_dict(), "vault_path": str(vault_path)}
