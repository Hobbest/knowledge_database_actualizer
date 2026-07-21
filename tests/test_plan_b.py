from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.analytics import load_analytics, record_counts
from app.chat import answer_vault_question
from app.config import settings
from app.prompt_domains import load_domain_rules
from app.sources.audio import AudioVideoLoader
from app.update_detection import detect_update
from app.vectorstore import SimilarChunk
from app.vision import describe_image


def test_audio_loader_builds_timestamped_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            return (
                iter(
                    [
                        SimpleNamespace(text=" First sentence ", start=1.5, end=3.0),
                        SimpleNamespace(text="Second sentence", start=3.0, end=5.25),
                    ]
                ),
                SimpleNamespace(language="en"),
            )

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    path = tmp_path / "meeting.mp3"
    path.write_bytes(b"fake")
    source = AudioVideoLoader().load_from_path(path)
    assert source.source_type == "audio_video"
    assert source.segments[0].location.timestamp_start == 1.5
    assert source.segments[1].location.timestamp_end == 5.25


def test_audio_loader_dependency_error_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    path = tmp_path / "meeting.wav"
    path.write_bytes(b"fake")
    with pytest.raises(RuntimeError, match="requirements-audio.txt"):
        AudioVideoLoader().load_from_path(path)


def test_update_detection_flags_negation_dates_and_numbers():
    assert detect_update("The feature is not enabled.", "The feature is enabled.", similarity=0.9).update_type == "contradiction"
    assert detect_update("Released in 2026.", "Released in 2024.", similarity=0.9).update_type == "update"
    assert detect_update("Latency is 25 ms.", "Latency is 40 ms.", similarity=0.9).update_type == "update"


def test_prompt_domain_is_bundled_and_invalid_names_are_safe():
    load_domain_rules.cache_clear()
    assert any("causation" in rule for rule in load_domain_rules("research"))
    assert load_domain_rules("../../secrets") == ()


def test_analytics_records_atomic_daily_counts(tmp_data_dir: Path):
    record_counts(analyzed_sources=1)
    record_counts(written_notes=2)
    data = load_analytics()
    assert data["totals"] == {"analyzed_sources": 1, "written_notes": 2}
    assert len(data["days"]) == 1


def test_chat_uses_retrieved_context(monkeypatch: pytest.MonkeyPatch):
    class Store:
        def query_similar(self, _question: str, *, top_k: int):
            assert top_k == 5
            return [
                SimilarChunk(
                    chunk_id="a::0",
                    note_path="a.md",
                    note_title="Alpha",
                    text="Alpha is the first letter.",
                    similarity=0.9,
                )
            ]

    class Provider:
        def complete(self, prompt: str, **_kwargs):
            assert "Alpha is the first letter" in prompt
            return "Alpha is first [1]."

    monkeypatch.setattr("app.chat.get_llm_provider", lambda: Provider())
    result = answer_vault_question("What is Alpha?", Store())  # type: ignore[arg-type]
    assert result.answer.endswith("[1].")
    assert result.citations[0]["note_path"] == "a.md"


def test_vision_disabled_does_not_call_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "vision_media_enabled", False)
    assert describe_image(b"image") is None
