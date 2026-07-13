from __future__ import annotations

from app.config import settings
from app.llm import clear_llm_provider_cache, get_llm_provider, get_llm_provider_uncached


def test_llm_provider_is_cached(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.2")
    monkeypatch.setattr(settings, "llm_api_key", None)
    clear_llm_provider_cache()

    first = get_llm_provider()
    second = get_llm_provider()
    assert first is not None
    assert first is second


def test_clear_llm_provider_cache_rebuilds(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.2")
    monkeypatch.setattr(settings, "llm_api_key", None)
    clear_llm_provider_cache()

    cached = get_llm_provider()
    fresh = get_llm_provider_uncached()
    assert cached is not fresh
    assert type(cached) is type(fresh)
