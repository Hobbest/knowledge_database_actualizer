from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

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
from app.sources.limits import truncate_segments_to_char_cap
from app.sources.pdf_quality import assess_pdf_extraction_quality, page_text_is_unreliable
from app.vision import describe_image

try:
    from pdf2image import convert_from_path
except ImportError:  # Optional OCR extra.
    convert_from_path = None

try:
    import pytesseract
except ImportError:  # Optional OCR extra.
    pytesseract = None

logger = logging.getLogger(__name__)


class PdfLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".pdf"}

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        try:
            reader = PdfReader(str(path))
            page_count = len(reader.pages)
        except Exception as exc:  # noqa: BLE001 - normalize parser-specific failures
            raise ValueError(f"Invalid or unreadable PDF: {path}") from exc

        load_warnings: list[str] = []
        pages_to_read = page_count
        max_pages = int(getattr(settings, "max_pdf_pages", 0) or 0)
        if max_pages > 0 and page_count > max_pages:
            pages_to_read = max_pages
            load_warnings.append(
                f"PDF has {page_count} pages; only the first {max_pages} were "
                f"extracted (MAX_PDF_PAGES)."
            )

        page_texts: list[str] = []
        for page_number, page in enumerate(reader.pages[:pages_to_read], start=1):
            try:
                page_texts.append((page.extract_text() or "").strip())
            except Exception as exc:  # noqa: BLE001 - another extractor may recover the page
                logger.warning("pypdf extraction failed for %s page %s: %s", path, page_number, exc)
                page_texts.append("")

        sparse_indexes = [
            index for index, text in enumerate(page_texts) if page_text_is_unreliable(text)
        ]
        plumber_texts = self._extract_text_with_pdfplumber(path, sparse_indexes)
        for index, fallback_text in plumber_texts.items():
            current_text = page_texts[index]
            if fallback_text and (
                not page_text_is_unreliable(fallback_text)
                or len(fallback_text) > len(current_text)
            ):
                page_texts[index] = fallback_text

        ocr_indexes = [
            index for index in sparse_indexes if page_text_is_unreliable(page_texts[index])
        ]
        if getattr(settings, "pdf_ocr_enabled", False) and ocr_indexes:
            load_warnings.extend(self._apply_ocr(path, page_texts, ocr_indexes))

        segments: list[SourceSegment] = []
        media: list[MediaItem] = []

        for page_number, text in enumerate(page_texts, start=1):
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

        segments, char_warning = truncate_segments_to_char_cap(
            segments,
            max_chars=int(getattr(settings, "max_source_chars", 0) or 0),
        )
        if char_warning:
            load_warnings.append(char_warning)

        if settings.include_media:
            media.extend(self._extract_tables(path))
            media = merge_table_captions(media)
            self._describe_figures(path, page_texts, media)

        content = "\n\n".join(segment.text for segment in segments)
        load_warnings.extend(
            assess_pdf_extraction_quality(
                page_count=page_count,
                pages_with_text=len(segments),
                text=content,
            )
        )
        return LoadedSource(
            title=path.stem,
            text=content,
            source_type="pdf",
            source_ref=str(path),
            segments=segments,
            media=media,
            load_warnings=load_warnings,
        )

    def _extract_text_with_pdfplumber(
        self, path: Path, page_indexes: list[int]
    ) -> dict[int, str]:
        """Retry sparse or garbled pages with pdfplumber."""
        if not page_indexes:
            return {}
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber is unavailable; PDF text fallback was skipped.")
            return {}

        extracted: dict[int, str] = {}
        try:
            with pdfplumber.open(str(path)) as pdf:
                for index in page_indexes:
                    if index >= len(pdf.pages):
                        continue
                    text = (pdf.pages[index].extract_text() or "").strip()
                    if text:
                        extracted[index] = text
        except Exception as exc:  # noqa: BLE001 - fallback extraction is best-effort
            logger.warning("pdfplumber text extraction failed for %s: %s", path, exc)
        return extracted

    def _apply_ocr(
        self, path: Path, page_texts: list[str], page_indexes: list[int]
    ) -> list[str]:
        """OCR only pages still sparse after both text extractors."""
        if convert_from_path is None or pytesseract is None:
            warning = (
                "PDF OCR was requested for sparse pages, but optional OCR dependencies "
                "are unavailable. Install requirements-ocr.txt and Poppler/Tesseract."
            )
            logger.warning(warning)
            return [warning]

        language = str(getattr(settings, "pdf_ocr_language", "eng"))
        dpi = int(getattr(settings, "pdf_ocr_dpi", 300))
        failed_pages: list[int] = []
        succeeded_pages: list[int] = []
        for index in page_indexes:
            page_number = index + 1
            try:
                images: list[Any] = convert_from_path(
                    str(path),
                    dpi=dpi,
                    first_page=page_number,
                    last_page=page_number,
                )
                if not images:
                    raise ValueError("renderer returned no image")
                ocr_text = (pytesseract.image_to_string(images[0], lang=language) or "").strip()
                if ocr_text:
                    page_texts[index] = ocr_text
                    succeeded_pages.append(page_number)
                else:
                    failed_pages.append(page_number)
            except Exception as exc:  # noqa: BLE001 - OCR is optional and best-effort
                failed_pages.append(page_number)
                logger.warning("OCR failed for %s page %s: %s", path, page_number, exc)

        warnings: list[str] = []
        if succeeded_pages:
            pages = ", ".join(str(page) for page in succeeded_pages)
            warnings.append(f"PDF OCR supplied text for scanned page(s): {pages}.")
        if failed_pages:
            pages = ", ".join(str(page) for page in failed_pages)
            warnings.append(f"PDF OCR failed or returned no text for page(s): {pages}.")
        return warnings

    def _describe_figures(
        self, path: Path, page_texts: list[str], items: list[MediaItem]
    ) -> None:
        """Optionally caption PDF figure pages with the configured vision model."""
        if not settings.vision_media_enabled or convert_from_path is None:
            return
        by_page: dict[int, list[MediaItem]] = {}
        for item in items:
            if item.kind == "figure" and item.location.page is not None:
                by_page.setdefault(item.location.page, []).append(item)
        for page_number, figures in by_page.items():
            try:
                images = convert_from_path(
                    str(path),
                    dpi=min(settings.pdf_ocr_dpi, 160),
                    first_page=page_number,
                    last_page=page_number,
                )
                if not images:
                    continue
                buffer = BytesIO()
                images[0].save(buffer, format="PNG")
                description = describe_image(
                    buffer.getvalue(),
                    context=page_texts[page_number - 1],
                )
                if description:
                    for item in figures:
                        item.caption = (
                            f"{item.caption} — {description}"
                            if item.caption
                            else description
                        )
            except Exception as exc:  # noqa: BLE001 - vision remains best-effort
                logger.warning(
                    "Vision description failed for %s page %s: %s",
                    path,
                    page_number,
                    exc,
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
