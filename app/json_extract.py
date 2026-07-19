"""Robust extraction of JSON arrays from messy LLM responses."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_FENCE_OPEN_RE = re.compile(r"^```(?:json|JSON)?\s*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$")
_SMART_QUOTES = str.maketrans(
    {
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
    }
)
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


def _strip_code_fences(text: str) -> str:
    """Remove surrounding ``` / ```json fences when present."""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    cleaned = _FENCE_OPEN_RE.sub("", cleaned, count=1)
    cleaned = _FENCE_CLOSE_RE.sub("", cleaned, count=1)
    return cleaned.strip()


def _normalize_smart_quotes(text: str) -> str:
    return text.translate(_SMART_QUOTES)


def _escape_raw_newlines_in_strings(text: str) -> str:
    """Escape literal newlines inside JSON strings (common LLM damage)."""
    out: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                out.append(char)
                escaped = False
                continue
            if char == "\\":
                out.append(char)
                escaped = True
                continue
            if char == '"':
                out.append(char)
                in_string = False
                continue
            if char == "\n":
                out.append("\\n")
                continue
            if char == "\r":
                continue
            if char == "\t":
                out.append("\\t")
                continue
            out.append(char)
            continue
        if char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def _repair_json_text(text: str) -> str:
    """Apply cheap, safe repairs before parsing."""
    repaired = _normalize_smart_quotes(text)
    repaired = _escape_raw_newlines_in_strings(repaired)
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)
    return repaired


def _array_score(items: list[dict]) -> int:
    """Prefer arrays that look like note plans / drafts over incidental lists."""
    if not items:
        return 0
    score = len(items)
    for item in items:
        if "title" in item:
            score += 10
        if "id" in item:
            score += 3
        if "body" in item or "segment_indices" in item or "summary" in item:
            score += 5
    return score


def _dicts_from_list(parsed: list) -> list[dict]:
    return [item for item in parsed if isinstance(item, dict)]


def _try_extract_dict_arrays(text: str) -> list[dict] | None:
    """Scan every ``[`` and parse with ``raw_decode`` (ignores trailing junk)."""
    decoder = json.JSONDecoder()
    best: list[dict] | None = None
    best_score = -1
    saw_array = False
    index = 0
    length = len(text)

    while index < length:
        start = text.find("[", index)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue

        if isinstance(parsed, list):
            saw_array = True
            dicts = _dicts_from_list(parsed)
            score = _array_score(dicts)
            if best is None or score > best_score:
                best = dicts
                best_score = score
            index = max(end, start + 1)
        else:
            index = start + 1

    if not saw_array:
        return None
    return best if best is not None else []


def _try_extract_from_wrapper_objects(text: str) -> list[dict] | None:
    """Handle ``{"notes": [...]}`` / ``{"items": [...]}`` OpenAI-style wrappers."""
    decoder = json.JSONDecoder()
    best: list[dict] | None = None
    best_score = -1
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(parsed, dict):
            for value in parsed.values():
                if isinstance(value, list):
                    dicts = _dicts_from_list(value)
                    score = _array_score(dicts)
                    if dicts and score > best_score:
                        best = dicts
                        best_score = score
        index = max(end, start + 1)
    return best


def _extract_objects_from_fragment(text: str) -> list[dict]:
    """Recover complete objects from truncated / broken array fragments."""
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(parsed, dict) and (
            "body" in parsed or "title" in parsed or "segment_indices" in parsed
        ):
            objects.append(parsed)
        index = max(end, start + 1)
    return objects


def extract_json_array(raw: str) -> list[dict]:
    """Extract a JSON array of objects from an LLM response.

    Handles markdown fences, prose preambles, Obsidian ``[[wikilinks]]``,
    trailing junk, smart quotes, literal newlines inside strings, truncated
    arrays (partial object recovery), and ``{"items": [...]}`` wrappers.

    Returns an empty list when nothing usable is found. Raises
    ``json.JSONDecodeError`` only when the response clearly tried to include
    JSON brackets/braces but no objects could be recovered.
    """
    text = _strip_code_fences(raw or "")
    if not text:
        return []

    variants = [text]
    repaired = _repair_json_text(text)
    if repaired != text:
        variants.append(repaired)

    complete_best: list[dict] | None = None
    complete_score = -1
    fragment_best: list[dict] | None = None
    fragment_score = -1

    for candidate in variants:
        for extracted in (
            _try_extract_dict_arrays(candidate),
            _try_extract_from_wrapper_objects(candidate),
        ):
            if extracted is None:
                continue
            score = _array_score(extracted)
            if complete_best is None or score > complete_score:
                complete_best = extracted
                complete_score = score

        # Truncated batch replies: salvage complete objects when the array
        # itself cannot be decoded. Prefer a successful full-array parse.
        fragment_objs = _extract_objects_from_fragment(candidate)
        if fragment_objs:
            score = _array_score(fragment_objs)
            if fragment_best is None or score > fragment_score:
                fragment_best = fragment_objs
                fragment_score = score

    if complete_best is not None and (complete_score > 0 or complete_best == []):
        return complete_best
    if fragment_best is not None and fragment_score > 0:
        return fragment_best

    if "[" not in text and "{" not in text:
        return []

    preview = text[:240].replace("\n", "\\n")
    logger.warning("Failed to parse LLM JSON payload (preview=%r)", preview)
    raise json.JSONDecodeError(
        "Could not parse a JSON array of objects from model response",
        text,
        0,
    )
