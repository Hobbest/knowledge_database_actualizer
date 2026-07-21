from __future__ import annotations

from types import SimpleNamespace

from app.embeddings import EmbeddingBackend, EmbeddingService
from app.qdrant_store import QdrantVectorStore
from app.vectorstore import IndexedChunk


class _Embeddings(EmbeddingBackend):
    def embed_texts(self, texts, *, task_type=None):
        return [[1.0, 0.0] for _ in texts]


class _Models:
    class Distance:
        COSINE = "cosine"

    class VectorParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class PointStruct:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


class _Client:
    def __init__(self):
        self.exists = False
        self.points = []

    def collection_exists(self, name):
        return self.exists

    def create_collection(self, **kwargs):
        self.exists = True

    def upsert(self, *, points, **kwargs):
        self.points = points

    def query_points(self, **kwargs):
        point = self.points[0]
        return SimpleNamespace(
            points=[SimpleNamespace(id=point.id, score=0.8, payload=point.payload)]
        )

    def count(self, **kwargs):
        return SimpleNamespace(count=len(self.points))

    def scroll(self, **kwargs):
        return ([SimpleNamespace(id=p.id, payload=p.payload) for p in self.points], None)


def test_qdrant_adapter_upsert_query_count_and_sample(monkeypatch):
    monkeypatch.setattr("app.qdrant_store._qdrant_models", lambda: _Models)
    client = _Client()
    store = QdrantVectorStore(
        collection_name="test",
        vector_size=2,
        client=client,
        embedding_service=EmbeddingService(_Embeddings()),
    )
    chunk = IndexedChunk("a", "note.md", "Note", "knowledge text", None, [])

    assert store.upsert_chunks([chunk]) == 1
    assert store.chunk_count() == 1
    assert store.query_similar("knowledge")[0].chunk_id == "a"
    assert store.sample_chunks()[0].note_path == "note.md"


def test_qdrant_adapter_indexes_vault(monkeypatch, tmp_path):
    monkeypatch.setattr("app.qdrant_store._qdrant_models", lambda: _Models)
    monkeypatch.setattr("app.qdrant_store.save_index_meta", lambda **kwargs: kwargs)
    (tmp_path / "note.md").write_text("# Note\n\nUseful indexed knowledge.")
    store = QdrantVectorStore(
        collection_name="vault",
        vector_size=2,
        client=_Client(),
        embedding_service=EmbeddingService(_Embeddings()),
    )

    stats = store.index_vault(tmp_path)

    assert stats["note_count"] == 1
    assert stats["chunk_count"] >= 1
    assert store.query_similar("knowledge")[0].note_path == "note.md"
