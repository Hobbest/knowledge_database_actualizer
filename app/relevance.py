"""Filter out low-value boilerplate that should not become knowledge notes.

Books and papers contain non-substantive material -- acknowledgements, tables of
contents, reference lists, chapter summaries, contact/media pages, copyright
notices -- that adds no new knowledge to a vault. This module detects such
sections so they can be skipped during note generation.
"""

from __future__ import annotations

import re

from app.sources.base import SourceSegment

# Section titles that mark non-substantive content. Matched against the
# normalized leading line of a segment (or a topic title), as a whole label.
_BOILERPLATE_TITLE = re.compile(
    r"(?:"
    r"acknowledge?ments?"
    r"|table of contents|contents"
    r"|list of (?:figures|tables|abbreviations|illustrations|symbols|equations)"
    r"|references?|bibliography|works cited|further reading|reading list|citations?"
    r"|index"
    r"|copyright(?: (?:notice|page))?"
    r"|about the (?:author|authors|publisher|book|editor)"
    r"|author (?:biograph(?:y|ies)|bio|note)"
    r"|dedication|colophon|epigraph|imprint"
    r"|(?:chapter )?summary|summary of (?:this|the) chapter|chapter recap|recap"
    r"|key takeaways?|takeaways?|conclusion of (?:this|the) chapter"
    r"|(?:press|media) (?:inquiries|contact|kit|relations|enquiries)"
    r"|contact(?: us| information| details| info)?"
    r"|frequently asked questions|faq"
    r"|errata|disclaimer|legal notice|terms of (?:use|service)|privacy policy"
    r"|permissions?|rights and permissions|order(?:ing)? information"
    r")",
    re.IGNORECASE,
)

# Strong content markers that appear on legal / front-matter pages.
_CONTENT_MARKERS = (
    "all rights reserved",
    "©",
    "isbn",
    "printed in the united states",
    "no part of this book may be reproduced",
    "no part of this publication may be reproduced",
    "library of congress",
    "cataloging-in-publication",
    "disclaim all responsibility",
    "used good faith efforts",
    "at your own risk",
    "is subject to open source licenses",
    "trademarks of their respective",
    "for more information, contact",
)

# Phrases that mark a contact / media / promotional block.
_CONTACT_PHRASES = (
    "media inquiries",
    "press inquiries",
    "press contact",
    "for media",
    "for permissions",
    "rights and permissions",
    "follow us on",
    "contact us at",
    "reach us at",
    "subscribe to our newsletter",
    "visit us at",
)

_LEADING_NUMBERING = re.compile(
    r"^(?:chapter|part|section|appendix|unit|lesson)\s+[\divxlcdm]+\s*[:.\-\u2013)]?\s*",
    re.IGNORECASE,
)
_LEADING_ENUM = re.compile(r"^\d+(?:\.\d+)*\s*[:.\-\u2013)]?\s+")

# Dot leaders ("......" or ". . .") joining an entry to its page number are the
# clearest signature of a table of contents and survive whitespace collapsing.
_DOT_LEADER = re.compile(r"\.{4,}|(?:\.[ \t]+){2,}\.")

# A single ToC/index line: some text, then a gap (dot leaders or wide spacing),
# then a trailing page number (arabic or roman).
_TOC_LINE = re.compile(
    r"^.+?[ \t.]*(?:\.{2,}|[ \t]{2,})[ \t]*(?:\d{1,4}|[ivxlcdm]{1,7})$",
    re.IGNORECASE,
)

# URLs and bracketed citation markers ("[17]") -- their density is a strong
# signal that a block is a reference / link list rather than prose.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CITATION_MARKER_RE = re.compile(r"\[\d{1,4}\]")
_WORD_CHARS_RE = re.compile(r"[^\W\d_]")  # unicode letters only

# Distribution / watermark footers that ebook exporters inject.
_WATERMARKS = (
    "oceanofpdf.com",
    "this page intentionally left blank",
    "downloaded from",
    "free ebooks ==>",
    "www.it-ebooks",
)


