from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.chunking import chunk_text
from app.config import settings
from app.embeddings import EmbeddingService, embedding_collection_suffix, get_embedding_service
from app.index_meta import load_index_meta, save_index_meta
from app.preflight import import_chromadb
from app.runtime import INDEX_LOCK
from app.similarity import adjusted_similarity
from app.vault import VaultNote, embedding_text_for_note, load_note, load_vault
from app.vault_fingerprints import index_config_changed, note_fingerprint
from app.vault_index import resolve_vault_meta, vault_collection_token
from app.wikilinks import WikilinkIndex, build_wikilink_index

chromadb = import_chromadb()
ChromaSettings = chromadb.config.Settings

COLLECTION_NAME = "vault_chunks"


def _parse_tags_metadata(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def _merge_note_fingerprints(vault_path: Path, relative_paths: list[str]) -> dict[str, dict]:
    meta = load_index_meta() or {}
    fingerprints = dict(meta.get("note_fingerprints") or {})
    vault_path = vault_path.resolve()
    for rel in relative_paths:
        note = load_note(vault_path, rel)
        if note is None:
            fingerprints.pop(rel, None)
        else:
            fingerprints[rel] = note_fingerprint(vault_path, note)
    return fingerprints


def _collection_name(vault_path: Path | None = None) -> str:
    return f"{COLLECTION_NAME}_{embedding_collection_suffix()}{vault_collection_token(vault_path)}"


@dataclass
class IndexedChunk:
    chunk_id: str
    note_path: str
    note_title: str
    text: str
    heading: str | None
    wikilinks: list[str]


@dataclass
class SimilarChunk:
    chunk_id: str
    note_path: str
    note_title: str
    text: str
    similarity: float
    heading: str | None = None
    tags: list[str] = field(default_factory=list)
    # Raw cosine before any tag-overlap boost. Ranking uses ``similarity`` (tag
    # aware); the novel/known decision uses this so tags cannot mask novelty.
    base_similarity: float | None = None

    def __post_init__(self) -> None:
        if self.base_similarity is None:
            self.base_similarity = self.similarity

    @property
    def content_similarity(self) -> float:
        """Raw cosine similarity (never tag-boosted)."""
        return self.similarity if self.base_similarity is None else self.base_similarity


@dataclass
class SampledChunk:
    chunk_id: str
    note_path: str
    text: str


class VectorStore:
    def __init__(
        self,
        persist_dir: Path | None = None,
        embedding_service: EmbeddingService | None = None,
        vault_path: Path | None = None,
    ):
        self.persist_dir = persist_dir or settings.chroma_path
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._vault_path = vault_path.resolve() if vault_path else None
        self._embedding_service = embedding_service
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._bind_collection(self._vault_path)

    def _bind_collection(self, vault_path: Path | None) -> None:
        self._collection = self._client.get_or_create_collection(
            name=_collection_name(vault_path),
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def reset(self) -> None:
        with INDEX_LOCK:
            self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        name = _collection_name(self._vault_path)
        try:
            self._client.delete_collection(name)
        except Exception:
            pass
        self._bind_collection(self._vault_path)

    def index_vault(self, vault_path: Path) -> dict[str, Any]:
        """Embed and index the vault, skipping unchanged notes when fingerprints match."""
        vault_path = vault_path.resolve()
        vault_result = load_vault(vault_path)
        meta = load_index_meta()
        vault_meta = resolve_vault_meta(meta, vault_path) if meta else None
        fingerprints: dict[str, dict] = dict((vault_meta or {}).get("note_fingerprints") or {})

        same_vault = (
            vault_meta is not None
            and vault_meta.get("vault_path") == str(vault_path)
            and not index_config_changed(vault_meta)
            and bool(fingerprints)
        )
        full_rebuild = not same_vault

        if full_rebuild:
            with INDEX_LOCK:
                self._reset_unlocked()
            fingerprints = {}

        current_notes = {note.path.as_posix(): note for note in vault_result.notes}
        notes_by_path = current_notes
        link_index = (
            build_wikilink_index(vault_result.notes)
            if settings.transclude_depth > 0
            else None
        )
        stored_paths = set(fingerprints.keys())
        current_paths = set(current_notes.keys())
        removed_paths = stored_paths - current_paths

        to_index: list[VaultNote] = []
        skipped_paths: list[str] = []
        for rel, note in current_notes.items():
            fp = note_fingerprint(vault_path, note)
            if not full_rebuild and fingerprints.get(rel) == fp:
                skipped_paths.append(rel)
            else:
                to_index.append(note)

        batch_size = max(1, settings.chroma_index_batch_size)
        indexed_note_count = 0
        chunk_delta = 0

        with INDEX_LOCK:
            for rel in removed_paths:
                try:
                    self._collection.delete(where={"note_path": rel})
                except Exception:
                    self._delete_chunks_by_note_path(rel)
                fingerprints.pop(rel, None)

            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []
            embeddings: list[list[float]] = []

            def flush() -> None:
                nonlocal chunk_delta
                if not ids:
                    return
                self._collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
                chunk_delta += len(ids)
                ids.clear()
                documents.clear()
                metadatas.clear()
                embeddings.clear()

            for note in to_index:
                rel = note.path.as_posix()
                try:
                    self._collection.delete(where={"note_path": rel})
                except Exception:
                    self._delete_chunks_by_note_path(rel)
                before = len(ids)
                self._append_note_chunks(
                    note,
                    ids,
                    documents,
                    metadatas,
                    embeddings,
                    notes_by_path=notes_by_path,
                    link_index=link_index,
                )
                if len(ids) > before:
                    indexed_note_count += 1
                if len(ids) >= batch_size:
                    flush()
            flush()
            total_chunks = self._collection.count()

        new_fingerprints = {
            rel: note_fingerprint(vault_path, note) for rel, note in current_notes.items()
        }

        stats = {
            "vault_path": str(vault_path),
            "note_count": vault_result.note_count,
            "link_count": vault_result.link_count,
            "chunk_count": total_chunks,
            "duplicate_stems": vault_result.duplicate_stems,
            "index_mode": "full" if full_rebuild else "incremental",
            "indexed_notes": indexed_note_count,
            "skipped_notes": len(skipped_paths),
            "removed_notes": len(removed_paths),
            "chunks_added": chunk_delta,
        }
        save_index_meta(
            vault_path=vault_path,
            chunk_count=total_chunks,
            note_count=vault_result.note_count,
            note_fingerprints=new_fingerprints,
            index_mode=str(stats["index_mode"]),
        )
        return stats

    def upsert_notes(self, vault_path: Path, relative_paths: list[str]) -> dict[str, Any]:
        """Re-embed specific notes without rebuilding the whole collection."""
        vault_path = vault_path.resolve()
        unique_paths = list(dict.fromkeys(p for p in relative_paths if p))
        missing: list[str] = []
        prepared: list[tuple[str, list[str], list[str], list[dict[str, Any]], list[list[float]]]] = []

        notes_by_path: dict[str, VaultNote] | None = None
        link_index: WikilinkIndex | None = None
        if settings.transclude_depth > 0:
            vault_result = load_vault(vault_path)
            notes_by_path = {note.path.as_posix(): note for note in vault_result.notes}
            link_index = build_wikilink_index(vault_result.notes)

        for rel in unique_paths:
            note = load_note(vault_path, rel)
            if note is None:
                missing.append(rel)
                prepared.append((rel, [], [], [], []))
                continue
            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []
            embeddings: list[list[float]] = []
            self._append_note_chunks(
                note,
                ids,
                documents,
                metadatas,
                embeddings,
                notes_by_path=notes_by_path,
                link_index=link_index,
            )
            prepared.append((rel, ids, documents, metadatas, embeddings))

        indexed = 0
        chunk_count = 0
        with INDEX_LOCK:
            for rel, ids, documents, metadatas, embeddings in prepared:
                try:
                    self._collection.delete(where={"note_path": rel})
                except Exception:
                    self._delete_chunks_by_note_path(rel)
                if not ids:
                    continue
                self._collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
                chunk_count += len(ids)
                indexed += 1
            total_chunks = self._collection.count()

        save_index_meta(
            vault_path=vault_path,
            chunk_count=total_chunks,
            note_count=None,
            note_fingerprints=_merge_note_fingerprints(vault_path, unique_paths),
        )
        return {
            "indexed_notes": indexed,
            "missing_notes": missing,
            "chunk_count_added": chunk_count,
            "chunk_count": total_chunks,
        }

    def _delete_chunks_by_note_path(self, note_path: str) -> None:
        """Fallback delete when where-filters are unavailable. Caller holds INDEX_LOCK."""
        try:
            existing = self._collection.get(include=[])
        except Exception:
            return
        ids = [
            chunk_id
            for chunk_id in (existing.get("ids") or [])
            if chunk_id.startswith(f"{note_path}::")
        ]
        if ids:
            self._collection.delete(ids=ids)

    def _append_note_chunks(
        self,
        note: VaultNote,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        embeddings: list[list[float]],
        *,
        notes_by_path: dict[str, VaultNote] | None = None,
        link_index: WikilinkIndex | None = None,
    ) -> None:
        note_chunks = chunk_text(
            embedding_text_for_note(
                note,
                notes_by_path=notes_by_path,
                link_index=link_index,
            ),
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            source_label=note.title,
        )
        if not note_chunks:
            return

        texts = [chunk.text for chunk in note_chunks]
        vectors = self.embedding_service.embed_texts(texts)

        for chunk, vector in zip(note_chunks, vectors, strict=True):
            chunk_id = f"{note.path.as_posix()}::{chunk.index}"
            ids.append(chunk_id)
            documents.append(chunk.text)
            metadatas.append(
                {
                    "note_path": note.path.as_posix(),
                    "note_title": note.title,
                    "heading": chunk.heading or "",
                    "wikilinks": ",".join(note.wikilinks),
                    "tags": ",".join(note.tags),
                }
            )
            embeddings.append(vector)

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
        """Batch-embed queries and run one Chroma multi-query."""
        if not texts:
            return []

        vectors = self.embedding_service.embed_texts(texts, task_type="RETRIEVAL_QUERY")
        with INDEX_LOCK:
            # Chroma requires n_results <= collection size.
            n_results = min(top_k, max(1, self._collection.count()))
            if self._collection.count() == 0:
                return [[] for _ in texts]
            result = self._collection.query(
                query_embeddings=vectors,
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

        batch: list[list[SimilarChunk]] = []
        ids_by_query = result.get("ids") or []
        docs_by_query = result.get("documents") or []
        metas_by_query = result.get("metadatas") or []
        dists_by_query = result.get("distances") or []

        for query_idx in range(len(texts)):
            similar: list[SimilarChunk] = []
            if query_idx >= len(ids_by_query) or not ids_by_query[query_idx]:
                batch.append(similar)
                continue
            for chunk_id, doc, meta, distance in zip(
                ids_by_query[query_idx],
                docs_by_query[query_idx],
                metas_by_query[query_idx],
                dists_by_query[query_idx],
                strict=True,
            ):
                match_tags = _parse_tags_metadata(meta.get("tags"))
                base_similarity = 1.0 - float(distance)
                similarity = adjusted_similarity(base_similarity, query_tags, match_tags)
                similar.append(
                    SimilarChunk(
                        chunk_id=chunk_id,
                        note_path=meta.get("note_path", ""),
                        note_title=meta.get("note_title", ""),
                        text=doc or "",
                        similarity=similarity,
                        heading=meta.get("heading") or None,
                        tags=match_tags,
                        base_similarity=base_similarity,
                    )
                )
            similar.sort(key=lambda item: item.similarity, reverse=True)
            batch.append(similar)
        return batch

    def search_keyword(self, query: str, *, top_k: int = 10) -> list[SimilarChunk]:
        """Search indexed chunk text and metadata without embedding the query."""
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []
        with INDEX_LOCK:
            result = self._collection.get(include=["documents", "metadatas"])

        matches: list[SimilarChunk] = []
        for chunk_id, doc, meta in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
            strict=False,
        ):
            text = str(doc or "")
            metadata = meta or {}
            title = str(metadata.get("note_title", ""))
            heading = str(metadata.get("heading", ""))
            haystack = f"{title}\n{heading}\n{text}".casefold()
            counts = [haystack.count(term) for term in terms]
            if not all(counts):
                continue
            # Rank exact/title matches above repeated body matches.
            title_bonus = sum(2 for term in terms if term in title.casefold())
            heading_bonus = sum(1 for term in terms if term in heading.casefold())
            score = float(sum(counts) + title_bonus + heading_bonus)
            matches.append(
                SimilarChunk(
                    chunk_id=str(chunk_id),
                    note_path=str(metadata.get("note_path", "")),
                    note_title=title,
                    text=text,
                    similarity=score,
                    heading=heading or None,
                    tags=_parse_tags_metadata(metadata.get("tags")),
                )
            )
        matches.sort(key=lambda item: (-item.similarity, item.note_path, item.chunk_id))
        return matches[: max(1, top_k)]

    def sample_chunks(self, *, limit: int = 200) -> list[SampledChunk]:
        """Return a random subset of indexed chunks for threshold calibration."""
        with INDEX_LOCK:
            count = self._collection.count()
            if count == 0:
                return []
            take = min(max(1, limit), count)
            result = self._collection.get(
                limit=take,
                include=["documents", "metadatas"],
            )
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        samples: list[SampledChunk] = []
        for chunk_id, doc, meta in zip(ids, docs, metas, strict=False):
            if not doc or not meta:
                continue
            samples.append(
                SampledChunk(
                    chunk_id=str(chunk_id),
                    note_path=str(meta.get("note_path", "")),
                    text=str(doc),
                )
            )
        return samples

    def chunk_count(self) -> int:
        with INDEX_LOCK:
            return self._collection.count()
