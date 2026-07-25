"""ApplySuggestionRequest path/content/batch validators."""

from __future__ import annotations

import pytest
from app.api.schemas import (
    MAX_APPLY_BATCH_NOTES,
    ApplySuggestionRequest,
    ApplySuggestionsBatchRequest,
)
from app.checkpoint import MAX_SUGGESTION_CONTENT_CHARS
from pydantic import ValidationError


def test_apply_request_rejects_unsafe_note_path():
    with pytest.raises(ValidationError, match="note_path"):
        ApplySuggestionRequest(note_path="../escape.md", content="body")


def test_apply_request_rejects_oversized_content():
    with pytest.raises(ValidationError, match="characters"):
        ApplySuggestionRequest(
            note_path="notes/a.md",
            content="x" * (MAX_SUGGESTION_CONTENT_CHARS + 1),
        )


def test_apply_request_accepts_safe_payload():
    req = ApplySuggestionRequest(note_path="notes/a.md", content="hello")
    assert req.note_path == "notes/a.md"
    assert req.content == "hello"


def test_apply_batch_rejects_empty_and_oversized():
    with pytest.raises(ValidationError):
        ApplySuggestionsBatchRequest(notes=[])

    too_many = [
        ApplySuggestionRequest(note_path=f"n{i}.md", content="x")
        for i in range(MAX_APPLY_BATCH_NOTES + 1)
    ]
    with pytest.raises(ValidationError):
        ApplySuggestionsBatchRequest(notes=too_many)