def _leading_label(text: str) -> str:
    """Return the first meaningful line, normalized for title matching."""
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line:
            continue
        line = _LEADING_NUMBERING.sub("", line)
        line = _LEADING_ENUM.sub("", line)
        return line.strip(" :.-\u2013\t").strip()
    return ""


def looks_like_table_of_contents(text: str) -> bool:
    """True when text is a table of contents / index rather than prose.

    Such pages are just entry-plus-page-number lists ("Introduction ...... 5")
    with no standalone knowledge, and their dot leaders / stray page numbers turn
    into garbage notes. Detected structurally so it also catches ToC pages whose
    heading was lost or renamed during extraction.
    """
    stripped = text.strip()
    if not stripped:
        return False

    # Dot leaders are the strongest signal and survive collapsed whitespace.
    if len(_DOT_LEADER.findall(stripped)) >= 3:
        return True

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    entry_like = sum(1 for line in lines if _TOC_LINE.match(line))
    return entry_like >= max(4, (len(lines) + 1) // 2)


def is_boilerplate_title(title: str) -> bool:
    """True when a title clearly names a non-substantive section."""
    stripped = title.strip()
    # A title lifted from a ToC entry ("1.2 Overview ...... 7") is never a real
    # concept, regardless of its length.
    if _DOT_LEADER.search(stripped) or _TOC_LINE.match(stripped):
        return True

    label = _leading_label(title)
    if not label or len(label) > 60:
        return False
    return _BOILERPLATE_TITLE.fullmatch(label) is not None


def _prose_word_ratio(tokens: list[str]) -> float:
    """Fraction of whitespace tokens that look like real words (not URLs/markers)."""
    if not tokens:
        return 0.0
    wordish = 0
    for token in tokens:
        if _URL_RE.match(token):
            continue
        core = token.strip("[](){}.,;:!?\"'\u2013-")
        if len(core) < 2:
            continue
        letters = len(_WORD_CHARS_RE.findall(core))
        if letters >= 2 and letters / len(core) >= 0.6:
            wordish += 1
    return wordish / len(tokens)


def is_low_value_text(text: str) -> bool:
    """True when text is a link/reference dump or otherwise non-prose junk.

    Reference lists, URL dumps, and export watermarks are not knowledge and make
    for garbage notes. They are detected structurally -- by the density of URLs
    and citation markers versus real words -- so they are caught even without a
    "References" heading.
    """
    stripped = text.strip()
    if not stripped:
        return True

    tokens = stripped.split()
    if not tokens:
        return True

    prose_ratio = _prose_word_ratio(tokens)
    url_count = len(_URL_RE.findall(stripped))
    citation_count = len(_CITATION_MARKER_RE.findall(stripped))
    lowered = stripped.lower()
    watermark = any(mark in lowered for mark in _WATERMARKS)

    # A block dominated by links / citation markers rather than sentences.
    if url_count >= 3 and prose_ratio < 0.55:
        return True
    if citation_count >= 5 and prose_ratio < 0.6:
        return True
    # Substantial text that is almost entirely non-words (data/symbol dumps).
    if len(stripped) >= 200 and prose_ratio < 0.35:
        return True
    # A watermark line only counts as junk when there is little real prose around it.
    if watermark and prose_ratio < 0.5:
        return True

    return False


def is_boilerplate(text: str) -> bool:
    """True when a segment's content is boilerplate that should be skipped."""
    stripped = text.strip()
    if not stripped:
        return True

    if is_boilerplate_title(_leading_label(stripped)):
        return True

    if looks_like_table_of_contents(stripped):
        return True

    if is_low_value_text(stripped):
        return True

    lowered = stripped.lower()
    if any(marker in lowered for marker in _CONTENT_MARKERS):
        return True
    if any(phrase in lowered for phrase in _CONTACT_PHRASES):
        return True

    return False


def filter_relevant_segments(segments: list[SourceSegment]) -> list[SourceSegment]:
    """Drop boilerplate segments, but never return an empty list."""
    kept = [segment for segment in segments if not is_boilerplate(segment.text)]
    return kept or segments
