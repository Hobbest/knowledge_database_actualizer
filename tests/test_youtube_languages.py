from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.sources.youtube import YouTubeLoader, youtube_transcript_language_list


def test_youtube_transcript_language_list_parses_config(monkeypatch: pytest.MonkeyPatch):
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "youtube_transcript_languages", " ru , de ")
    assert youtube_transcript_language_list() == ["ru", "de"]


def test_youtube_transcript_language_list_defaults_when_empty(monkeypatch: pytest.MonkeyPatch):
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "youtube_transcript_languages", "  ")
    assert youtube_transcript_language_list() == ["en", "en-US", "en-GB"]


def test_transcript_api_uses_configured_languages(monkeypatch: pytest.MonkeyPatch):
    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "youtube_transcript_languages", "fr,de")
    loader = YouTubeLoader()
    snippet = type("Snippet", (), {"text": "bonjour", "start": 0.0, "duration": 2.0})()
    fetched = [snippet]

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as api_cls:
        api = api_cls.return_value
        api.fetch.return_value = fetched
        segments = loader._fetch_via_transcript_api("abc123")

    api.fetch.assert_called_once_with("abc123", languages=["fr", "de"])
    assert segments and segments[0].text == "bonjour"


def test_transcript_api_falls_back_to_first_available_language():
    loader = YouTubeLoader()
    snippet = type("Snippet", (), {"text": "hola", "start": 0.0, "duration": 2.0})()
    fallback = MagicMock()
    fallback.fetch.return_value = [snippet]

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as api_cls:
        from youtube_transcript_api._errors import NoTranscriptFound

        api = api_cls.return_value
        api.fetch.side_effect = NoTranscriptFound("video", ["en"], MagicMock())
        api.list_transcripts.return_value = [fallback]
        segments = loader._fetch_via_transcript_api("abc123")

    api.list_transcripts.assert_called_once_with("abc123")
    fallback.fetch.assert_called_once_with()
    assert segments and segments[0].text == "hola"
