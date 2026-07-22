from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.common import _resolve_vault_path
from app.api.schemas import (
    ChatRequest,
)
from app.chat import answer_vault_question
from app.deps import get_vector_store

router = APIRouter()


@router.post("/api/chat")
def chat_with_vault(request: ChatRequest):
    """Retrieve relevant chunks and emit an NDJSON-compatible completed answer."""
    vault = _resolve_vault_path(request.vault_path)
    store = get_vector_store(vault)
    if store.chunk_count() == 0:
        raise HTTPException(status_code=400, detail="Index is empty — index the vault first.")
    try:
        result = answer_vault_question(
            request.question.strip(),
            store,
            source_context=request.source_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vault chat failed: {exc}") from exc
    events = [
        {"type": "citations", "citations": result.citations},
        {"type": "answer", "text": result.answer},
        {"type": "done"},
    ]
    return StreamingResponse(
        iter(json.dumps(event) + "\n" for event in events),
        media_type="application/x-ndjson",
    )
