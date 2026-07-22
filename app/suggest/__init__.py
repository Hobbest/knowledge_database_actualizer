"""Suggestion drafting, planning, and vault apply — stable public façade."""

from __future__ import annotations

import os

from app.config import settings
from app.llm import call_with_retry, get_llm_provider
from app.suggest.apply import (
    _atomic_write_text,
    _backup_existing_note,
    _resolve_vault_target,
    apply_suggestion,
    apply_suggestions,
    preview_suggestion_merge,
)
from app.suggest.draft import (
    _apply_analyze_in_place,
    _batch_draft_prompt_chars,
    _build_frontmatter,
    _build_moc_suggestion,
    _build_suggestion,
    _infer_note_tags,
    _inject_related_links,
    _link_sibling_notes,
    _llm_draft_topic_body,
    _llm_draft_topics_batch,
    _note_identity,
    _parse_delimited_notes,
    _related_links,
    _source_section,
    _strip_related_notes_section,
    _topic_related_links,
    draft_note_suggestion,
    draft_note_suggestions,
    iter_note_suggestions,
)
from app.suggest.models import (
    ApplyNoteResult,
    MergedNotePreview,
    NoteSuggestion,
    ProgressFn,
)
from app.suggest.plan import (
    _plan_topics,
    _source_meta,
    _stream_plan_topics,
    analysis_fingerprint,
    topics_from_checkpoint,
    topics_to_checkpoint,
)

__all__ = [
    "ApplyNoteResult",
    "MergedNotePreview",
    "NoteSuggestion",
    "ProgressFn",
    "analysis_fingerprint",
    "apply_suggestion",
    "apply_suggestions",
    "call_with_retry",
    "draft_note_suggestion",
    "draft_note_suggestions",
    "get_llm_provider",
    "iter_note_suggestions",
    "os",
    "preview_suggestion_merge",
    "settings",
    "topics_from_checkpoint",
    "topics_to_checkpoint",
    # Private helpers re-exported for tests / main lazy imports
    "_apply_analyze_in_place",
    "_atomic_write_text",
    "_backup_existing_note",
    "_batch_draft_prompt_chars",
    "_build_frontmatter",
    "_build_moc_suggestion",
    "_build_suggestion",
    "_infer_note_tags",
    "_inject_related_links",
    "_link_sibling_notes",
    "_llm_draft_topic_body",
    "_llm_draft_topics_batch",
    "_note_identity",
    "_parse_delimited_notes",
    "_plan_topics",
    "_related_links",
    "_resolve_vault_target",
    "_source_meta",
    "_source_section",
    "_stream_plan_topics",
    "_strip_related_notes_section",
    "_topic_related_links",
]
