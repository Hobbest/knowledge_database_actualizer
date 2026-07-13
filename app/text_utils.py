"""Shared text manipulation helpers."""

from __future__ import annotations

import re

from app.sources.base import SourceSegment
from app.text_limits import TEXT_LIMITS

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Dot leaders ("......" / ". . .") and other stray symbol runs that leak in from
# tables of contents or PDF layout artifacts and add no meaning to a note.
_DOT_LEADER_RUN = re.compile(r"\.{4,}|(?:\.[ \t]+){2,}\.")
_SYMBOL_RUN = re.compile(r"[·•▪●◦‣∙*_~=+#\-–—]{3,}")
# A markdown table row/separator -- captured separately as media, so it should
# not leak into extractive prose as a run of pipe characters.
_TABLE_LINE = re.compile(r"^\|.*\|$|^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _drop_table_lines(paragraph: str) -> str:
    return "\n".join(
        line for line in paragraph.splitlines() if not _TABLE_LINE.match(line.strip())
    )


def truncate_with_ellipsis(text: str, max_chars: int) -> str:
    """Trim text to max_chars, appending an ellipsis when truncated."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 3].rstrip() + "..."


def combine_segment_text(segments: list[SourceSegment]) -> str:
    return "\n\n".join(segment.text.strip() for segment in segments if segment.text.strip())


def clean_extractive_text(text: str) -> str:
    """Join hard-wrapped lines into readable paragraphs.

    PDF extraction inserts a newline per visual line, which makes naive
    extractive notes look choppy. Collapse intra-paragraph newlines into spaces
    while keeping blank-line paragraph breaks.
    """
    paragraphs = _PARAGRAPH_SPLIT.split(text)
    cleaned: list[str] = []
    for paragraph in paragraphs:
        joined = re.sub(r"\s*\n\s*", " ", _drop_table_lines(paragraph).strip())
        joined = _DOT_LEADER_RUN.sub(" ", joined)
        joined = _SYMBOL_RUN.sub(" ", joined)
        joined = re.sub(r"[ \t]{2,}", " ", joined).strip()
        if joined:
            cleaned.append(joined)
    return "\n\n".join(cleaned)


def extract_topic_summary(
    segments: list[SourceSegment],
    *,
    max_chars: int | None = None,
) -> str:
    """Build a compact excerpt from one or more source segments."""
    limit = max_chars or TEXT_LIMITS.topic_summary_chars
    return truncate_with_ellipsis(combine_segment_text(segments), limit)


def title_from_text(text: str, *, max_chars: int | None = None) -> str:
    """Derive a short topic title from the first meaningful line."""
    limit = max_chars or TEXT_LIMITS.topic_title_chars

    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        return truncate_with_ellipsis(clean.rstrip("."), limit)

    return truncate_with_ellipsis(text, limit) or "Untitled concept"


def join_excerpts(texts: list[str], *, max_chars: int) -> str:
    """Concatenate text snippets up to a total character budget."""
    if not texts:
        return ""

    parts: list[str] = []
    total = 0
    for text in texts:
        snippet = text.strip()
        if not snippet:
            continue
        if total + len(snippet) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                parts.append(truncate_with_ellipsis(snippet, remaining))
            break
        parts.append(snippet)
        total += len(snippet) + 2

    return "\n\n".join(parts)


def split_text_into_blocks(text: str, *, max_chars: int) -> list[str]:
    """Split text into blocks up to max_chars, preferring natural boundaries.

    Prefers paragraph breaks, then sentence breaks, and only splits mid-text
    when a single unit still exceeds the limit. Unlike ``truncate_with_ellipsis``
    this never discards content -- every character ends up in some block.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    paragraphs = [part.strip() for part in _PARAGRAPH_SPLIT.split(cleaned) if part.strip()]
    if len(paragraphs) > 1:
        return _group_units(paragraphs, max_chars=max_chars, separator="\n\n")

    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(cleaned) if part.strip()]
    if len(sentences) > 1:
        return _group_units(sentences, max_chars=max_chars, separator=" ")

    return _hard_split(cleaned, max_chars)


def _group_units(units: list[str], *, max_chars: int, separator: str) -> list[str]:
    blocks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if buffer:
            blocks.append(separator.join(buffer).strip())
            buffer = []
            buffer_len = 0

    for unit in units:
        if len(unit) > max_chars:
            flush()
            blocks.extend(_hard_split(unit, max_chars))
            continue
        addition = len(unit) + len(separator)
        if buffer and buffer_len + addition > max_chars:
            flush()
        buffer.append(unit)
        buffer_len += addition

    flush()
    return [block for block in blocks if block]


def _hard_split(text: str, max_chars: int) -> list[str]:
    pieces = [text[start : start + max_chars].strip() for start in range(0, len(text), max_chars)]
    return [piece for piece in pieces if piece]
