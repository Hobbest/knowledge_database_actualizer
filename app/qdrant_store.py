from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.config import settings
from app.embeddings import EmbeddingService, get_embedding_service
from app.indexing import (
    IndexedChunk,
    build_chunks_for_note,
    finalize_index_meta,
    fingerprints_for_vault,
    index_stats_from_plan,
    iter_vault_index_chunks,
    merge_note_fingerprints,
    plan_vault_index,
)
from app.runtime import INDEX_LOCK
from app.similarity import adjusted_similarity
from app.vault import load_note, load_vault
from app.vectorstore import SampledChunk, SimilarChunk


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
        with INDEX_LOCK:
            try:
                self._client.delete_collection(collection_name=self.collection_name)
            except Exception:
                pass
            self._ensure_collection()

    def _delete_note_chunks(self, note_path: str) -> None:
        models = _qdrant_models()
        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="note_path",
                                match=models.MatchValue(value=note_path),
                            )
                        ]
                    )
                ),
            )
        except Exception:
            pass

    def index_vault(self, vault_path: Path) -> dict[str, Any]:
        """Embed and index the vault using shared fingerprint-aware orchestration."""
        plan = plan_vault_index(vault_path)
        if plan.full_rebuild:
            with INDEX_LOCK:
                try:
                    self._client.delete_collection(collection_name=self.collection_name)
                except Exception:
                    pass
                self._ensure_collection()

        batch_size = max(1, settings.chroma_index_batch_size)
        indexed_note_count = 0
        chunk_delta = 0
        pending: list[IndexedChunk] = []

        with INDEX_LOCK:
            for rel in plan.removed_paths:
                self._delete_note_chunks(rel)

            for note, note_chunks in iter_vault_index_chunks(plan):
                rel = note.path.as_posix()
                self._delete_note_chunks(rel)
                if note_chunks:
                    indexed_note_count += 1
                pending.extend(note_chunks)
                while len(pending) >= batch_size:
                    batch = pending[:batch_size]
                    del pending[:batch_size]
                    chunk_delta += self.upsert_chunks(batch)

            if pending:
                chunk_delta += self.upsert_chunks(pending)

            total_chunks = self.chunk_count()

        new_fingerprints = fingerprints_for_vault(plan.vault_path, list(plan.current_notes.values()))
        stats = index_stats_from_plan(
            plan,
            chunk_count=total_chunks,
            indexed_note_count=indexed_note_count,
            chunk_delta=chunk_delta,
        )
        finalize_index_meta(
            vault_path=plan.vault_path,
            chunk_count=total_chunks,
            note_count=plan.note_count,
            note_fingerprints=new_fingerprints,
            index_mode=str(stats["index_mode"]),
        )
        return stats

    def upsert_notes(self, vault_path: Path, relative_paths: list[str]) -> dict[str, Any]:
        vault_path = vault_path.resolve()
        unique_paths = list(dict.fromkeys(p for p in relative_paths if p))
        missing: list[str] = []
        prepared: list[tuple[str, list[IndexedChunk]]] = []

        notes_by_path = None
        link_index = None
        if settings.transclude_depth > 0:
            from app.indexing import build_wikilink_context

            vault_result = load_vault(vault_path)
            notes_by_path, link_index = build_wikilink_context(vault_result)

        for rel in unique_paths:
            note = load_note(vault_path, rel)
            if note is None:
                missing.append(rel)
                prepared.append((rel, []))
                continue
            prepared.append(
                (
                    rel,
                    build_chunks_for_note(
                        note,
                        notes_by_path=notes_by_path,
                        link_index=link_index,
                    ),
                )
            )

        indexed = 0
        chunk_count = 0
        with INDEX_LOCK:
            for rel, note_chunks in prepared:
                self._delete_note_chunks(rel)
                if not note_chunks:
                    continue
                chunk_count += self.upsert_chunks(note_chunks)
                indexed += 1
            total_chunks = self.chunk_count()

        finalize_index_meta(
            vault_path=vault_path,
            chunk_count=total_chunks,
            note_count=None,
            note_fingerprints=merge_note_fingerprints(vault_path, unique_paths),
            index_mode="incremental",
        )
        return {
            "indexed_notes": indexed,
            "missing_notes": missing,
            "chunk_count_added": chunk_count,
            "chunk_count": total_chunks,
        }

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
        with INDEX_LOCK:
            result = self._client.count(collection_name=self.collection_name, exact=True)
            return int(getattr(result, "count", result))

    upsert = upsert_chunks
    query = query_similar
    sample = sample_chunks
    count = chunk_count


QdrantStore = QdrantVectorStore


def create_qdrant_store(**kwargs: Any) -> QdrantVectorStore:
    return QdrantVectorStore(**kwargs)
