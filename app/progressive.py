"""Progressive evidence packing for note drafting (Skim → Deep Read → Synthesize).

Builds layered ``EvidencePack`` artifacts from full topic text so LLM and
extractive drafts see mid/late salient claims, not only a prefix truncate.
No extra LLM calls — packing is extractive only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.summarize import (
    _content_words,
    _normalize,
    _score_sentences,
    _split_sentences,
    compose_title,
    refine_note_body,
    summarize_text,
)
from app.text_limits import TEXT_LIMITS
from app.text_utils import clean_extractive_text, truncate_with_ellipsis
from app.titling import title_body_coherence, title_is_grounded

EVIDENCE_PACK_VERSION = "1"

_DEFINITIONAL = re.compile(
    r"\b(?:is|are|was|were|refers\s+to|means|denotes)\s+(?:a|an|the)\b",
    re.IGNORECASE,
)
_NUMBERISH = re.compile(
    r"(?:\d+(?:\.\d+)?%?|\b(?:zero|one|two|three|four|five|ten|hundred|thousand)\b)",
    re.IGNORECASE,
)
_CAVEAT = re.compile(
    r"\b(?:however|although|limitation|constraint|caveat|unless|except|"
    r"trade-?off|drawback|risk|cannot|must not|should not)\b",
    re.IGNORECASE,
)
_CONCLUSION = re.compile(
    r"\b(?:in conclusion|therefore|thus|overall|finally|in summary|"
    r"as a result|consequently)\b",
    re.IGNORECASE,
)
_METHODISH = re.compile(
    r"\b(?:algorithm|method|technique|optimizer|schedule|architecture|"
    r"protocol|theorem|lemma|formula|equation)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidencePack:
    """Layered progressive-summarization evidence for one atomic topic."""

    l3_executive: str
    l2_essentials: tuple[str, ...]
    l1_salient: tuple[str, ...]
    media_hints: tuple[str, ...] = ()
    # When True, format_for_prompt emits a single executive block (tiny budgets).
    compact: bool = False


def _priority_bonus(sentence: str) -> float:
    """Boost Deep Read ranking for definitions, numbers, caveats, conclusions."""
    bonus = 0.0
    if _DEFINITIONAL.search(sentence):
        bonus += 1.5
    if _NUMBERISH.search(sentence):
        bonus += 0.8
    if _CAVEAT.search(sentence):
        bonus += 1.0
    if _CONCLUSION.search(sentence):
        bonus += 0.7
    if _METHODISH.search(sentence):
        bonus += 0.5
    return bonus


def _rank_sentences(sentences: list[str]) -> list[tuple[int, float]]:
    base = _score_sentences(sentences)
    ranked = [
        (index, base[index] + _priority_bonus(sentences[index]))
        for index in range(len(sentences))
    ]
    ranked.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    return ranked


def _clip_sentence(sentence: str, *, max_chars: int | None = None) -> str:
    limit = max_chars or TEXT_LIMITS.fallback_bullet_chars
    cleaned = re.sub(r"\s+", " ", (sentence or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return truncate_with_ellipsis(cleaned, limit)


def _executive_from_planner(planner_summary: str | None, body: str) -> str | None:
    summary = (planner_summary or "").strip()
    if not summary:
        return None
    if not body.strip():
        return truncate_with_ellipsis(summary, TEXT_LIMITS.summary_max_chars)
    if title_is_grounded(summary, body):
        return truncate_with_ellipsis(summary, TEXT_LIMITS.summary_max_chars)
    if title_body_coherence(summary, body) >= TEXT_LIMITS.title_coherence_floor:
        return truncate_with_ellipsis(summary, TEXT_LIMITS.summary_max_chars)
    return None


def _pick_layers(
    sentences: list[str],
    *,
    exclude_norms: set[str],
) -> tuple[list[str], list[str]]:
    """Select L2 essentials then L1 salient passages (reading order within each)."""
    if not sentences:
        return [], []

    ranked = _rank_sentences(sentences)
    seen = set(exclude_norms)
    essentials: list[str] = []
    salient: list[str] = []
    l2_max = TEXT_LIMITS.evidence_l2_max
    l1_max = TEXT_LIMITS.evidence_l1_max
    min_chars = TEXT_LIMITS.key_point_min_chars

    for index, _score in ranked:
        sentence = sentences[index]
        if len(sentence) < min_chars:
            continue
        norm = _normalize(sentence)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        clipped = _clip_sentence(sentence)
        if len(essentials) < l2_max:
            essentials.append(clipped)
        elif len(salient) < l1_max:
            salient.append(clipped)
        else:
            break

    order = {sentence: i for i, sentence in enumerate(sentences)}

    def reading_key(item: str) -> int:
        # Match against originals by normalized form when clipped.
        for original, idx in order.items():
            if _normalize(original) == _normalize(item) or original.startswith(
                item.rstrip("…")[:40]
            ):
                return idx
        return 0

    essentials.sort(key=reading_key)
    salient.sort(key=reading_key)
    return essentials, salient


def build_evidence_pack(
    topic_text: str,
    *,
    planner_summary: str | None = None,
    title: str | None = None,
    media_hints: list[str] | tuple[str, ...] | None = None,
) -> EvidencePack:
    """Build progressive layers from the full topic body (extractive Deep Read)."""
    del title  # Reserved for future skim outlines; L3 uses planner_summary + body.
    cleaned = clean_extractive_text(topic_text or "")
    sentences = _split_sentences(cleaned)

    l3 = _executive_from_planner(planner_summary, cleaned)
    if not l3:
        l3 = summarize_text(
            cleaned,
            max_sentences=TEXT_LIMITS.evidence_l3_max_sentences,
            max_chars=TEXT_LIMITS.summary_max_chars,
        )
    if not l3.strip():
        l3 = (planner_summary or "").strip() or "No extractable summary."

    exclude = {_normalize(part) for part in _split_sentences(l3)}
    essentials, salient = _pick_layers(sentences, exclude_norms=exclude)
    # When L3 already covers every sentence (short topics), still surface
    # essentials for progressive note bullets / prompt claims.
    if not essentials and not salient and sentences:
        essentials, salient = _pick_layers(sentences, exclude_norms=set())
        # Prefer not to duplicate the entire executive as the only essential.
        if (
            len(essentials) == 1
            and _normalize(essentials[0]) == _normalize(l3)
            and len(sentences) > 1
        ):
            essentials, salient = _pick_layers(
                sentences[1:] if len(sentences) > 1 else sentences,
                exclude_norms=set(),
            )
            if not essentials:
                essentials, salient = _pick_layers(sentences, exclude_norms=set())

    hints: tuple[str, ...] = ()
    if media_hints:
        hints = tuple(
            truncate_with_ellipsis(hint.strip(), TEXT_LIMITS.media_caption_chars)
            for hint in media_hints
            if hint and hint.strip()
        )

    return EvidencePack(
        l3_executive=l3.strip(),
        l2_essentials=tuple(essentials),
        l1_salient=tuple(salient),
        media_hints=hints,
    )


@dataclass(frozen=True)
class EvidencePack:
    """Layered progressive-summarization evidence for one atomic topic."""

    l3_executive: str
    l2_essentials: tuple[str, ...]
    l1_salient: tuple[str, ...]
    media_hints: tuple[str, ...] = ()
    # When True, format_for_prompt emits a single executive block (tiny budgets).
    compact: bool = False


def pack_to_budget(pack: EvidencePack, max_chars: int) -> EvidencePack:
    """Drop L1 then L2 (then trim L3) until ``format_for_prompt`` fits ``max_chars``."""
    if max_chars <= 0:
        return EvidencePack(
            l3_executive="",
            l2_essentials=(),
            l1_salient=(),
            media_hints=(),
            compact=True,
        )

    current = replace(pack, compact=False)
    while len(format_for_prompt(current)) > max_chars and current.media_hints:
        current = replace(current, media_hints=current.media_hints[:-1])

    while len(format_for_prompt(current)) > max_chars and current.l1_salient:
        current = replace(current, l1_salient=current.l1_salient[:-1])

    while len(format_for_prompt(current)) > max_chars and current.l2_essentials:
        current = replace(current, l2_essentials=current.l2_essentials[:-1])

    while len(format_for_prompt(current)) > max_chars and current.l3_executive:
        overflow = len(format_for_prompt(current)) - max_chars
        target = max(1, len(current.l3_executive) - overflow - 3)
        if target >= len(current.l3_executive):
            target = max(1, len(current.l3_executive) - 8)
        trimmed = truncate_with_ellipsis(current.l3_executive, target)
        if trimmed == current.l3_executive:
            trimmed = current.l3_executive[: max(1, target)].rstrip()
        if trimmed == current.l3_executive:
            break
        current = replace(current, l3_executive=trimmed)

    if len(format_for_prompt(current)) <= max_chars:
        return current

    # Extreme budgets: compact single-block format that can always shrink to fit.
    prefix = "Executive summary (use as concept spine):\n"
    if max_chars <= len(prefix):
        return EvidencePack(
            l3_executive=(current.l3_executive or "")[:max_chars],
            l2_essentials=(),
            l1_salient=(),
            media_hints=(),
            compact=True,
        )

    room = max(1, max_chars - len(prefix))
    stub = truncate_with_ellipsis(current.l3_executive or "…", room)
    compact = EvidencePack(
        l3_executive=stub,
        l2_essentials=(),
        l1_salient=(),
        media_hints=(),
        compact=True,
    )
    while len(format_for_prompt(compact)) > max_chars and len(compact.l3_executive) > 1:
        compact = replace(compact, l3_executive=compact.l3_executive[:-1].rstrip())
    return compact


def format_for_prompt(pack: EvidencePack) -> str:
    """Serialize an evidence pack into structured draft-prompt blocks."""
    if pack.compact:
        body = (pack.l3_executive or "").strip()
        if not body:
            return ""
        labeled = f"Executive summary (use as concept spine):\n{body}\n"
        # Pathological: budget smaller than the label — emit the raw clip only.
        if len(labeled) > len(body) and len(body) < len(
            "Executive summary (use as concept spine):\n"
        ):
            return body if body.endswith("\n") else body + "\n"
        return labeled

    lines = [
        "Executive summary (use as concept spine):",
        pack.l3_executive.strip() or "(none)",
        "",
        "Essential claims (must be reflected if supported):",
    ]
    if pack.l2_essentials:
        lines.extend(f"- {item}" for item in pack.l2_essentials)
    else:
        lines.append("- (none)")

    lines += ["", "Salient source passages (quote or paraphrase; do not invent beyond these):"]
    if pack.l1_salient:
        lines.extend(f"- {item}" for item in pack.l1_salient)
    else:
        lines.append("- (none)")

    if pack.media_hints:
        lines += ["", "Media hints:"]
        lines.extend(f"- {item}" for item in pack.media_hints)

    return "\n".join(lines).strip() + "\n"


def _nucleus_and_clause(sentence: str) -> tuple[str, str]:
    words = re.findall(r"[A-Za-z0-9']+", sentence or "")
    if not words:
        return "Point", sentence.strip()
    limit = TEXT_LIMITS.evidence_nucleus_max_words
    # Prefer a short content-word nucleus when possible.
    content = _content_words(sentence)
    if content:
        nucleus_words = content[: min(limit, max(2, len(content) // 2 or 1))]
        nucleus = " ".join(w[:1].upper() + w[1:] for w in nucleus_words)
    else:
        nucleus = " ".join(words[:limit])
        nucleus = nucleus[:1].upper() + nucleus[1:] if nucleus else "Point"

    remainder = sentence.strip()
    # Strip a matching lead-in so the clause does not repeat the nucleus verbatim.
    lead = " ".join(words[: len(nucleus.split())])
    if remainder.lower().startswith(lead.lower()):
        clause = remainder[len(lead) :].lstrip(" ,:—-–")
    else:
        clause = remainder
    if not clause:
        clause = remainder
    return nucleus, clause


def render_progressive_note(title: str, pack: EvidencePack) -> str:
    """Render a light progressive note body (Related/Source added by draft later)."""
    heading = compose_title(title or "Untitled concept")
    sections: list[str] = [f"# {heading}", ""]

    executive = (pack.l3_executive or "").strip()
    if executive:
        sections += [f"> {executive}", ""]

    # One short definition-ish line: prefer the first definitional essential.
    definition = ""
    for item in pack.l2_essentials:
        if _DEFINITIONAL.search(item):
            definition = item
            break
    if not definition and pack.l2_essentials:
        definition = pack.l2_essentials[0]
    if definition:
        sections += [definition, ""]

    bullets_src = list(pack.l2_essentials)
    if not bullets_src:
        bullets_src = list(pack.l1_salient)
    # Avoid duplicating the definition paragraph as the first bullet.
    if definition and bullets_src and _normalize(bullets_src[0]) == _normalize(definition):
        bullets_src = bullets_src[1:]

    if bullets_src:
        sections += ["## Key points", ""]
        for item in bullets_src:
            nucleus, clause = _nucleus_and_clause(item)
            if clause and clause.casefold() != nucleus.casefold():
                sections.append(f"- **{nucleus}** — {clause}")
            else:
                sections.append(f"- **{nucleus}**")

    return refine_note_body("\n".join(sections))


__all__ = [
    "EVIDENCE_PACK_VERSION",
    "EvidencePack",
    "build_evidence_pack",
    "format_for_prompt",
    "pack_to_budget",
    "render_progressive_note",
]
