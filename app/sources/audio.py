from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.sources.base import LoadedSource, SourceLoader, SourceLocation, SourceSegment

AUDIO_VIDEO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".webm"})


class AudioVideoLoader(SourceLoader):
    """Transcribe local audio/video with the optional faster-whisper package."""

    def supports_path(self, path: Path) -> bool:
        return path.suffix.lower() in AUDIO_VIDEO_EXTENSIONS

    def load_from_path(self, path: Path) -> LoadedSource:
        if not self.supports_path(path):
            raise ValueError(f"Unsupported audio/video type: {path.suffix}")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Audio/video transcription requires faster-whisper. "
                "Install it with: pip install -r requirements-audio.txt"
            ) from exc

        model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        raw_segments, info = model.transcribe(
            str(path),
            beam_size=settings.whisper_beam_size,
            language=settings.whisper_language or None,
        )
        segments: list[SourceSegment] = []
        for raw in raw_segments:
            text = str(getattr(raw, "text", "")).strip()
            if not text:
                continue
            segments.append(
                SourceSegment(
                    text=text,
                    location=SourceLocation(
                        timestamp_start=float(getattr(raw, "start", 0.0)),
                        timestamp_end=float(getattr(raw, "end", 0.0)),
                    ),
                    index=len(segments),
                )
            )
        if not segments:
            raise ValueError(f"No speech could be transcribed from {path.name}")
        language = str(getattr(info, "language", "") or "").strip()
        return LoadedSource(
            title=path.stem,
            text="\n".join(segment.text for segment in segments),
            source_type="audio_video",
            source_ref=str(path),
            segments=segments,
            tags=[f"language/{language}"] if language else [],
        )
