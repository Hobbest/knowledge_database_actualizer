from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.sources.youtube import YouTubeLoader, fetch_youtube_title


def test_fetch_youtube_title_from_oembed():
    payload = json.dumps({"title": "Never Gonna Give You Up"}).encode("utf-8")
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__.return_value = response

    with patch("urllib.request.urlopen", return_value=response):
        assert fetch_youtube_title("dQw4w9WgXcQ") == "Never Gonna Give You Up"


def test_fetch_youtube_title_fallback_on_oembed_failure():
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert fetch_youtube_title("abc123xyz") == "YouTube abc123xyz"


def test_loader_uses_oembed_title_when_transcript_api_succeeds():
    loader = YouTubeLoader()
    segments = [
        type("Snippet", (), {"text": "hello world", "start": 0.0, "duration": 5.0})(),
    ]

    with (
        patch("app.sources.youtube.fetch_youtube_title", return_value="Real Video Title"),
        patch.object(loader, "_fetch_via_transcript_api", return_value=segments),
    ):
        source = loader.load_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert source.title == "Real Video Title"
    assert source.source_type == "youtube"
    assert "hello world" in source.text
