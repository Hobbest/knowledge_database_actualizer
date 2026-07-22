"""Body-grounded concept titling for AtomicTopic names.

Titles are refined at planning time from the note body (extractive only — no
extra LLM calls). Presentation normalize stays in ``compose_title``.
"""

from __future__ import annotations

import re
from collections import Counter

from app.relevance import is_boilerplate_title
from app.summarize import (
    _content_words,
    _score_sentences,
    _split_sentences,
    compose_title,
    summarize_text,
)
from app.text_limits import TEXT_LIMITS
from app.text_utils import clean_extractive_text

TITLE_ALGORITHM_VERSION = "1"

_HEADING_LINE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_DEFINITIONAL = re.compile(
    r"^\s*(?P<subject>[A-Za-z][\w'’\-/]*(?:\s+[\w'’\-/]+){0,6})\s+"
    r"(?:is|are|was|were|refers\s+to|means|denotes)\s+"
    r"(?:a|an|the)\s+",
    re.IGNORECASE,
)
_DISCOURSE_OPENER = re.compile(
    r"^(?:however|therefore|moreover|furthermore|additionally|meanwhile|"
    r"in\s+this\s+(?:section|chapter|paper|article)|as\s+(?:shown|seen|noted)\s+in|"
    r"for\s+example|for\s+instance|note\s+that|it\s+is\s+(?:important|worth)|"
    r"figure\s+\d+|table\s+\d+)\s*[,:]?\s+",
    re.IGNORECASE,
)
_TRAILING_INCOMPLETE = re.compile(
    r"\b(?:and|or|of|to|for|with|in|on|at|by|from|as|the|a|an)\s*$",
    re.IGNORECASE,
)
_GENERIC_ONE_WORD = frozenset(
    {
        "overview",
        "introduction",
        "summary",
        "conclusion",
        "background",
        "discussion",
        "results",
        "methods",
        "method",
        "analysis",
        "data",
        "system",
        "model",
        "models",
        "approach",
        "concept",
        "untitled",
    }
)


def title_body_coherence(title: str, body: str) -> float:
    """Content-word Jaccard overlap between a title and a body (0..1)."""
    title_words = set(_content_words(title or ""))
    body_words = set(_content_words(body or ""))
    if not title_words or not body_words:
        return 0.0
    overlap = title_words & body_words
    union = title_words | body_words
    return len(overlap) / len(union)


def title_is_grounded(title: str, body: str) -> bool:
    """True when most title content words appear in ``body`` (asymmetric coverage).

    Prefer this over :func:`title_body_coherence` for quality flags: short concept
    titles against long notes score poorly under symmetric Jaccard.
    """
    title_words = set(_content_words(title or ""))
    body_words = set(_content_words(body or ""))
    if not title_words or not body_words:
        return False
    coverage = len(title_words & body_words) / len(title_words)
    if coverage >= 0.5:
        return True
    return title_body_coherence(title, body) >= TEXT_LIMITS.title_coherence_floor


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def _title_case_phrase(words: list[str]) -> str:
    return " ".join(word[:1].upper() + word[1:] if word else word for word in words)


def _clip_phrase(text: str, *, max_words: int | None = None) -> str:
    limit = max_words or TEXT_LIMITS.title_max_words
    cleaned = (text or "").strip().strip(" .:-\u2013\t\"'")
    cleaned = _DISCOURSE_OPENER.sub("", cleaned).strip()
    for sep in (". ", "; ", " — ", " - ", ": "):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
            break
    words = re.findall(r"[A-Za-z0-9']+", cleaned)
    if not words:
        return ""
    if len(words) > limit:
        words = words[:limit]
    phrase = " ".join(words)
    phrase = _TRAILING_INCOMPLETE.sub("", phrase).strip()
    return phrase


def _extract_in_body_heading(body: str) -> str | None:
    match = _HEADING_LINE.search(body or "")
    if not match:
        return None
    return match.group(2).strip()


