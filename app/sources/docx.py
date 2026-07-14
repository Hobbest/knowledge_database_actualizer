from __future__ import annotations

import logging
from pathlib import Path

import mammoth

from app.sources.base import LoadedSource, SourceLoader, SourceLocation, SourceSegment
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

        raw_lines = markdown.splitlines()
        segments = segments_from_markdown(raw_lines)
        if not segments:
            segments = [
                SourceSegment(text=markdown, location=SourceLocation(), index=0)
            ]

        return LoadedSource(
            title=path.stem,
            text=markdown,
            source_type="docx",
            source_ref=str(path),
            segments=segments,
        )
