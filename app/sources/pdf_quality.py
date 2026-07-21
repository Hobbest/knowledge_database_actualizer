from __future__ import annotations

import re

_WORD_PATTERN = re.compile(r"[A-Za-z']+")
_MIN_USEFUL_PAGE_CHARS = 80


def _looks_like_word(token: str) -> bool:
    lowered = token.lower().strip("'")
    return len(lowered) >= 3 and any(char in "aeiouy" for char in lowered)


def page_text_is_unreliable(text: str) -> bool:
    """Return whether a page should be retried with another extraction method."""
    stripped = text.strip()
    if len(stripped) < _MIN_USEFUL_PAGE_CHARS:
        return True

    words = _WORD_PATTERN.findall(stripped)
    if len(words) < 10:
        return True
    readable_ratio = sum(_looks_like_word(word) for word in words) / len(words)
    return readable_ratio < 0.55


def assess_pdf_extraction_quality(
    *,
    page_count: int,
    pages_with_text: int,
    text: str,
) -> list[str]:
    """Return user-facing warnings when PDF text extraction looks unreliable."""
    warnings: list[str] = []
    stripped = text.strip()
    if not stripped:
        return warnings

    if page_count >= 2:
        text_page_ratio = pages_with_text / page_count
        if text_page_ratio < 0.4:
            warnings.append(
                "PDF text extraction looks poor: only "
                f"{pages_with_text} of {page_count} pages yielded text. "
                "Scanned PDFs need OCR; multi-column layouts can also extract badly."
            )

        chars_per_page = len(stripped) / page_count
        if pages_with_text >= 2 and chars_per_page < 80:
            warnings.append(
                "PDF text extraction looks sparse: very little text per page was recovered. "
                "Novelty results may be unreliable until you use a text-based or OCR'd PDF."
            )

    words = _WORD_PATTERN.findall(stripped)
    if len(words) >= 20:
        readable_ratio = sum(_looks_like_word(word) for word in words) / len(words)
        if readable_ratio < 0.55:
            warnings.append(
                "PDF text extraction looks garbled: extracted words rarely look like readable "
                "language. Novelty results may be unreliable."
            )

    return warnings