def _definitional_candidates(sentences: list[str]) -> list[str]:
    found: list[str] = []
    for sentence in sentences:
        match = _DEFINITIONAL.match(sentence.strip())
        if not match:
            continue
        subject = re.sub(r"\s+", " ", match.group("subject")).strip()
        if subject and not is_boilerplate_title(subject):
            found.append(subject)
    return found


def _concept_span_from_sentence(sentence: str) -> str | None:
    stripped = _DISCOURSE_OPENER.sub("", (sentence or "").strip()).strip()
    if not stripped:
        return None
    cap = re.match(
        r"^([A-Z][\w'’\-/]*(?:\s+[A-Z][\w'’\-/]*){1,7})",
        stripped,
    )
    if cap:
        phrase = _clip_phrase(cap.group(1))
        if phrase and _word_count(phrase) >= TEXT_LIMITS.title_min_words:
            return phrase
    words = _content_words(stripped)
    if len(words) < TEXT_LIMITS.title_min_words:
        return None
    window = words[: TEXT_LIMITS.title_max_words]
    return _title_case_phrase(window)


def _salient_ngram(body: str) -> str | None:
    words = _content_words(body)
    if len(words) < TEXT_LIMITS.title_min_words:
        return None
    counts: Counter[tuple[str, ...]] = Counter()
    for size in (3, 2):
        if len(words) < size:
            continue
        for index in range(len(words) - size + 1):
            gram = tuple(words[index : index + size])
            if any(token in _GENERIC_ONE_WORD for token in gram):
                continue
            counts[gram] += 1
        if counts:
            best, _ = counts.most_common(1)[0]
            return _title_case_phrase(list(best))
    freq = Counter(words)
    for word, _ in freq.most_common():
        if word not in _GENERIC_ONE_WORD and len(word) > 3:
            return word[:1].upper() + word[1:]
    return None


def _looks_definitional_subject(candidate: str, body: str) -> bool:
    for sentence in _split_sentences(clean_extractive_text(body)):
        match = _DEFINITIONAL.match(sentence.strip())
        if not match:
            continue
        subject = re.sub(r"\s+", " ", match.group("subject")).strip()
        if subject.casefold() == candidate.casefold():
            return True
    return False


def _candidate_score(candidate: str, body: str) -> float:
    if not candidate or is_boilerplate_title(candidate):
        return -1.0
    words = _word_count(candidate)
    grounding = title_body_coherence(candidate, body)
    score = grounding * 2.0

    min_w = TEXT_LIMITS.title_min_words
    max_w = TEXT_LIMITS.title_max_words
    if min_w <= words <= max_w:
        score += 0.35
    elif words == 1:
        score -= 0.45 if candidate.lower() in _GENERIC_ONE_WORD else 0.2
    elif words == 2:
        score += 0.1
    elif words > 12:
        score -= 0.5
    elif words > max_w:
        score -= 0.15 * (words - max_w)

    # Definitional subjects are preferred over free n-grams from later sentences.
    if _looks_definitional_subject(candidate, body):
        score += 0.8

    if _TRAILING_INCOMPLETE.search(candidate):
        score -= 0.35
    if "..." in candidate or "…" in candidate:
        score -= 0.4
    if re.search(r"\s+\d{1,4}$", candidate):
        score -= 0.35
    if candidate.endswith((",", ";", "-")):
        score -= 0.25
    # Penalize verb-heavy title-case spans that read like sentence fragments.
    if re.search(
        r"\b(Often|Use|Uses|Using|Improve|Improves|Allow|Allows|Can|May)\b",
        candidate,
    ):
        score -= 0.4
    return score


def _keep_hint(hint: str | None, body: str, *, require_overlap: bool = True) -> str | None:
    if not hint:
        return None
    cleaned = hint.strip().lstrip("#").strip()
    if not cleaned or is_boilerplate_title(cleaned):
        return None
    if not require_overlap:
        return cleaned
    body_words = set(_content_words(body or ""))
    if not body_words:
        # No body signal — keep a clean hint rather than inventing a title.
        return cleaned
    if title_body_coherence(cleaned, body) >= TEXT_LIMITS.title_heading_jaccard:
        return cleaned
    return None


