from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha1
from threading import Lock

from app.config import settings
from app.llm import call_with_retry

logger = logging.getLogger(__name__)

GEMINI_EMBEDDING_BATCH_SIZE = 200
QUERY_EMBEDDING_CACHE_SIZE = 2048
_QUERY_EMBEDDING_CACHE: OrderedDict[tuple[str, str, str, str], tuple[float, ...]] = (
    OrderedDict()
)
_QUERY_EMBEDDING_CACHE_LOCK = Lock()


def clear_query_embedding_cache() -> None:
    with _QUERY_EMBEDDING_CACHE_LOCK:
        _QUERY_EMBEDDING_CACHE.clear()

# Rough chars-per-token for English text, used to convert model token limits
# into character budgets for CHUNK_SIZE.
CHARS_PER_TOKEN = 4

# Input capacity (tokens) of embedding models this app knows about. Text beyond
# the limit is SILENTLY TRUNCATED by the model, so similarity — and therefore
# the novelty verdict — only sees the start of each chunk. Keys are
# (provider, lowercased model name).
_KNOWN_EMBEDDING_INPUT_TOKENS: dict[tuple[str, str], int] = {
    ("local", "all-minilm-l6-v2"): 256,
    ("local", "all-minilm-l12-v2"): 256,
    ("local", "sentence-transformers/all-minilm-l6-v2"): 256,
    ("local", "sentence-transformers/all-minilm-l12-v2"): 256,
    ("local", "all-mpnet-base-v2"): 384,
    ("local", "sentence-transformers/all-mpnet-base-v2"): 384,
    ("local", "bge-small-en-v1.5"): 512,
    ("local", "bge-small-en"): 512,
    ("local", "sentence-transformers/bge-small-en-v1.5"): 512,
    ("local", "BAAI/bge-small-en-v1.5"): 512,
    ("gemini", "gemini-embedding-001"): 2048,
    ("gemini", "text-embedding-004"): 2048,
    ("gemini", "models/gemini-embedding-001"): 2048,
    ("gemini", "models/text-embedding-004"): 2048,
}


def max_embedding_input_chars(
    provider: str | None = None,
    model: str | None = None,
) -> int | None:
    """Approximate input capacity of the configured embedding model, in chars.

    Returns None when the model is not in the known table (e.g. an arbitrary
    sentence-transformers model); the local backend then checks the real
    ``max_seq_length`` when the model is loaded.
    """
    key = (
        (provider or settings.embedding_provider or "local").lower(),
        (model or settings.embedding_model or "").lower(),
    )
    tokens = _KNOWN_EMBEDDING_INPUT_TOKENS.get(key)
    return tokens * CHARS_PER_TOKEN if tokens else None


def chunk_size_error() -> str | None:
    """Error text when CHUNK_SIZE cannot fit the embedding model, else None."""
    limit = max_embedding_input_chars()
    if limit is not None and settings.chunk_size > limit:
        tokens = limit // CHARS_PER_TOKEN
        return (
            f"CHUNK_SIZE={settings.chunk_size} exceeds what "
            f"{settings.embedding_provider}/{settings.embedding_model} can embed "
            f"(~{limit} chars = {tokens} tokens). Text past that limit is silently "
            "ignored during similarity search, so novelty verdicts would be based "
            "only on how each chunk begins. "
            f"Set CHUNK_SIZE <= {limit} (and CHUNK_OVERLAP below it), then re-index the vault."
        )
    return None


