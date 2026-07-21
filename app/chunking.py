from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    text: str
    index: int
    heading: str | None = None


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_LIST_LINE_PATTERN = re.compile(r"^\s*([-*+]|\d+\.)\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATION_END = re.compile(
    r"(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)|\b[A-Z])\.$",
    re.IGNORECASE,
)


def _split_by_headings(text: str) -> list[tuple[str | None, str]]:
    """Split markdown/text into (heading, section_body) pairs."""
    matches = list(HEADING_PATTERN.finditer(text))
    if not matches:
        return [(None, text.strip())] if text.strip() else []

    sections: list[tuple[str | None, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append((None, preamble))

    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((heading, body))

    return sections


def _split_into_units(text: str) -> list[str]:
    """Split section text into units that should not be broken (code, lists, paragraphs)."""
    if not text.strip():
        return []

    units: list[str] = []
    lines = text.splitlines(keepends=True)
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            block = [line]
            index += 1
            while index < total:
                block.append(lines[index])
                if lines[index].strip().startswith("```") and len(block) > 1:
                    index += 1
                    break
                index += 1
            units.append("".join(block))
            continue

        if _LIST_LINE_PATTERN.match(line):
            block = [line]
            index += 1
            while index < total:
                next_line = lines[index]
                if not next_line.strip():
                    break
                if _LIST_LINE_PATTERN.match(next_line) or next_line.startswith(("  ", "\t")):
                    block.append(next_line)
                    index += 1
                    continue
                break
            units.append("".join(block))
            continue

        block = [line]
        index += 1
        while index < total:
            next_line = lines[index]
            if not next_line.strip():
                break
            if next_line.strip().startswith("```") or _LIST_LINE_PATTERN.match(next_line):
                break
            block.append(next_line)
            index += 1
        units.append("".join(block))

    return units


def _split_at_word_boundary(text: str, chunk_size: int) -> list[str]:
    """Character fallback that prefers whitespace boundaries."""
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            split_at = text.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end)
    return pieces


def _split_sentences(text: str) -> list[str]:
    """Split prose semantically while avoiding common abbreviation boundaries."""
    candidates = _SENTENCE_BOUNDARY.split(text)
    sentences: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if sentences and _ABBREVIATION_END.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {candidate}"
        else:
            sentences.append(candidate)
    return sentences


def _split_oversized_unit(text: str, chunk_size: int) -> list[str]:
    """Split a single oversized unit on sentence, then word/character boundaries."""
    if len(text) <= chunk_size:
        return [text]

    if text.lstrip().startswith("```"):
        return _split_at_word_boundary(text, chunk_size)

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return _split_at_word_boundary(text, chunk_size)

    pieces: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            pieces.append("".join(current).strip())
            current = []
            current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > chunk_size:
            flush()
            pieces.extend(_split_at_word_boundary(sentence, chunk_size))
            continue
        extra = len(sentence) + (1 if current else 0)
        if current_len + extra > chunk_size:
            flush()
        current.append(sentence if not current else f" {sentence}")
        current_len += extra

    flush()
    return pieces or _split_at_word_boundary(text, chunk_size)


def _tail_units_for_overlap(units: list[str], chunk_overlap: int) -> list[str]:
    if chunk_overlap <= 0 or not units:
        return []

    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        unit_len = len(unit)
        if tail and total + unit_len > chunk_overlap:
            break
        tail.insert(0, unit)
        total += unit_len
    return tail


def _chunk_units(units: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """Pack indivisible units into overlapping chunks."""
    if not units:
        return []

    chunks: list[str] = []
    current: list[str] = []
    index = 0

    def current_length() -> int:
        return len("".join(current))

    def flush() -> None:
        text = "".join(current).strip()
        if text:
            chunks.append(text)

    while index < len(units) or current:
        if index >= len(units):
            flush()
            break

        unit = units[index]

        if len(unit) > chunk_size:
            flush()
            overlap = _tail_units_for_overlap(current, chunk_overlap)
            current = list(overlap)
            for piece in _split_oversized_unit(unit, chunk_size):
                if current and current_length() + len(piece) > chunk_size:
                    flush()
                    overlap = _tail_units_for_overlap(current, chunk_overlap)
                    current = list(overlap)
                current.append(piece)
            index += 1
            continue

        if current and current_length() + len(unit) > chunk_size:
            flush()
            current = _tail_units_for_overlap(current, chunk_overlap)

        current.append(unit)
        index += 1

    return chunks


def chunk_text(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    source_label: str | None = None,
) -> list[TextChunk]:
    """Chunk plain text or markdown into overlapping segments."""
    sections = _split_by_headings(text)
    chunks: list[TextChunk] = []
    idx = 0

    for heading, section_body in sections:
        prefix = f"# {heading}\n\n" if heading else ""
        for piece in _chunk_units(_split_into_units(section_body), chunk_size, chunk_overlap):
            full = f"{prefix}{piece}".strip()
            if full:
                chunks.append(TextChunk(text=full, index=idx, heading=heading or source_label))
                idx += 1

    if not chunks and text.strip():
        for piece in _chunk_units(_split_into_units(text), chunk_size, chunk_overlap):
            chunks.append(TextChunk(text=piece, index=idx, heading=source_label))
            idx += 1

    return chunks
