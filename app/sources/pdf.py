from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from app.config import settings
from app.media import find_captions, merge_table_captions, table_to_markdown
from app.sources.base import (
    LoadedSource,
    MediaItem,
    SourceLoader,
    SourceLocation,
    SourceSegment,
)

logger = logging.getLogger(__name__)


class PdfLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".pdf"}

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        reader = PdfReader(str(path))
        segments: list[SourceSegment] = []
        media: list[MediaItem] = []

        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            location = SourceLocation(page=page_number, page_end=page_number)
            segments.append(
                SourceSegment(text=text, location=location, index=len(segments))
            )
            if settings.include_media:
                media.extend(find_captions(text, location))

        if not segments:
            raise ValueError(f"No extractable text found in PDF: {path}")

        if settings.include_media:
            media.extend(self._extract_tables(path))
            media = merge_table_captions(media)

        content = "\n\n".join(segment.text for segment in segments)
        return LoadedSource(
            title=path.stem,
            text=content,
            source_type="pdf",
            source_ref=str(path),
            segments=segments,
            media=media,
        )

    def _extract_tables(self, path: Path) -> list[MediaItem]:
        """Extract structured tables with pdfplumber, if it is available."""
        try:
            import pdfplumber
        except ImportError:
            return []

        items: list[MediaItem] = []
        try:
            with pdfplumber.open(str(path)) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    location = SourceLocation(page=page_number, page_end=page_number)
                    for table_index, table in enumerate(page.extract_tables() or [], start=1):
                        markdown = table_to_markdown(table)
                        if not markdown:
                            continue
                        items.append(
                            MediaItem(
                                kind="table",
                                label=f"Table (page {page_number}, #{table_index})",
                                markdown=markdown,
                                location=location,
                            )
                        )
        except Exception as exc:  # noqa: BLE001 - table extraction is best-effort
            logger.warning("Table extraction failed for %s: %s", path, exc)
            return items
        return items
