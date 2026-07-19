"""Shared pytest fixtures — hermetic, no HuggingFace / cloud calls."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import settings
from app.embeddings import (
    EmbeddingBackend,
    EmbeddingService,
    clear_query_embedding_cache,
    get_embedding_service,
)
from app.graph import KnowledgeGraph
from app.llm import clear_llm_provider_cache
from app.vectorstore import VectorStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = ROOT / "sample_vault"


class FakeEmbeddingBackend(EmbeddingBackend):
    """Deterministic vectors for unit tests (no HF download)."""

    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * 48
            lowered = text.lower()
            for i, ch in enumerate(lowered):
                vec[ord(ch) % 48] += 1.0
                if i + 1 < len(lowered):
                    bigram = (ord(ch) * 31 + ord(lowered[i + 1])) % 48
                    vec[bigram] += 0.5
            for token in lowered.split():
                h = sum(ord(c) for c in token) % 48
                vec[h] += 2.0
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


@pytest.fixture()
def sample_vault() -> Path:
    return SAMPLE_VAULT


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "vault_path", SAMPLE_VAULT)
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "embedding_model", "fake-test-model")
    monkeypatch.setattr(settings, "llm_provider", None)
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "llm_model", None)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)
    clear_llm_provider_cache()
    get_embedding_service.cache_clear()
    clear_query_embedding_cache()
    yield data
    clear_llm_provider_cache()
    get_embedding_service.cache_clear()
    clear_query_embedding_cache()


@pytest.fixture()
def fake_embeddings() -> EmbeddingService:
    return EmbeddingService(backend=FakeEmbeddingBackend())


@pytest.fixture()
def vector_store(tmp_data_dir: Path, fake_embeddings: EmbeddingService) -> VectorStore:
    return VectorStore(
        persist_dir=tmp_data_dir / "chroma",
        embedding_service=fake_embeddings,
    )


@pytest.fixture()
def indexed_store(vector_store: VectorStore, sample_vault: Path) -> VectorStore:
    vector_store.index_vault(sample_vault)
    return vector_store


@pytest.fixture()
def graph(sample_vault: Path) -> KnowledgeGraph:
    g = KnowledgeGraph()
    g.build_from_vault(sample_vault)
    return g
