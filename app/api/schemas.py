from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.auth import validate_suggestion_content, validate_suggestion_note_path
from app.checkpoint import MAX_SUGGESTION_CONTENT_CHARS

# Keep apply-batch bounded so a single request cannot flood the vault writer.
MAX_APPLY_BATCH_NOTES = 100


class VaultIndexRequest(BaseModel):
    vault_path: str | None = None
    if_stale: bool = False


class VaultWatchRequest(BaseModel):
    enabled: bool


class ThresholdUpdateRequest(BaseModel):
    novel: float = Field(ge=0.0, le=1.0)
    known: float = Field(ge=0.0, le=1.0)
    persist: bool = True


class AnalyzeUrlRequest(BaseModel):
    url: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    vault_path: str | None = None
    source_context: str | None = Field(default=None, max_length=20_000)


class ApplySuggestionRequest(BaseModel):
    note_path: str
    content: str
    mode: str = Field(default="write", pattern="^(write|append)$")
    overwrite: bool = False
    vault_path: str | None = None
    append_heading: str | None = None
    source_title: str | None = Field(default=None, max_length=300)

    @field_validator("note_path")
    @classmethod
    def _validate_note_path(cls, value: str) -> str:
        return validate_suggestion_note_path(value)

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        return validate_suggestion_content(value, max_chars=MAX_SUGGESTION_CONTENT_CHARS)


class ApplySuggestionsBatchRequest(BaseModel):
    notes: list[ApplySuggestionRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_APPLY_BATCH_NOTES,
    )
    vault_path: str | None = None
    source_title: str | None = Field(default=None, max_length=300)


class ReportExportRequest(BaseModel):
    result: dict
    format: str = Field(default="markdown", pattern="^(markdown|html)$")
    title: str = Field(default="Actualizer Report", min_length=1, max_length=200)


class RefreshNotesRequest(BaseModel):
    vault_path: str | None = None
    note_paths: list[str] = Field(default_factory=list)