class EmbeddingBackend(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        raise NotImplementedError


class LocalEmbeddingBackend(EmbeddingBackend):
    def __init__(self, model_name: str):
        if _looks_like_gemini_generation_model(model_name):
            raise ValueError(
                f"'{model_name}' is a Gemini chat model, not a local embedding model. "
                "Set LLM_PROVIDER=gemini and LLM_MODEL for note drafting. "
                "For embeddings, use EMBEDDING_PROVIDER=gemini with EMBEDDING_MODEL=gemini-embedding-001, "
                "or keep EMBEDDING_PROVIDER=local with a sentence-transformers model like all-MiniLM-L6-v2."
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings require sentence-transformers. "
                "Install the project's base requirements."
            ) from exc

        self.model_name = model_name
        device = str(getattr(settings, "embedding_device", "auto") or "auto").lower()
        backend = str(
            getattr(settings, "embedding_backend", "sentence_transformers")
            or "sentence_transformers"
        ).lower()
        if device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError(
                f"Unsupported EMBEDDING_DEVICE '{device}'. Use auto, cpu, cuda, or mps."
            )
        if backend not in {"sentence_transformers", "onnx"}:
            raise ValueError(
                f"Unsupported EMBEDDING_BACKEND '{backend}'. "
                "Use sentence_transformers or onnx."
            )

        model_options: dict[str, str] = {}
        # Omitting device delegates "auto" selection to sentence-transformers,
        # which handles CUDA, MPS, and CPU availability in one place.
        if device != "auto":
            model_options["device"] = device
        if backend == "onnx":
            model_options["backend"] = "onnx"

        try:
            self._model = SentenceTransformer(model_name, **model_options)
        except (ImportError, ModuleNotFoundError) as exc:
            if backend == "onnx":
                raise RuntimeError(
                    "ONNX embeddings require sentence-transformers with ONNX support, "
                    "optimum[onnxruntime], and onnxruntime. Install "
                    "requirements-performance.txt."
                ) from exc
            raise
        except TypeError as exc:
            if backend == "onnx" and "backend" in str(exc):
                raise RuntimeError(
                    "The installed sentence-transformers version does not support "
                    "backend='onnx'. Upgrade it and install requirements-performance.txt."
                ) from exc
            raise

        self.device = device
        self.backend = backend
        self.quantized = bool(getattr(settings, "embedding_quantized", False))
        if self.quantized and backend != "onnx":
            logger.warning(
                "EMBEDDING_QUANTIZED is only applicable to the ONNX backend; "
                "using regular sentence-transformers inference."
            )

        # Authoritative capacity check for models missing from the known table:
        # sentence-transformers exposes the real token window after loading.
        max_tokens = getattr(self._model, "max_seq_length", None)
        if max_tokens and settings.chunk_size > max_tokens * CHARS_PER_TOKEN:
            logger.warning(
                "CHUNK_SIZE=%d exceeds ~%d chars (%d tokens) that '%s' can embed; "
                "text past that limit is silently ignored during similarity search. "
                "Lower CHUNK_SIZE and re-index.",
                settings.chunk_size,
                max_tokens * CHARS_PER_TOKEN,
                max_tokens,
                model_name,
            )

    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]


class GeminiEmbeddingBackend(EmbeddingBackend):
    def __init__(self, api_key: str, model_name: str):
        if _looks_like_gemini_generation_model(model_name):
            raise ValueError(
                f"'{model_name}' is a Gemini generation model, not an embedding model. "
                "Set it as LLM_MODEL instead. For embeddings use e.g. gemini-embedding-001."
            )

        from google import genai

        self.model_name = model_name
        self._client = genai.Client(api_key=api_key)

    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []

        from google.genai import types

        embed_task = task_type or "RETRIEVAL_DOCUMENT"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), GEMINI_EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + GEMINI_EMBEDDING_BATCH_SIZE]

            def _embed_batch(batch_texts: list[str] = batch) -> list[list[float]]:
                response = self._client.models.embed_content(
                    model=self.model_name,
                    contents=batch_texts,
                    config=types.EmbedContentConfig(task_type=embed_task),
                )
                if not response.embeddings:
                    raise RuntimeError(
                        f"Gemini returned no embeddings for model '{self.model_name}'"
                    )
                return [_normalize(list(embedding.values or [])) for embedding in response.embeddings]

            vectors.extend(call_with_retry(_embed_batch))

        return vectors


