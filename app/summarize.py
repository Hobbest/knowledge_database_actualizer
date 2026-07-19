"""Extractive summarization, title composition, and note refinement.

Structural note drafting used to just concatenate and truncate raw source text,
which produced repetitive, ragged notes (the "Summary" and "Key points" sections
were near-duplicates, titles were raw line fragments, and spacing was uneven).

This module centralizes the text-quality logic so both the extractive fallback
and the final assembly step produce consistent, readable notes:

* ``summarize_text``  -- frequency-scored extractive summary (the most
  representative sentences, kept in reading order).
* ``key_points``      -- salient sentences that *complement* the summary rather
  than repeat it.
* ``compose_title``   -- normalize a heading/fragment into a clean concept title.
* ``refine_note_body``-- final pass: consistent heading spacing, de-duplicated
  bullets, collapsed blank lines.
"""

from __future__ import annotations

import re
from collections import Counter

from app.text_utils import clean_extractive_text, truncate_with_ellipsis

_WORD = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DOT_LEADER = re.compile(r"\.{4,}|(?:\.[ \t]+){2,}\.")

# Section/enumeration prefixes and part markers that clutter derived titles.
_TITLE_NUMBERING = re.compile(
    r"^\s*(?:chapter|part|section|appendix|unit|lesson)\s+[\divxlcdm]+[\s:.\-\u2013)]*",
    re.IGNORECASE,
)
# A leading section number: dotted ("1.2") or an integer followed by punctuation
# ("3:" / "4)"). A bare "2020 " is deliberately left alone.
_TITLE_ENUM = re.compile(r"^\s*(?:\d+(?:\.\d+)+[\s:.\-\u2013)]*|\d+\s*[:.\-\u2013)]+\s*)")
_PART_SUFFIX = re.compile(r"\s*\((?:part\s*)?\d+\)\s*$", re.IGNORECASE)

# Small, dependency-free stopword list for frequency scoring.
_STOPWORDS = frozenset(
    """a an the and or but if then else when at by for with about against between into
    through during before after above below to from up down in out on off over under
    again further once here there all any both each few more most other some such no nor
    not only own same so than too very can will just should now is are was were be been
    being have has had do does did of as it its it's this that these those i you he she
    they we me him her them my your his our their what which who whom whose how why one
    also may might must could would shall them then""".split()
)


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def _content_words(sentence: str) -> list[str]:
    return [
        word
        for word in _WORD.findall(sentence.lower())
        if len(word) > 2 and word not in _STOPWORDS
    ]


def _score_sentences(sentences: list[str]) -> list[float]:
    """Score each sentence by normalized content-word frequency.

    Words that recur across the text mark central ideas; dividing by sqrt(length)
    keeps long sentences from dominating purely on word count.
    """
    freq: Counter[str] = Counter()
    for sentence in sentences:
        freq.update(_content_words(sentence))

    if not freq:
        return [0.0] * len(sentences)

    peak = max(freq.values())
    scores: list[float] = []
    for sentence in sentences:
        words = _content_words(sentence)
        if not words:
            scores.append(0.0)
            continue
        raw = sum(freq[word] / peak for word in words)
        scores.append(raw / (len(words) ** 0.5))
    return scores


def _normalize(sentence: str) -> str:
    return " ".join(_WORD.findall(sentence.lower()))


def summarize_text(text: str, *, max_sentences: int = 3, max_chars: int | None = None) -> str:
    """Return an extractive summary: the top-scoring sentences in reading order."""
    cleaned = clean_extractive_text(text)
    sentences = _split_sentences(cleaned)
    if not sentences:
        return cleaned.strip()
    if len(sentences) <= max_sentences:
        summary = " ".join(sentences)
    else:
        scores = _score_sentences(sentences)
        ranked = sorted(range(len(sentences)), key=lambda i: (scores[i], -i), reverse=True)
        chosen = sorted(ranked[:max_sentences])
        summary = " ".join(sentences[i] for i in chosen)
    if max_chars:
        summary = truncate_with_ellipsis(summary, max_chars)
    return summary


