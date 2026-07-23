"""Named limits for text truncation and note generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextLimits:
    """Character and count limits used across planning, drafting, and API previews."""

    # Short excerpt stored on each AtomicTopic during structural planning.
    topic_summary_chars: int = 500

    # Auto-generated topic title derived from the first meaningful line.
    topic_title_chars: int = 72

    # Body-grounded titling (app/titling.py).
    title_min_words: int = 3
    title_max_words: int = 8
    # Keep a heading/hint only when content-word overlap with the body meets this.
    title_heading_jaccard: float = 0.25
    # Below this coherence score, fall back to a phrase from summarize_text.
    title_coherence_floor: float = 0.12

    # Source excerpt passed to the LLM when drafting a single note body.
    note_draft_excerpt_chars: int = 2000

    # Per-topic evidence budget in batch draft prompts (was magic 700).
    batch_draft_excerpt_chars: int = 700

    # Progressive EvidencePack layer sizes (app/progressive.py).
    evidence_l2_max: int = 7
    evidence_l1_max: int = 10
    evidence_l3_max_sentences: int = 3
    evidence_nucleus_max_words: int = 6

    # Fallback bullet text when extractive parsing finds no list items.
    fallback_bullet_chars: int = 220

    # Maximum bullets in extractive fallback notes.
    fallback_bullet_count: int = 5

    # Extractive summary sizing for structural (non-LLM) note bodies.
    summary_sentence_count: int = 3
    summary_max_chars: int = 600

    # Minimum length for a sentence to qualify as a distinct key point.
    key_point_min_chars: int = 30

    # Maximum wikilinks attached to a drafted note.
    related_link_count: int = 5

    # Segments included in the LLM topic-planning outline.
    llm_planning_max_segments: int = 400

    # Per-segment text included in the LLM topic-planning outline.
    # Should be >= segment_target_chars so the planner sees whole planning units.
    llm_planning_segment_chars: int = 1600

    # Total outline character budget for a single planning request. When the
    # source is larger than this, the LLM planner is skipped and structural
    # planning (which always covers the full source) is used instead.
    llm_planning_total_chars_budget: int = 120_000

    # Target summary length in words for LLM topic planning responses.
    llm_planning_summary_words: int = 120

    # Tables & figures embedded in notes.
    media_items_per_note: int = 4
    media_table_max_rows: int = 12
    media_caption_chars: int = 200

    # API response previews.
    api_chunk_preview_chars: int = 240
    api_overlap_preview_chars: int = 300
    api_chunk_list_limit: int = 20


TEXT_LIMITS = TextLimits()