def _looks_like_gemini_generation_model(model_name: str) -> bool:
    lower = model_name.lower()
    if "embedding" in lower or "text-embedding" in lower:
        return False
    return bool(re.search(r"\bgemini[\w.-]*(flash|pro|lite|ultra)\b", lower))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class EmbeddingService:
    def __init__(self, backend: EmbeddingBackend | None = None):
        self._backend = backend or _create_embedding_backend()

    def embed_texts(self, texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        if task_type == "RETRIEVAL_QUERY":
            return self._embed_query_texts_cached(texts, task_type=task_type)
        return self._embed_uncached(texts, task_type=task_type)

    def _embed_uncached(
        self,
        texts: list[str],
        *,
        task_type: str | None = None,
    ) -> list[list[float]]:
        # Gemini retries per API sub-batch internally; other backends wrap once.
        if isinstance(self._backend, GeminiEmbeddingBackend):
            return self._backend.embed_texts(texts, task_type=task_type)
        return call_with_retry(
            lambda: self._backend.embed_texts(texts, task_type=task_type)
        )

    def _embed_query_texts_cached(
        self,
        texts: list[str],
        *,
        task_type: str,
    ) -> list[list[float]]:
        namespace = (
            (settings.embedding_provider or "local").lower(),
            settings.embedding_model or "",
            task_type,
        )
        keys = [(*namespace, sha1(text.encode("utf-8")).hexdigest()) for text in texts]
        vectors: list[list[float] | None] = [None] * len(texts)
        misses: dict[tuple[str, str, str, str], tuple[int, str]] = {}

        with _QUERY_EMBEDDING_CACHE_LOCK:
            for index, key in enumerate(keys):
                cached = _QUERY_EMBEDDING_CACHE.get(key)
                if cached is not None:
                    _QUERY_EMBEDDING_CACHE.move_to_end(key)
                    vectors[index] = list(cached)
                elif key not in misses:
                    misses[key] = (index, texts[index])

        if misses:
            miss_keys = list(misses)
            miss_vectors = self._embed_uncached(
                [misses[key][1] for key in miss_keys],
                task_type=task_type,
            )
            by_key = dict(zip(miss_keys, miss_vectors, strict=True))
            with _QUERY_EMBEDDING_CACHE_LOCK:
                for key, vector in by_key.items():
                    _QUERY_EMBEDDING_CACHE[key] = tuple(vector)
                    _QUERY_EMBEDDING_CACHE.move_to_end(key)
                while len(_QUERY_EMBEDDING_CACHE) > QUERY_EMBEDDING_CACHE_SIZE:
                    _QUERY_EMBEDDING_CACHE.popitem(last=False)
            for index, key in enumerate(keys):
                if vectors[index] is None:
                    vectors[index] = list(by_key[key])

        return [vector for vector in vectors if vector is not None]

    def embed_text(self, text: str, *, task_type: str | None = None) -> list[float]:
        return self.embed_texts([text], task_type=task_type)[0]


def _create_embedding_backend() -> EmbeddingBackend:
    provider = (settings.embedding_provider or "local").lower()
    model_name = settings.embedding_model

    if provider == "gemini":
        api_key = settings.embedding_api_key or settings.llm_api_key
        if not api_key:
            raise ValueError(
                "Gemini embeddings require EMBEDDING_API_KEY or LLM_API_KEY to be set."
            )
        return GeminiEmbeddingBackend(api_key=api_key, model_name=model_name)

    if provider != "local":
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER '{settings.embedding_provider}'. "
            "Use 'local' or 'gemini'."
        )

    return LocalEmbeddingBackend(model_name)


def embedding_collection_suffix() -> str:
    safe_model = re.sub(r"[^a-zA-Z0-9_-]+", "_", settings.embedding_model)
    return f"{settings.embedding_provider}_{safe_model}"


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
