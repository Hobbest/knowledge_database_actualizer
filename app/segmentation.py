"""Normalize source segments into bounded planning units.

Loaders emit one segment per natural unit (a PDF page, a transcript window, a
markdown section). For large units -- especially full PDF pages -- a single
segment can hold many distinct concepts. This module splits oversized segments
into smaller planning units while preserving each unit's source location, so
every concept can become its own atomic note and citations stay checkable.
"""

from __future__ import annotations

from app.sources.base import SourceSegment
from app.text_utils import split_text_into_blocks


def split_large_segments(
    segments: list[SourceSegment],
    *,
    target_chars: int,
) -> list[SourceSegment]:
    """Split segments longer than ``target_chars`` into location-preserving pieces."""
    if target_chars <= 0:
        return _reindex(
            SourceSegment(text=segment.text.strip(), location=segment.location, index=0)
            for segment in segments
            if segment.text.strip()
        )

    result: list[SourceSegment] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if len(text) <= target_chars:
            result.append(SourceSegment(text=text, location=segment.location, index=0))
            continue
        for piece in split_text_into_blocks(text, max_chars=target_chars):
            result.append(SourceSegment(text=piece, location=segment.location, index=0))

    return _reindex(result)


def _reindex(segments) -> list[SourceSegment]:
    ordered = list(segments)
    for index, segment in enumerate(ordered):
        segment.index = index
    return ordered
