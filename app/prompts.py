"""Centralized LLM prompt templates for note planning and drafting."""

from __future__ import annotations

import json

from app.novelty import NoveltyResult
from app.sources.base import LoadedSource
from app.text_limits import TEXT_LIMITS

ARCHITECT_SYSTEM_PROMPT = (
    "You are an Obsidian knowledge architect. Produce comprehensive atomic note plans. "
    "Respond with JSON only."
)

NOTE_WRITER_SYSTEM_PROMPT = (
    "You write atomic, compact, factual Obsidian notes. One concept per note. "
    "Every claim should be traceable to the provided excerpt."
)

ATOMIC_NOTE_RULES = (
    "Explain exactly one concept per note",
    "Keep notes compact and specific; avoid generic filler",
    "Prefer precise terminology over vague phrases like 'fundamental component'",
    "Use markdown headings",
    "Include a short definition and 2-5 bullet points",
    "Add a 'Related notes' section with [[wikilinks]] when useful",
    "Do not include YAML frontmatter",
    "Do not include a Source section (it will be appended automatically)",
    "Provide examples when the excerpt supports them",
)

TOPIC_PLANNING_RULES = (
    "Cover every distinct concept discussed in the source",
    "One concept per item; do not merge unrelated ideas",
    "Reuse segment indices exactly as provided",
    "Prefer more smaller notes over fewer large notes",
    f"Keep summaries compact (no more than {TEXT_LIMITS.llm_planning_summary_words} words)",
    "Be precise and specific; avoid generic summaries",
)


def _format_rules(rules: tuple[str, ...]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def topic_planning_prompt(
    *,
    source: LoadedSource,
    segment_outline: list[dict],
    target_min_notes: int,
    novelty: NoveltyResult,
) -> str:
    return (
        "Split this source material into atomic knowledge notes.\n\n"
        f"Source title: {source.title}\n"
        f"Source type: {source.source_type}\n"
        f"Segment count: {len(segment_outline)}\n"
        f"Overall verdict: {novelty.verdict.value}\n"
        f"Target note count: at least {target_min_notes}, ideally one note per distinct concept\n\n"
        "Source segments with location:\n"
        f"{json.dumps(segment_outline, indent=2)}\n\n"
        "Return ONLY valid JSON array. Each item must have:\n"
        '- "title": short concept title (one concept only)\n'
        '- "segment_indices": list of segment index integers to include\n'
        '- "summary": compact 2-5 sentence explanation of the single concept\n\n'
        "Rules:\n"
        f"{_format_rules(TOPIC_PLANNING_RULES)}\n"
    )


def batch_note_draft_prompt(
    *,
    source: LoadedSource,
    topics: list[dict],
    related_links: list[str],
    max_note_lines: int,
) -> str:
    links_text = ", ".join(related_links) if related_links else "none"
    return (
        "Write atomic Obsidian notes for each concept below.\n\n"
        f"Source: {source.title} ({source.source_type})\n"
        f"Related notes: {links_text}\n\n"
        "Concepts:\n"
        f"{json.dumps(topics, indent=2)}\n\n"
        "Return ONLY valid JSON array. Each item must have:\n"
        '- "title": must match the input title exactly\n'
        '- "body": markdown note body (no YAML frontmatter, no Source section)\n\n'
        "Requirements:\n"
        f"{_format_rules(ATOMIC_NOTE_RULES)}\n"
        f"- Keep each note compact (under {max_note_lines} lines)\n"
    )


def note_draft_prompt(
    *,
    source: LoadedSource,
    concept_title: str,
    location_display: str,
    excerpt: str,
    related_links: list[str],
    max_note_lines: int,
) -> str:
    links_text = ", ".join(related_links) if related_links else "none"
    return (
        f"Write one atomic Obsidian note for the concept '{concept_title}'.\n\n"
        f"Source: {source.title} ({source.source_type})\n"
        f"Checkable location in source: {location_display}\n"
        f"Related notes: {links_text}\n\n"
        f"Source excerpt:\n{excerpt}\n\n"
        "Requirements:\n"
        f"{_format_rules(ATOMIC_NOTE_RULES)}\n"
        f"- Keep the note compact (under {max_note_lines} lines)\n"
    )
