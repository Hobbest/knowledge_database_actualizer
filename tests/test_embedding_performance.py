from __future__ import annotations

import sys
from types import SimpleNamespace

from app import embeddings


class _Vector(list):
    def tolist(self):
        return list(self)


def test_local_embedding_honors_device_and_onnx_backend(monkeypatch):
    calls = []

    class FakeSentenceTransformer:
        max_seq_length = 512

        def __init__(self, model_name, **kwargs):
            calls.append((model_name, kwargs))

        def encode(self, texts, **kwargs):
            return [_Vector([1.0, 0.0]) for _ in texts]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(
            embedding_device="cuda",
            embedding_backend="onnx",
            embedding_quantized=False,
            chunk_size=100,
        ),
    )

    backend = embeddings.LocalEmbeddingBackend("example/model")

    assert calls == [("example/model", {"device": "cuda", "backend": "onnx"})]
    assert backend.embed_texts(["one"]) == [[1.0, 0.0]]


def test_auto_device_is_delegated_to_sentence_transformers(monkeypatch):
    calls = []

    class FakeSentenceTransformer:
        max_seq_length = 512

        def __init__(self, model_name, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setattr(
        embeddings,
        "settings",
        SimpleNamespace(chunk_size=100),
    )

    embeddings.LocalEmbeddingBackend("example/model")

    assert calls == [{}]
