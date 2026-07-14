from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.source_identity import canonical_youtube_url, extract_youtube_video_id
from app.config import settings
from app.sources.base import (
    LoadedSource,
    SourceLoader,
    SourceLocation,
    SourceSegment,
)

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 30.0
DEFAULT_TRANSCRIPT_LANGUAGES = ("en", "en-US", "en-GB")


def youtube_transcript_language_list() -> list[str]:
    raw = settings.youtube_transcript_languages.strip()
    if not raw:
        return list(DEFAULT_TRANSCRIPT_LANGUAGES)
    languages = [item.strip() for item in raw.split(",") if item.strip()]
    return languages or list(DEFAULT_TRANSCRIPT_LANGUAGES)


def extract_video_id(url: str) -> str | None:
    return extract_youtube_video_id(url)


def fetch_youtube_title(video_id: str) -> str:
    """Best-effort title via YouTube oEmbed (no API key)."""
    watch_url = canonical_youtube_url(video_id)
    oembed_url = (
        "https://www.youtube.com/oembed?"
        + urllib.parse.urlencode({"url": watch_url, "format": "json"})
    )
    try:
        with urllib.request.urlopen(oembed_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        title = str(payload.get("title") or "").strip()
        if title:
            return title
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("YouTube oEmbed title fetch failed for %s: %s", video_id, exc)
    return f"YouTube {video_id}"


class YouTubeLoader(SourceLoader):
    def supports_url(self, url: str) -> bool:
        return extract_video_id(url) is not None

    def load_from_url(self, url: str) -> LoadedSource:
        video_id = extract_video_id(url)
        if not video_id:
            raise ValueError(f"Unsupported YouTube URL: {url}")

        title, segments = self._fetch_transcript_segments(video_id)
        text = "\n".join(segment.text for segment in segments)
        return LoadedSource(
            title=title,
            text=text,
            source_type="youtube",
            # Canonical watch URL so checkpoint resume matches youtu.be / shorts / embed.
            source_ref=canonical_youtube_url(video_id),
            segments=segments,
        )

    def _fetch_transcript_segments(self, video_id: str) -> tuple[str, list[SourceSegment]]:
        title = fetch_youtube_title(video_id)
        primary_error: Exception | None = None

        try:
            segments = self._fetch_via_transcript_api(video_id)
            if segments:
                return title, segments
        except Exception as exc:  # noqa: BLE001 - fall back to yt-dlp with context
            primary_error = exc
            logger.warning(
                "YouTube transcript API failed for %s; trying yt-dlp: %s",
                video_id,
                exc,
            )

        try:
            return self._fetch_transcript_ytdlp(video_id)
        except Exception as exc:
            if primary_error is not None:
                raise ValueError(
                    f"No transcript available for YouTube video {video_id}. "
                    f"Transcript API error: {primary_error}"
                ) from exc
            raise

    def _fetch_via_transcript_api(self, video_id: str) -> list[SourceSegment]:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound

        ytt_api = YouTubeTranscriptApi()
        languages = youtube_transcript_language_list()
        try:
            transcript = ytt_api.fetch(video_id, languages=languages)
        except NoTranscriptFound:
            transcript = self._fetch_first_available_transcript(ytt_api, video_id)
        snippets = [
            {
                "text": snippet.text.strip(),
                "start": float(snippet.start),
                "duration": float(getattr(snippet, "duration", 0.0) or 0.0),
            }
            for snippet in transcript
            if snippet.text.strip()
        ]
        if not snippets:
            return []
        return self._window_snippets(snippets)

    def _fetch_first_available_transcript(self, ytt_api, video_id: str):
        transcript_list = ytt_api.list_transcripts(video_id)
        for transcript in transcript_list:
            return transcript.fetch()
        raise ValueError(f"No transcript available for YouTube video: {video_id}")

    def _window_snippets(self, snippets: list[dict]) -> list[SourceSegment]:
        segments: list[SourceSegment] = []
        bucket: list[dict] = []
        bucket_start: float | None = None

        def flush() -> None:
            nonlocal bucket, bucket_start
            if not bucket:
                return
            text = " ".join(item["text"] for item in bucket).strip()
            end = bucket[-1]["start"] + bucket[-1]["duration"]
            segments.append(
                SourceSegment(
                    text=text,
                    location=SourceLocation(
                        timestamp_start=bucket_start,
                        timestamp_end=end,
                    ),
                    index=len(segments),
                )
            )
            bucket = []
            bucket_start = None

        for snippet in snippets:
            if bucket_start is None:
                bucket_start = snippet["start"]
            bucket.append(snippet)
            current_end = snippet["start"] + snippet["duration"]
            if current_end - bucket_start >= WINDOW_SECONDS:
                flush()

        flush()
        return segments

    def _fetch_transcript_ytdlp(self, video_id: str) -> tuple[str, list[SourceSegment]]:
        import tempfile

        import yt_dlp

        url = f"https://www.youtube.com/watch?v={video_id}"
        languages = youtube_transcript_language_list()
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": languages,
                "subtitlesformat": "vtt",
                "outtmpl": f"{tmpdir}/%(id)s",
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title") or fetch_youtube_title(video_id)

            vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                fallback_opts = {
                    **ydl_opts,
                    "subtitleslangs": ["all"],
                }
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    ydl.extract_info(url, download=True)
                vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                raise ValueError(f"No transcript available for YouTube video: {video_id}")

            snippets = self._parse_vtt(vtt_files[0].read_text(encoding="utf-8", errors="replace"))
            if not snippets:
                raise ValueError(f"Empty transcript for YouTube video: {video_id}")

            return title, self._window_snippets(snippets)

    def _parse_vtt(self, content: str) -> list[dict]:
        snippets: list[dict] = []
        timestamp_pattern = re.compile(
            r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
        )

        current_start: float | None = None
        current_end: float | None = None
        current_lines: list[str] = []

        def flush_snippet() -> None:
            nonlocal current_start, current_end, current_lines
            if current_start is None or not current_lines:
                current_start = None
                current_end = None
                current_lines = []
                return
            text = re.sub(r"\s+", " ", " ".join(current_lines)).strip()
            if text:
                snippets.append(
                    {
                        "text": text,
                        "start": current_start,
                        "duration": max(0.0, (current_end or current_start) - current_start),
                    }
                )
            current_start = None
            current_end = None
            current_lines = []

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue

            match = timestamp_pattern.match(line)
            if match:
                flush_snippet()
                current_start = self._parse_vtt_timestamp(match.group("start"))
                current_end = self._parse_vtt_timestamp(match.group("end"))
                continue

            if line.isdigit():
                continue

            clean = re.sub(r"<[^>]+>", "", line).strip()
            if clean:
                current_lines.append(clean)

        flush_snippet()
        return snippets

    @staticmethod
    def _parse_vtt_timestamp(value: str) -> float:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
