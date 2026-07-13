"""Tag-aware similarity adjustments for novelty scoring."""

from __future__ import annotations

from app.config import settings


def tag_overlap_boost(query_tags: list[str] | None, match_tags: list[str] | None) -> float:
    """Boost similarity when tag sets overlap (same topic, different wording)."""
    if not settings.tag_similarity_enabled:
        return 0.0
    if not query_tags or not match_tags:
        return 0.0
    query = {tag.casefold() for tag in query_tags if tag}
    match = {tag.casefold() for tag in match_tags if tag}
    overlap = query & match
    if not overlap:
        return 0.0
    boost = len(overlap) * settings.tag_similarity_boost_per_tag
    return min(settings.tag_similarity_max_boost, boost)


def adjusted_similarity(
    base_similarity: float,
    query_tags: list[str] | None,
    match_tags: list[str] | None,
) -> float:
    """Apply tag overlap boost, capped at 1.0."""
    return min(1.0, base_similarity + tag_overlap_boost(query_tags, match_tags))
