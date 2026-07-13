from __future__ import annotations

from app.llm_budget import LLMBudget
from app.source_identity import normalize_source_key
from app.thresholds import recommended_thresholds_for


def test_llm_budget_call_cap():
    budget = LLMBudget(max_calls=2, max_input_chars=10_000)
    assert budget.can_call(100)
    budget.record(100)
    budget.record(100)
    assert not budget.can_call(1)
    reason = budget.refuse(1)
    assert "call budget" in reason.lower()
    assert budget.exhausted


def test_llm_budget_char_cap():
    budget = LLMBudget(max_calls=100, max_input_chars=50)
    assert budget.can_call(40)
    budget.record(40)
    assert not budget.can_call(20)
    assert "input budget" in budget.refuse(20).lower()


def test_recommended_thresholds():
    local = recommended_thresholds_for("local")
    gemini = recommended_thresholds_for("gemini")
    assert local["novel_threshold"] < local["known_threshold"]
    assert gemini["novel_threshold"] > local["novel_threshold"]


def test_recommended_thresholds_by_model():
    mini = recommended_thresholds_for("local", "all-MiniLM-L6-v2")
    mpnet = recommended_thresholds_for("local", "sentence-transformers/all-mpnet-base-v2")
    bge = recommended_thresholds_for("local", "BAAI/bge-small-en-v1.5")
    assert mini["novel_threshold"] < mpnet["novel_threshold"] <= bge["novel_threshold"]
    assert mini["known_threshold"] < mpnet["known_threshold"] <= bge["known_threshold"]


def test_file_source_key_posix():
    assert normalize_source_key("pdf", r"C:\docs\a.pdf").replace("\\", "/").endswith("a.pdf")


def test_youtube_source_key_canonical():
    a = normalize_source_key("youtube", "https://youtu.be/dQw4w9WgXcQ")
    b = normalize_source_key("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert a == b == "youtube:dQw4w9WgXcQ"


def test_ollama_provider_selection(monkeypatch):
    from app.config import settings
    from app.llm import OllamaProvider, clear_llm_provider_cache, get_llm_provider

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.2")
    monkeypatch.setattr(settings, "llm_api_key", None)
    clear_llm_provider_cache()
    assert settings.llm_enabled
    provider = get_llm_provider()
    assert isinstance(provider, OllamaProvider)

    monkeypatch.setattr(settings, "llm_provider", "local")
    clear_llm_provider_cache()
    assert isinstance(get_llm_provider(), OllamaProvider)

    monkeypatch.setattr(settings, "llm_provider", "not-a-provider")
    monkeypatch.setattr(settings, "llm_api_key", "dummy")
    clear_llm_provider_cache()
    assert settings.llm_enabled
    try:
        get_llm_provider()
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unsupported" in str(exc)
