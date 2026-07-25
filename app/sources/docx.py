from __future__ import annotations

import logging
from pathlib import Path

import mammoth

from app.config import settings
from app.sources.base import LoadedSource, SourceLoader, SourceLocation, SourceSegment
from app.sources.limits import truncate_segments_to_char_cap
from app.sources.text import segments_from_markdown

logger = logging.getLogger(__name__)


class DocxLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".docx"}

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        with path.open("rb") as handle:
            result = mammoth.convert_to_markdown(handle)

        markdown = (result.value or "").strip()
        if not markdown:
            raise ValueError(f"No extractable text found in DOCX: {path}")

        for message in result.messages:
            logger.debug("DOCX conversion message for %s: %s", path, message)

        load_warnings = [
            f"DOCX conversion warning: {message}"
            for message in result.messages
            if str(message).strip()
        ]

        raw_lines = markdown.splitlines()
        segments = segments_from_markdown(raw_lines)
        if not segments:
            segments = [
                SourceSegment(text=markdown, location=SourceLocation(), index=0)
            ]

        segments, char_warning = truncate_segments_to_char_cap(
            segments,
            max_chars=int(getattr(settings, "max_source_chars", 0) or 0),
        )
        if char_warning:
            load_warnings.append(char_warning)

        content = "\n\n".join(segment.text for segment in segments)
        return LoadedSource(
            title=path.stem,
            text=content,
            source_type="docx",
            source_ref=str(path),
            segments=segments,
            load_warnings=load_warnings,
        )
