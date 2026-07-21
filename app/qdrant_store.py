from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.chunking import chunk_text
from app.config import settings
from app.embeddings import EmbeddingService, get_embedding_service
from app.index_meta import save_index_meta
from app.similarity import adjusted_similarity
from app.vault import embedding_text_for_note, load_vault
from app.vault_fingerprints import note_fingerprint
from app.vectorstore import IndexedChunk, SampledChunk, SimilarChunk


def _qdrant_models() -> Any:
    try:
        from qdrant_client import models
    except ImportError as exc:
        raise RuntimeError(
            "Qdrant support is optional. Install requirements-qdrant.txt."
        ) from exc
    return models


def _field(chunk: IndexedChunk | Mapping[str, Any], name: str, default: Any = "") -> Any:
    if isinstance(chunk, Mapping):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


class QdrantVectorStore:
    """Small Qdrant collection adapter with Chroma-compatible query shapes."""

    def __init__(
        self,
        *,
        collection_name: str,
        vector_size: int,
        client: Any | None = None,
        url: str | None = None,
        api_key: str | None = None,
        embedding_service: EmbeddingService | None = None,
    ):
        if vector_size < 1:
            raise ValueError("vector_size must be positive")
        if client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RuntimeError(
                    "Qdrant support is optional. Install requirements-qdrant.txt."
                ) from exc
            client = QdrantClient(url=url, api_key=api_key) if url else QdrantClient(":memory:")
        self._client = client
        self.collection_name = collection_name
        self.vector_size = vector_size
        self._embedding_service = embedding_service
        self._ensure_collection()

    @property
    def embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def _ensure_collection(self) -> None:
        models = _qdrant_models()
        exists = False
        if hasattr(self._client, "collection_exists"):
            exists = bool(self._client.collection_exists(self.collection_name))
        else:
            try:
                self._client.get_collection(self.collection_name)
                exists = True
            except Exception:
                exists = False
        if not exists:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )

    def reset(self) -> None:
        try:
            self._client.delete_collection(collection_name=self.collection_name)
        except Exception:
            pass
        self._ensure_collection()

    def index_vault(self, vault_path: Path) -> dict[str, Any]:
        """Build a complete Qdrant index using the same chunk format as Chroma."""
        vault_path = vault_path.resolve()
        loaded = load_vault(vault_path)
        notes_by_path = {note.path.as_posix(): note for note in loaded.notes}
        chunks: list[dict[str, Any]] = []
        for note in loaded.notes:
            for chunk in chunk_text(
                embedding_text_for_note(note, notes_by_path=notes_by_path),
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
                source_label=note.title,
            ):
                chunks.append(
                    {
                        "chunk_id": f"{note.path.as_posix()}::{chunk.index}",
                        "note_path": note.path.as_posix(),
                        "note_title": note.title,
                        "text": chunk.text,
                        "heading": chunk.heading,
                        "tags": note.tags,
                        "wikilinks": note.wikilinks,
                    }
                )
        self.reset()
        batch_size = max(1, settings.chroma_index_batch_size)
        for start in range(0, len(chunks), batch_size):
            self.upsert_chunks(chunks[start : start + batch_size])
        fingerprints = {
            note.path.as_posix(): note_fingerprint(vault_path, note)
            for note in loaded.notes
        }
        save_index_meta(
            vault_path=vault_path,
            chunk_count=len(chunks),
            note_count=loaded.note_count,
            note_fingerprints=fingerprints,
            index_mode="full",
        )
        return {
            "vault_path": str(vault_path),
            "note_count": loaded.note_count,
            "link_count": loaded.link_count,
            "chunk_count": len(chunks),
            "duplicate_stems": loaded.duplicate_stems,
            "index_mode": "full",
            "indexed_notes": loaded.note_count,
            "skipped_notes": 0,
            "removed_notes": 0,
            "chunks_added": len(chunks),
        }

    def upsert_notes(self, vault_path: Path, relative_paths: list[str]) -> dict[str, Any]:
        # A full rebuild keeps deletion/replacement semantics correct across
        # Qdrant versions; incremental point filtering can be added later.
        stats = self.index_vault(vault_path)
        stats["requested_paths"] = list(relative_paths)
        return stats

    def upsert_chunks(
        self,
        chunks: Sequence[IndexedChunk | Mapping[str, Any]],
        *,
        embeddings: Sequence[Sequence[float]] | None = None,
    ) -> int:
        if not chunks:
            return 0
        texts = [str(_field(chunk, "text")) for chunk in chunks]
        vectors = (
            [list(vector) for vector in embeddings]
            if embeddings is not None
            else self.embedding_service.embed_texts(texts)
        )
        if len(vectors) != len(chunks):
            raise ValueError("Each chunk must have exactly one embedding")
        if any(len(vector) != self.vector_size for vector in vectors):
            raise ValueError(f"All embeddings must have dimension {self.vector_size}")

        models = _qdrant_models()
        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk_id = str(_field(chunk, "chunk_id"))
            payload = {
                "chunk_id": chunk_id,
                "note_path": str(_field(chunk, "note_path")),
                "note_title": str(_field(chunk, "note_title")),
                "text": str(_field(chunk, "text")),
                "heading": _field(chunk, "heading", None),
                "tags": list(_field(chunk, "tags", []) or []),
                "wikilinks": list(_field(chunk, "wikilinks", []) or []),
            }
            points.append(
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"{self.collection_name}:{chunk_id}")),
                    vector=vector,
                    payload=payload,
                )
            )
        self._client.upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def _query_vector(self, vector: Sequence[float], top_k: int) -> list[Any]:
        if hasattr(self._client, "query_points"):
            response = self._client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                limit=max(1, top_k),
                with_payload=True,
            )
            return list(getattr(response, "points", response))
        return list(
            self._client.search(
                collection_name=self.collection_name,
                query_vector=list(vector),
                limit=max(1, top_k),
                with_payload=True,
            )
        )

    def query_similar(
        self,
        text: str,
        *,
        top_k: int = 5,
        query_tags: list[str] | None = None,
    ) -> list[SimilarChunk]:
        return self.query_similar_many([text], top_k=top_k, query_tags=query_tags)[0]

    def query_similar_many(
        self,
        texts: list[str],
        *,
        top_k: int = 5,
        query_tags: list[str] | None = None,
    ) -> list[list[SimilarChunk]]:
        if not texts:
            return []
        vectors = self.embedding_service.embed_texts(texts, task_type="RETRIEVAL_QUERY")
        result: list[list[SimilarChunk]] = []
        for vector in vectors:
            matches: list[SimilarChunk] = []
            for point in self._query_vector(vector, top_k):
                payload = dict(getattr(point, "payload", None) or {})
                tags = [str(tag) for tag in payload.get("tags", [])]
                base_similarity = float(getattr(point, "score", 0.0))
                matches.append(
                    SimilarChunk(
                        chunk_id=str(payload.get("chunk_id", getattr(point, "id", ""))),
                        note_path=str(payload.get("note_path", "")),
                        note_title=str(payload.get("note_title", "")),
                        text=str(payload.get("text", "")),
                        similarity=adjusted_similarity(base_similarity, query_tags, tags),
                        heading=payload.get("heading") or None,
                        tags=tags,
                        base_similarity=base_similarity,
                    )
                )
            matches.sort(key=lambda item: item.similarity, reverse=True)
            result.append(matches)
        return result

    def _scroll(self, limit: int) -> list[Any]:
        response = self._client.scroll(
            collection_name=self.collection_name,
            limit=max(1, limit),
            with_payload=True,
            with_vectors=False,
        )
        return list(response[0] if isinstance(response, tuple) else response)

    def sample_chunks(self, *, limit: int = 200) -> list[SampledChunk]:
        samples: list[SampledChunk] = []
        for point in self._scroll(limit):
            payload = dict(getattr(point, "payload", None) or {})
            text = str(payload.get("text", ""))
            if text:
                samples.append(
                    SampledChunk(
                        chunk_id=str(payload.get("chunk_id", getattr(point, "id", ""))),
                        note_path=str(payload.get("note_path", "")),
                        text=text,
                    )
                )
        return samples

    def search_keyword(self, query: str, *, top_k: int = 10) -> list[SimilarChunk]:
        terms = [term.casefold() for term in query.split() if term]
        if not terms:
            return []
        matches: list[SimilarChunk] = []
        for point in self._scroll(max(200, top_k)):
            payload = dict(getattr(point, "payload", None) or {})
            text = str(payload.get("text", ""))
            title = str(payload.get("note_title", ""))
            heading = str(payload.get("heading", ""))
            haystack = f"{title}\n{heading}\n{text}".casefold()
            counts = [haystack.count(term) for term in terms]
            if not all(counts):
                continue
            score = float(sum(counts))
            matches.append(
                SimilarChunk(
                    chunk_id=str(payload.get("chunk_id", getattr(point, "id", ""))),
                    note_path=str(payload.get("note_path", "")),
                    note_title=title,
                    text=text,
                    similarity=score,
                    heading=heading or None,
                    tags=[str(tag) for tag in payload.get("tags", [])],
                )
            )
        matches.sort(key=lambda item: (-item.similarity, item.note_path, item.chunk_id))
        return matches[: max(1, top_k)]

    def chunk_count(self) -> int:
        result = self._client.count(collection_name=self.collection_name, exact=True)
        return int(getattr(result, "count", result))

    upsert = upsert_chunks
    query = query_similar
    sample = sample_chunks
    count = chunk_count


QdrantStore = QdrantVectorStore


def create_qdrant_store(**kwargs: Any) -> QdrantVectorStore:
    return QdrantVectorStore(**kwargs)
