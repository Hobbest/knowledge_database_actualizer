"""Cloud provider privacy warnings on /api/status."""

from __future__ import annotations

from app.api.admin import cloud_provider_privacy_warnings
from app.config import settings


def test_local_providers_emit_no_privacy_warning(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.2")
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert cloud_provider_privacy_warnings() == []


def test_cloud_llm_and_embedding_warn(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "embedding_provider", "gemini")
    warnings = cloud_provider_privacy_warnings()
    assert any("LLM provider" in w for w in warnings)
    assert any("Embedding provider" in w for w in warnings)
