from __future__ import annotations

import logging
from pathlib import Path

import ebooklib
from ebooklib import epub

from app.sources.base import LoadedSource, SourceLoader, SourceLocation, SourceSegment
from app.sources.text import segments_from_markdown

logger = logging.getLogger(__name__)


def _chapter_text(html: str) -> str:
    import trafilatura

    extracted = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    if extracted and extracted.strip():
        return extracted.strip()

    # Fallback when trafilatura finds nothing in a short chapter fragment.
    from html.parser import HTMLParser

    class _TextCollector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            text = data.strip()
            if text:
                self.parts.append(text)

    parser = _TextCollector()
    parser.feed(html)
    return "\n".join(parser.parts).strip()


def _book_title(book: epub.EpubBook, fallback: str) -> str:
    titles = book.get_metadata("DC", "title") or []
    for entry in titles:
        value = str(entry[0] or "").strip()
        if value:
            return value
    return fallback


class EpubLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".epub"}

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        book = epub.read_epub(str(path))
        segments: list[SourceSegment] = []
        chapter_number = 0

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            chapter_number += 1
            try:
                html = item.get_content().decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 - skip unreadable chapter payloads
                logger.debug("Skipping EPUB chapter %s: %s", item.get_name(), exc)
                continue

            chapter_text = _chapter_text(html)
            if not chapter_text:
                continue

            raw_lines = chapter_text.splitlines()
            chapter_segments = segments_from_markdown(raw_lines)
            if not chapter_segments:
                location = SourceLocation(page=chapter_number, page_end=chapter_number)
                chapter_segments = [
                    SourceSegment(text=chapter_text, location=location, index=0)
                ]

            for segment in chapter_segments:
                location = segment.location
                segments.append(
                    SourceSegment(
                        text=segment.text,
                        location=SourceLocation(
                            page=chapter_number,
                            page_end=chapter_number,
                            line_start=location.line_start,
                            line_end=location.line_end,
                        ),
                        index=len(segments),
                    )
                )

        if not segments:
            raise ValueError(f"No readable chapter text found in EPUB: {path}")

        content = "\n\n".join(segment.text for segment in segments)
        return LoadedSource(
            title=_book_title(book, path.stem),
            text=content,
            source_type="epub",
            source_ref=str(path),
            segments=segments,
        )
