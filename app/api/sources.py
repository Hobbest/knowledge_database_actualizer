from __future__ import annotations

import threading
from queue import Queue

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.common import (
    _iter_analyze_events,
    _ndjson_worker,
    _queue_to_async_stream,
    _read_upload_bounded,
    _resolve_vault_path,
)
from app.config import settings
from app.runtime import ANALYZE_POOL, try_acquire_analyze_slot

router = APIRouter()


@router.post("/api/sources/analyze")
async def analyze_source(
    url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    resume: bool = Form(default=False),
    vault_note_path: str | None = Form(default=None),
    vault_path: str | None = Form(default=None),
):
    active_vault = _resolve_vault_path(vault_path)

    if not url and not file:
        raise HTTPException(status_code=400, detail="Provide either url or file")

    # The worker thread must not touch UploadFile. Stop reading immediately at the cap.
    file_bytes = await _read_upload_bounded(file, settings.max_upload_mb) if file else None
    file_name = (file.filename or "upload.txt") if file else None
    clean_url = url.strip() if url else None

    if not try_acquire_analyze_slot(settings.analyze_max_in_flight):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many analyze runs in flight "
                f"(limit {settings.analyze_max_in_flight}). "
                "Retry after an active analysis finishes."
            ),
            headers={"Retry-After": "30"},
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
            vault_path=active_vault,
        )

    try:
        ANALYZE_POOL.submit(_ndjson_worker, queue, iterator_factory, cancelled)
    except Exception:
        from app.runtime import release_analyze_slot

        release_analyze_slot()
        raise
    return StreamingResponse(
        _queue_to_async_stream(queue, cancelled),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
