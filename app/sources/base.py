from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceLocation:
    page: int | None = None
    page_end: int | None = None
    chapter: int | None = None
    chapter_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    timestamp_start: float | None = None
    timestamp_end: float | None = None

    def display(self) -> str:
        parts: list[str] = []
        if self.chapter is not None:
            if self.chapter_end and self.chapter_end != self.chapter:
                parts.append(f"chapters {self.chapter}-{self.chapter_end}")
            else:
                parts.append(f"chapter {self.chapter}")
        elif self.page is not None:
            if self.page_end and self.page_end != self.page:
                parts.append(f"pages {self.page}-{self.page_end}")
            else:
                parts.append(f"page {self.page}")
        if self.line_start is not None:
            line_end = self.line_end or self.line_start
            if line_end != self.line_start:
                parts.append(f"lines {self.line_start}-{line_end}")
            else:
                parts.append(f"line {self.line_start}")
        if self.timestamp_start is not None:
            start = format_timestamp(self.timestamp_start)
            end = format_timestamp(self.timestamp_end or self.timestamp_start)
            if end != start:
                parts.append(f"{start}-{end}")
            else:
                parts.append(start)
        return ", ".join(parts) if parts else "unknown"

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "page_end": self.page_end,
            "chapter": self.chapter,
            "chapter_end": self.chapter_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "display": self.display(),
        }


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def merge_locations(locations: list[SourceLocation]) -> SourceLocation:
    if not locations:
        return SourceLocation()

    chapters = [loc.chapter for loc in locations if loc.chapter is not None]
    chapter_ends = [
        loc.chapter_end or loc.chapter for loc in locations if loc.chapter is not None
    ]
    pages = [loc.page for loc in locations if loc.page is not None]
    page_ends = [loc.page_end or loc.page for loc in locations if loc.page is not None]
    line_starts = [loc.line_start for loc in locations if loc.line_start is not None]
    line_ends = [loc.line_end or loc.line_start for loc in locations if loc.line_start is not None]
    ts_starts = [loc.timestamp_start for loc in locations if loc.timestamp_start is not None]
    ts_ends = [
        loc.timestamp_end or loc.timestamp_start
        for loc in locations
        if loc.timestamp_start is not None
    ]

    return SourceLocation(
        page=min(pages) if pages else None,
        page_end=max(page_ends) if page_ends else None,
        chapter=min(chapters) if chapters else None,
        chapter_end=max(chapter_ends) if chapter_ends else None,
        line_start=min(line_starts) if line_starts else None,
        line_end=max(line_ends) if line_ends else None,
        timestamp_start=min(ts_starts) if ts_starts else None,
        timestamp_end=max(ts_ends) if ts_ends else None,
    )


@dataclass
class SourceSegment:
    text: str
    location: SourceLocation
    index: int = 0


@dataclass
class MediaItem:
    """A table or figure detected in a source, with its checkable location.

    ``markdown`` holds the rendered table body when structure was recovered
    (e.g. via pdfplumber or a markdown pipe table); figures usually have only a
    caption because the image itself cannot be turned into text.
    """

    kind: str  # "table" | "figure"
    label: str  # e.g. "Table 2", "Figure 3-1"
    caption: str = ""
    markdown: str | None = None
    location: SourceLocation = field(default_factory=SourceLocation)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "caption": self.caption,
            "markdown": self.markdown,
            "location": self.location.to_dict(),
        }


@dataclass
class LoadedSource:
    title: str
    text: str
    source_type: str
    source_ref: str
    segments: list[SourceSegment] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    load_warnings: list[str] = field(default_factory=list)
    source_key: str | None = None

    def __post_init__(self) -> None:
        if not self.segments and self.text.strip():
            self.segments = [
                SourceSegment(text=self.text.strip(), location=SourceLocation(), index=0)
            ]
        if not self.text.strip() and self.segments:
            self.text = "\n\n".join(segment.text for segment in self.segments if segment.text.strip())


class SourceLoader:
    def load_from_path(self, path):  # noqa: ANN001
        raise NotImplementedError

    def load_from_url(self, url: str) -> LoadedSource:
        raise NotImplementedError

    def supports_path(self, path) -> bool:  # noqa: ANN001
        return False

    def supports_url(self, url: str) -> bool:
        return False
