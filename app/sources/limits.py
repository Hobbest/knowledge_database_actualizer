"""Shared soft caps for in-process source extraction."""

from __future__ import annotations

from app.sources.base import SourceSegment


def truncate_segments_to_char_cap(
    segments: list[SourceSegment],
    *,
    max_chars: int,
) -> tuple[list[SourceSegment], str | None]:
    """Keep segments in order until *max_chars* of segment text is reached.

    Returns the (possibly shorter) segment list and an optional warning.
    ``max_chars <= 0`` means unlimited.
    """
    if max_chars <= 0 or not segments:
        return segments, None

    kept: list[SourceSegment] = []
    total = 0
    truncated = False
    for segment in segments:
        text = segment.text or ""
        if total >= max_chars:
            truncated = True
            break
        room = max_chars - total
        if len(text) <= room:
            segment.index = len(kept)
            kept.append(segment)
            total += len(text)
            continue
        kept.append(
            SourceSegment(
                text=text[:room],
                location=segment.location,
                index=len(kept),
            )
        )
        truncated = True
        break

    if not truncated:
        return segments, None
    return kept, (
        f"Extracted text truncated to {max_chars:,} characters "
        f"(MAX_SOURCE_CHARS); later content was skipped."
    )
