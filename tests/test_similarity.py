from __future__ import annotations

from app.config import settings
from app.similarity import adjusted_similarity, tag_overlap_boost


def test_tag_overlap_boost_disabled(monkeypatch):
    monkeypatch.setattr(settings, "tag_similarity_enabled", False)
    assert tag_overlap_boost(["python"], ["python"]) == 0.0


def test_tag_overlap_boost_scales_and_caps(monkeypatch):
    monkeypatch.setattr(settings, "tag_similarity_enabled", True)
    monkeypatch.setattr(settings, "tag_similarity_boost_per_tag", 0.06)
    monkeypatch.setattr(settings, "tag_similarity_max_boost", 0.15)

    assert tag_overlap_boost(["python"], ["python"]) == 0.06
    assert tag_overlap_boost(["A", "B", "C"], ["A", "B", "C"]) == 0.15
    assert tag_overlap_boost(["python"], ["rust"]) == 0.0
    assert tag_overlap_boost(None, ["python"]) == 0.0


def test_adjusted_similarity_caps_at_one(monkeypatch):
    monkeypatch.setattr(settings, "tag_similarity_enabled", True)
    monkeypatch.setattr(settings, "tag_similarity_boost_per_tag", 0.10)
    monkeypatch.setattr(settings, "tag_similarity_max_boost", 0.20)

    assert adjusted_similarity(0.95, ["a", "b"], ["a", "b"]) == 1.0
    assert adjusted_similarity(0.50, ["x"], ["x"]) == 0.60
