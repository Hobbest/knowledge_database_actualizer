"""Detect, format, and place tables & figures found in sources.

Sources frequently carry structured content -- data tables and figures -- that
plain text extraction flattens or drops. This module recovers what it can:

* **Tables**: real PDF tables (via pdfplumber, done in the loader) and markdown
  pipe tables are rendered as clean markdown tables usable directly in a note.
* **Figures**: the image cannot be read, but its caption ("Figure 3-1: ...") is
  detected and kept as a checkable reference.

Each detected item keeps its :class:`SourceLocation`, so note assembly can pull
in exactly the tables/figures that belong to a note's page or line range.
"""

from __future__ import annotations

import re

from app.sources.base import MediaItem, SourceLocation
from app.text_limits import TEXT_LIMITS

# "Figure 2-5: caption", "Fig. 3 caption", "Table 1.2. caption" -- anchored to a
# line start so mid-sentence references ("see Figure 4") are not captured.
_CAPTION_RE = re.compile(
    r"^\s*(?P<kind>figure|fig\.?|table|tbl\.?)\s*"
    r"(?P<num>[A-Za-z]?\d+(?:[.\-]\d+)*)"
    r"\s*[:.\)\u2013-]?\s*(?P<caption>.*)$",
    re.IGNORECASE,
)

# A markdown table separator row: | --- | :---: | ---: |
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

_DOT_LEADER = re.compile(r"\.{4,}|(?:\.[ \t]+){2,}\.")


def _clean_caption(text: str) -> str:
    """Trim a caption and reject table-of-contents noise (dot leaders)."""
    cleaned = _DOT_LEADER.sub(" ", text).strip(" .\u2013-\t")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    # A "List of Figures" entry collapses to just a page number after cleaning.
    if not cleaned or cleaned.isdigit():
        return ""
    if len(cleaned) > TEXT_LIMITS.media_caption_chars:
        cleaned = cleaned[: TEXT_LIMITS.media_caption_chars].rsplit(" ", 1)[0].strip() + "..."
    return cleaned


def _normalize_label(kind: str, num: str) -> tuple[str, str]:
    lowered = kind.lower()
    canonical = "figure" if lowered.startswith(("fig", "figure")) else "table"
    display = "Figure" if canonical == "figure" else "Table"
    return canonical, f"{display} {num}"


def find_captions(text: str, location: SourceLocation) -> list[MediaItem]:
    """Detect figure/table captions in a block of text."""
    items: list[MediaItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Skip lines that are clearly a full sentence rather than a caption label.
        if len(line) > TEXT_LIMITS.media_caption_chars + 40:
            continue
        match = _CAPTION_RE.match(line)
        if not match:
            continue
        caption = _clean_caption(match.group("caption"))
        kind, label = _normalize_label(match.group("kind"), match.group("num"))
        items.append(MediaItem(kind=kind, label=label, caption=caption, location=location))
    return items


def table_to_markdown(rows: list[list[str | None]]) -> str | None:
    """Render a grid of cells (e.g. from pdfplumber) as a markdown table."""
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [re.sub(r"\s+", " ", (cell or "").strip()).replace("|", "\\|") for cell in row]
        if any(cells):
            cleaned.append(cells)
    if len(cleaned) < 2:  # need at least a header and one data row
        return None

    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    if width < 2:  # a single column is not a meaningful table
        return None

    max_rows = TEXT_LIMITS.media_table_max_rows
    truncated = len(cleaned) > max_rows
    body_rows = cleaned[:max_rows]

    header = body_rows[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for data_row in body_rows[1:]:
        lines.append("| " + " | ".join(data_row) + " |")
    if truncated:
        lines.append(f"| _... {len(cleaned) - max_rows} more row(s) omitted_ |")
    return "\n".join(lines)


def extract_markdown_tables(raw_lines: list[str], *, line_offset: int = 1) -> list[MediaItem]:
    """Detect markdown pipe tables and keep them as-is (already valid markdown)."""
    items: list[MediaItem] = []
    index = 0
    total = len(raw_lines)
    while index < total - 1:
        line = raw_lines[index]
        nxt = raw_lines[index + 1]
        if "|" in line and _TABLE_DELIM_RE.match(nxt):
            start = index
            block = [line.rstrip(), nxt.rstrip()]
            index += 2
            while index < total and "|" in raw_lines[index] and raw_lines[index].strip():
                block.append(raw_lines[index].rstrip())
                index += 1
            items.append(
                MediaItem(
                    kind="table",
                    label=f"Table (lines {start + line_offset}-{index + line_offset - 1})",
                    markdown="\n".join(block),
                    location=SourceLocation(
                        line_start=start + line_offset,
                        line_end=index + line_offset - 1,
                    ),
                )
            )
            continue
        index += 1
    return items


def merge_table_captions(items: list[MediaItem]) -> list[MediaItem]:
    """Fold a page's table caption into the extracted table on that page.

    pdfplumber gives the table structure but no caption; caption detection gives
    the label/caption but no structure. Pairing them per page yields a single,
    well-labeled table entry instead of two redundant ones.
    """
    captions_by_page: dict[int | None, list[MediaItem]] = {}
    for item in items:
        if item.kind == "table" and not item.markdown and item.location.page is not None:
            captions_by_page.setdefault(item.location.page, []).append(item)

    consumed: set[int] = set()
    for item in items:
        if item.kind == "table" and item.markdown:
            pool = captions_by_page.get(item.location.page)
            if pool:
                caption = pool.pop(0)
                consumed.add(id(caption))
                item.label = caption.label
                item.caption = caption.caption

    return [item for item in items if id(item) not in consumed]


def dedupe_media(items: list[MediaItem]) -> list[MediaItem]:
    seen: set[tuple] = set()
    unique: list[MediaItem] = []
    for item in items:
        key = (item.kind, item.label, item.caption, item.markdown)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _location_matches(note: SourceLocation, media: SourceLocation) -> bool:
    """True when a media item falls within a note's page or line span."""
    if media.page is not None and note.page is not None:
        note_end = note.page_end or note.page
        if note.page <= media.page <= note_end:
            return True
    if media.line_start is not None and note.line_start is not None:
        note_end = note.line_end or note.line_start
        media_end = media.line_end or media.line_start
        # Overlap between [note.line_start, note_end] and [media.line_start, media_end].
        if media.line_start <= note_end and media_end >= note.line_start:
            return True
    return False


def media_for_location(note: SourceLocation, media: list[MediaItem]) -> list[MediaItem]:
    matched = [item for item in media if _location_matches(note, item.location)]
    return dedupe_media(matched)[: TEXT_LIMITS.media_items_per_note]


def render_media_section(items: list[MediaItem]) -> str:
    """Render a '## Tables & figures' markdown section for a note."""
    if not items:
        return ""
    blocks: list[str] = ["## Tables & figures", ""]
    for item in items:
        where = item.location.display()
        heading = f"**{item.label}"
        if item.caption:
            heading += f" — {item.caption}"
        heading += "**"
        if where and where != "unknown":
            heading += f" ({where})"
        blocks.append(heading)
        if item.markdown:
            blocks.append("")
            blocks.append(item.markdown)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"
