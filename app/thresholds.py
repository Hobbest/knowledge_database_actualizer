"""Recommended novelty thresholds per embedding backend and model.

Cosine similarity distributions differ across models, so the same
NOVEL_THRESHOLD / KNOWN_THRESHOLD can be too strict or too loose when you
switch providers or local sentence-transformers models. These are starting
points — tune on your vault or use threshold calibration.
"""

from __future__ import annotations

from app.config import settings

# Provider-level defaults (fallback when the model has no specific entry).
RECOMMENDED_THRESHOLDS: dict[str, dict[str, float]] = {
    "local": {
        "novel_threshold": 0.55,
        "known_threshold": 0.75,
    },
    "gemini": {
        "novel_threshold": 0.65,
        "known_threshold": 0.82,
    },
}

# Optional stronger local models (MiniLM remains the default for speed).
RECOMMENDED_THRESHOLDS_BY_MODEL: dict[str, dict[str, float]] = {
    "all-minilm-l6-v2": {"novel_threshold": 0.55, "known_threshold": 0.75},
    "all-minilm-l12-v2": {"novel_threshold": 0.55, "known_threshold": 0.75},
    "all-mpnet-base-v2": {"novel_threshold": 0.58, "known_threshold": 0.78},
    "bge-small-en-v1.5": {"novel_threshold": 0.60, "known_threshold": 0.80},
    "bge-small-en": {"novel_threshold": 0.60, "known_threshold": 0.80},
}


def _normalize_model_name(model: str) -> str:
    name = (model or "").lower().strip()
    if name.startswith("sentence-transformers/"):
        name = name.split("/", 1)[1]
    return name


def recommended_thresholds_for(
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, float]:
    resolved_provider = (provider or settings.embedding_provider or "local").lower()
    resolved_model = _normalize_model_name(
        model if model is not None else settings.embedding_model or ""
    )

    if resolved_provider == "local" and resolved_model:
        for key, thresholds in RECOMMENDED_THRESHOLDS_BY_MODEL.items():
            if key in resolved_model:
                return dict(thresholds)

    return dict(RECOMMENDED_THRESHOLDS.get(resolved_provider, RECOMMENDED_THRESHOLDS["local"]))


def threshold_mismatch_warnings() -> list[str]:
    """Warn when configured thresholds look far from the provider/model defaults."""
    recommended = recommended_thresholds_for()
    warnings: list[str] = []
    novel_delta = abs(settings.novel_threshold - recommended["novel_threshold"])
    known_delta = abs(settings.known_threshold - recommended["known_threshold"])
    if novel_delta >= 0.15 or known_delta >= 0.15:
        model = _normalize_model_name(settings.embedding_model or "")
        warnings.append(
            f"Thresholds (novel={settings.novel_threshold}, known={settings.known_threshold}) "
            f"differ from recommended values for EMBEDDING_PROVIDER={settings.embedding_provider} "
            f"EMBEDDING_MODEL={model or settings.embedding_model} "
            f"(novel={recommended['novel_threshold']}, known={recommended['known_threshold']}). "
            "Novelty verdicts may be skewed — see README."
        )
    return warnings
