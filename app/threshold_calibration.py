"""Suggest NOVEL/KNOWN thresholds from indexed vault chunk similarities."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.config import settings
from app.thresholds import recommended_thresholds_for
from app.vector_protocol import VectorStoreProtocol as VectorStore
from app.vectorstore import SampledChunk


@dataclass
class ThresholdCalibration:
    sample_size: int
    recommended_novel_threshold: float
    recommended_known_threshold: float
    same_note_samples: int
    cross_note_samples: int
    same_note_median: float | None
    cross_note_median: float | None
    provider: str
    fallback: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "sample_size": self.sample_size,
            "recommended_novel_threshold": self.recommended_novel_threshold,
            "recommended_known_threshold": self.recommended_known_threshold,
            "same_note_samples": self.same_note_samples,
            "cross_note_samples": self.cross_note_samples,
            "same_note_median": self.same_note_median,
            "cross_note_median": self.cross_note_median,
            "provider": self.provider,
            "fallback": self.fallback,
            "message": self.message,
            "current": {
                "novel": settings.novel_threshold,
                "known": settings.known_threshold,
            },
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def calibrate_thresholds(vector_store: VectorStore) -> ThresholdCalibration:
    """Estimate thresholds from same-note vs cross-note similarity samples."""
    provider = settings.embedding_provider or "local"
    fallback = recommended_thresholds_for(provider)
    limit = max(20, settings.threshold_calibration_samples)

    if vector_store.chunk_count() == 0:
        return ThresholdCalibration(
            sample_size=0,
            recommended_novel_threshold=fallback["novel_threshold"],
            recommended_known_threshold=fallback["known_threshold"],
            same_note_samples=0,
            cross_note_samples=0,
            same_note_median=None,
            cross_note_median=None,
            provider=provider,
            fallback=True,
            message="Index is empty — using provider defaults.",
        )

    samples: list[SampledChunk] = vector_store.sample_chunks(limit=limit)
    if len(samples) < 10:
        return ThresholdCalibration(
            sample_size=len(samples),
            recommended_novel_threshold=fallback["novel_threshold"],
            recommended_known_threshold=fallback["known_threshold"],
            same_note_samples=0,
            cross_note_samples=0,
            same_note_median=None,
            cross_note_median=None,
            provider=provider,
            fallback=True,
            message="Not enough indexed chunks to calibrate — using provider defaults.",
        )

    texts = [item.text for item in samples]
    matches_batch = vector_store.query_similar_many(texts, top_k=3)

    same_note_sims: list[float] = []
    cross_note_sims: list[float] = []
    for sample, matches in zip(samples, matches_batch, strict=True):
        for match in matches:
            if match.note_path == sample.note_path:
                same_note_sims.append(match.similarity)
            else:
                cross_note_sims.append(match.similarity)

    if not cross_note_sims:
        return ThresholdCalibration(
            sample_size=len(samples),
            recommended_novel_threshold=fallback["novel_threshold"],
            recommended_known_threshold=fallback["known_threshold"],
            same_note_samples=len(same_note_sims),
            cross_note_samples=0,
            same_note_median=statistics.median(same_note_sims) if same_note_sims else None,
            cross_note_median=None,
            provider=provider,
            fallback=True,
            message="Could not sample cross-note similarities — using provider defaults.",
        )

    cross_median = statistics.median(cross_note_sims)
    same_median = statistics.median(same_note_sims) if same_note_sims else None

    novel = _percentile(cross_note_sims, 0.60)
    if same_median is not None:
        known = max(novel + 0.05, _percentile(same_note_sims, 0.25))
    else:
        known = _percentile(cross_note_sims, 0.85)

    known = min(0.98, max(known, novel + 0.05))
    novel = max(0.05, min(novel, known - 0.05))

    return ThresholdCalibration(
        sample_size=len(samples),
        recommended_novel_threshold=round(novel, 3),
        recommended_known_threshold=round(known, 3),
        same_note_samples=len(same_note_sims),
        cross_note_samples=len(cross_note_sims),
        same_note_median=round(same_median, 3) if same_median is not None else None,
        cross_note_median=round(cross_median, 3),
        provider=provider,
        fallback=False,
        message="Calibrated from indexed vault chunk similarities.",
    )
