from __future__ import annotations

import re
from pathlib import Path

from app.config import settings
from app.media import extract_markdown_tables, find_captions
from app.sources.base import (
    LoadedSource,
    MediaItem,
    SourceLoader,
    SourceLocation,
    SourceSegment,
)
from app.vault import (
    extract_wikilinks,
    merge_note_tags,
    merge_note_wikilinks,
    parse_note_text,
)

HEADING_LINE_PATTERN = re.compile(r"^#{1,3}\s+.+$")


class TextLoader(SourceLoader):
    SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown"}
    PARAGRAPH_TARGET_LINES = 25

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            raise ValueError(f"Empty text file: {path}")

        is_markdown = path.suffix.lower() in {".md", ".markdown"}
        metadata: dict = {}
        body = raw
        tags: list[str] = []
        wikilinks: list[str] = []

        if is_markdown:
            metadata, body = parse_note_text(raw, source=str(path))
            tags = merge_note_tags(metadata, body)
            wikilinks = merge_note_wikilinks(metadata, body)

        raw_lines = body.splitlines()
        if not any(line.strip() for line in raw_lines):
            raise ValueError(f"Empty text file: {path}")

        segments = (
            self._segments_from_markdown(raw_lines)
            if is_markdown
            else self._segments_from_lines(raw_lines)
        )
        content = body.strip()
        source_type = "markdown" if is_markdown else "text"
        title = str(metadata.get("title") or path.stem)

        media = self._detect_media(raw_lines) if settings.include_media else []

        return LoadedSource(
            title=title,
            text=content,
            source_type=source_type,
            source_ref=str(path),
            segments=segments,
            media=media,
            wikilinks=wikilinks,
            tags=tags,
        )

    def _detect_media(self, raw_lines: list[str]) -> list[MediaItem]:
        media: list[MediaItem] = extract_markdown_tables(raw_lines)
        # Figure/table captions, tagged with the line they appear on.
        for line_number, line in enumerate(raw_lines, start=1):
            location = SourceLocation(line_start=line_number, line_end=line_number)
            media.extend(find_captions(line, location))
        return media

    def _segments_from_markdown(self, raw_lines: list[str]) -> list[SourceSegment]:
        segments: list[SourceSegment] = []
        section_lines: list[str] = []
        section_start: int | None = None

        def flush(end_line: int) -> None:
            nonlocal section_lines, section_start
            if not section_lines or section_start is None:
                section_lines = []
                section_start = None
                return

            text = "\n".join(section_lines).strip()
            if text:
                segments.append(
                    SourceSegment(
                        text=text,
                        location=SourceLocation(line_start=section_start, line_end=end_line),
                        index=len(segments),
                    )
                )
            section_lines = []
            section_start = None

        for line_number, line in enumerate(raw_lines, start=1):
            if HEADING_LINE_PATTERN.match(line.strip()):
                if section_lines:
                    flush(line_number - 1)
                section_start = line_number
                section_lines = [line]
                continue

            if section_start is None:
                section_start = line_number
            section_lines.append(line)

            if len(section_lines) >= self.PARAGRAPH_TARGET_LINES:
                flush(line_number)

        if section_lines and section_start is not None:
            flush(len(raw_lines))

        return segments or self._segments_from_lines(raw_lines)

    def _segments_from_lines(self, raw_lines: list[str]) -> list[SourceSegment]:
        segments: list[SourceSegment] = []
        paragraph_lines: list[str] = []
        paragraph_start: int | None = None

        def flush(end_line: int) -> None:
            nonlocal paragraph_lines, paragraph_start
            if not paragraph_lines or paragraph_start is None:
                paragraph_lines = []
                paragraph_start = None
                return

            text = "\n".join(paragraph_lines).strip()
            if text:
                segments.append(
                    SourceSegment(
                        text=text,
                        location=SourceLocation(
                            line_start=paragraph_start,
                            line_end=end_line,
                        ),
                        index=len(segments),
                    )
                )
            paragraph_lines = []
            paragraph_start = None

        for line_number, line in enumerate(raw_lines, start=1):
            if not line.strip():
                if paragraph_lines:
                    flush(line_number - 1)
                continue

            if paragraph_start is None:
                paragraph_start = line_number
            paragraph_lines.append(line)

            if len(paragraph_lines) >= self.PARAGRAPH_TARGET_LINES:
                flush(line_number)

        if paragraph_lines and paragraph_start is not None:
            flush(len(raw_lines))

        return segments