def key_points(
    text: str,
    *,
    max_points: int = 5,
    min_chars: int = 25,
    exclude: str | None = None,
) -> list[str]:
    """Return salient sentences that complement (do not repeat) ``exclude``."""
    cleaned = clean_extractive_text(text)
    sentences = _split_sentences(cleaned)
    if not sentences:
        return []

    scores = _score_sentences(sentences)
    order = {sentence: index for index, sentence in enumerate(sentences)}
    seen: set[str] = {_normalize(part) for part in _split_sentences(exclude or "")}

    ranked = sorted(range(len(sentences)), key=lambda i: (scores[i], -i), reverse=True)
    chosen: list[str] = []
    for index in ranked:
        sentence = sentences[index]
        if len(sentence) < min_chars:
            continue
        norm = _normalize(sentence)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        chosen.append(sentence)
        if len(chosen) >= max_points:
            break

    # Present them in the order they appear in the source for readability.
    chosen.sort(key=lambda sentence: order.get(sentence, 0))
    return chosen


def compose_title(raw: str, *, max_chars: int = 72) -> str:
    """Normalize a heading or line fragment into a clean concept title."""
    text = (raw or "").strip()

    # Preserve a trailing "(part 2)" / "(3)" marker added during splitting.
    part_suffix = ""
    match = _PART_SUFFIX.search(text)
    if match:
        number = re.search(r"\d+", match.group())
        if number:
            part_suffix = f" (part {number.group()})"
        text = text[: match.start()].strip()

    text = text.lstrip("#>*-•\u2013 \t").strip()
    text = _DOT_LEADER.sub(" ", text)
    text = re.sub(r"\s+\d{1,4}$", "", text)  # trailing page number left by a ToC-ish line
    text = _TITLE_NUMBERING.sub("", text)
    text = _TITLE_ENUM.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .:-\u2013\t")

    if not text:
        text = "Untitled concept"

    if len(text) > max_chars:
        clipped = text[:max_chars].rsplit(" ", 1)[0].strip()
        text = clipped or text[:max_chars].strip()

    if text[:1].islower():
        text = text[0].upper() + text[1:]

    return f"{text}{part_suffix}"


_BULLET = re.compile(r"^[-*]\s+(.*)$")
_HEADING = re.compile(r"^#{1,6}\s+\S")


def ensure_concept_heading(body: str, title: str) -> str:
    """Force the note body to start with ``# {title}`` (replace or prepend H1)."""
    concept = (title or "").strip() or "Untitled concept"
    text = (body or "").replace("\r\n", "\n").lstrip("\n")
    if not text.strip():
        return f"# {concept}\n"

    lines = text.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Single-# heading only (not ## / ###).
        if stripped.startswith("#") and not stripped.startswith("##"):
            lines[index] = f"# {concept}"
            return "\n".join(lines).rstrip() + "\n"
        # First non-empty line is not an H1 — prepend one.
        break

    return f"# {concept}\n\n{text.lstrip()}".rstrip() + "\n"


def refine_note_body(body: str) -> str:
    """Final consistency pass over a composed note body.

    Normalizes heading spacing, collapses blank-line runs, drops empty and
    duplicate bullets, and trims trailing whitespace -- while passing fenced code
    blocks through untouched.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    seen_bullets: set[str] = set()
    blank_run = 0
    in_fence = False

    for raw_line in lines:
        line = raw_line.rstrip()
        fence = line.lstrip().startswith("```")

        if in_fence:
            out.append(raw_line)
            if fence:
                in_fence = False
            continue
        if fence:
            if out and out[-1] != "":
                out.append("")
            out.append(line)
            in_fence = True
            blank_run = 0
            continue

        if not line.strip():
            blank_run += 1
            if blank_run == 1:
                out.append("")
            continue
        blank_run = 0

        bullet = _BULLET.match(line.strip())
        if bullet:
            item = bullet.group(1).strip()
            if not item:
                continue
            key = _normalize(item)
            if key and key in seen_bullets:
                continue
            if key:
                seen_bullets.add(key)
            out.append(f"- {item}")
            continue

        if _HEADING.match(line.strip()):
            seen_bullets.clear()  # bullets are only duplicates within one section
            if out and out[-1] != "":
                out.append("")
            out.append(line.strip())
            out.append("")
            blank_run = 1
            continue

        out.append(line)

    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
