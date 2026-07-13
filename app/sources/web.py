from __future__ import annotations

import logging
import urllib.error
import urllib.request

from app.config import settings
from app.media import extract_markdown_tables, find_captions
from app.source_identity import canonical_web_url, extract_youtube_video_id
from app.sources.base import (
    LoadedSource,
    MediaItem,
    SourceLoader,
    SourceLocation,
    SourceSegment,
)
from app.sources.text import segments_from_markdown

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 20
# Some sites block the default urllib UA outright; a browser-ish UA is enough.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


class WebArticleLoader(SourceLoader):
    """Extract the main article text from a generic web page.

    Registered after ``YouTubeLoader`` in the dispatcher, so YouTube URLs never
    reach this loader; ``supports_url`` still excludes them defensively.
    """

    def supports_url(self, url: str) -> bool:
        candidate = url.strip().lower()
        if not candidate.startswith(("http://", "https://")):
            return False
        return extract_youtube_video_id(url) is None

    def load_from_url(self, url: str) -> LoadedSource:
        url = url.strip()
        try:
            html = fetch_html(url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(f"Could not fetch web page {url}: {exc}") from exc

        title, markdown = self._extract_article(html, url)
        if not markdown.strip():
            raise ValueError(
                f"No readable article content found at {url}. "
                "The page may be behind a paywall, require JavaScript, or not be an article."
            )

        raw_lines = markdown.splitlines()
        segments = segments_from_markdown(raw_lines)
        media = self._detect_media(raw_lines) if settings.include_media else []

        return LoadedSource(
            title=title,
            text=markdown.strip(),
            source_type="web",
            source_ref=canonical_web_url(url),
            segments=segments,
            media=media,
        )

    def _extract_article(self, html: str, url: str) -> tuple[str, str]:
        """Return (title, markdown body) for the main content of the page."""
        import trafilatura

        markdown = (
            trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_tables=True,
                include_links=False,
                include_comments=False,
            )
            or ""
        )

        title = ""
        try:
            metadata = trafilatura.extract_metadata(html)
            if metadata is not None:
                title = (metadata.title or "").strip()
        except Exception as exc:  # noqa: BLE001 - title is best-effort
            logger.debug("Metadata extraction failed for %s: %s", url, exc)
        return title or url, markdown

    def _detect_media(self, raw_lines: list[str]) -> list[MediaItem]:
        media: list[MediaItem] = extract_markdown_tables(raw_lines)
        for line_number, line in enumerate(raw_lines, start=1):
            location = SourceLocation(line_start=line_number, line_end=line_number)
            media.extend(find_captions(line, location))
        return media


# Re-exported for tests that build segments directly.
__all__ = ["WebArticleLoader", "fetch_html", "SourceSegment"]