def concept_title_from_body(body: str, *, hint: str | None = None) -> str:
    """Pick a concept-phrase title grounded in ``body`` (extractive)."""
    cleaned = clean_extractive_text(body or "")
    if not cleaned.strip():
        kept = _keep_hint(hint, body or "", require_overlap=False)
        return compose_title(kept or "Untitled concept")

    sentences = _split_sentences(cleaned)
    scores = _score_sentences(sentences) if sentences else []
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (scores[i] if i < len(scores) else 0.0, -i),
        reverse=True,
    )
    top_sentences = [sentences[i] for i in ranked[:5]] if sentences else []

    candidates: list[str] = []
    kept_hint = _keep_hint(hint, cleaned)
    if kept_hint:
        candidates.append(kept_hint)
    in_body = _extract_in_body_heading(body or "")
    kept_heading = _keep_hint(in_body, cleaned)
    if kept_heading and kept_heading not in candidates:
        candidates.append(kept_heading)

    # Prefer definitional subjects from the full sentence list (not only top-scored).
    for item in _definitional_candidates(sentences):
        if item not in candidates:
            candidates.append(item)

    for sentence in top_sentences:
        span = _concept_span_from_sentence(sentence)
        if span and span not in candidates:
            candidates.append(span)

    ngram = _salient_ngram(cleaned)
    if ngram and ngram not in candidates:
        candidates.append(ngram)

    if not candidates:
        return compose_title(_clip_phrase(cleaned) or "Untitled concept")

    best = max(candidates, key=lambda item: _candidate_score(item, cleaned))
    if _candidate_score(best, cleaned) < 0:
        return compose_title(_clip_phrase(cleaned) or "Untitled concept")
    return compose_title(best)


def refine_topic_title(body: str, *, hint: str | None = None) -> str:
    """Ground a topic title in ``body``, repairing weak hints deterministically."""
    cleaned = clean_extractive_text(body or "")
    source = cleaned or (body or "")
    title = concept_title_from_body(source, hint=hint)
    coherence = title_body_coherence(title, source)

    kept_hint = _keep_hint(hint, source)
    if kept_hint:
        hint_coherence = title_body_coherence(kept_hint, source)
        if hint_coherence >= TEXT_LIMITS.title_coherence_floor and hint_coherence >= coherence:
            return compose_title(kept_hint)

    if coherence >= TEXT_LIMITS.title_coherence_floor and not is_boilerplate_title(title):
        return title

    summary = summarize_text(source, max_sentences=1)
    fallback = _clip_phrase(summary) or _clip_phrase(cleaned) or kept_hint or "Untitled concept"
    return compose_title(fallback)


def disambiguate_titles(titles: list[str]) -> list[str]:
    """Ensure unique titles by appending ``(part N)`` only on collisions."""
    if not titles:
        return []

    bare_titles: list[str] = []
    for title in titles:
        base = compose_title(title or "Untitled concept")
        bare = re.sub(r"\s*\((?:part\s*)?\d+\)\s*$", "", base, flags=re.IGNORECASE).strip()
        bare_titles.append(compose_title(bare or "Untitled concept"))

    counts: Counter[str] = Counter(item.casefold() for item in bare_titles)
    seen: dict[str, int] = {}
    result: list[str] = []
    for bare in bare_titles:
        key = bare.casefold()
        if counts[key] == 1:
            result.append(bare)
            continue
        index = seen.get(key, 0) + 1
        seen[key] = index
        result.append(compose_title(f"{bare} (part {index})"))
    return result


__all__ = [
    "TITLE_ALGORITHM_VERSION",
    "concept_title_from_body",
    "disambiguate_titles",
    "refine_topic_title",
    "title_body_coherence",
    "title_is_grounded",
]
