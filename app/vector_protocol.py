from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.embeddings import EmbeddingService
    from app.vectorstore import SampledChunk, SimilarChunk


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Structural interface consumed by analysis and note-writing services."""

    @property
    def embedding_service(self) -> EmbeddingService: ...

    def reset(self) -> None: ...

    def index_vault(self, vault_path: Path) -> dict[str, Any]: ...

    def upsert_notes(
        self, vault_path: Path, relative_paths: list[str]
    ) -> dict[str, Any]: ...

    def query_similar(
        self,
        text: str,
        *,
        top_k: int = 5,
        query_tags: list[str] | None = None,
    ) -> list[SimilarChunk]: ...

    def query_similar_many(
        self,
        texts: list[str],
        *,
        top_k: int = 5,
        query_tags: list[str] | None = None,
    ) -> list[list[SimilarChunk]]: ...

    def search_keyword(
        self, query: str, *, top_k: int = 10
    ) -> list[SimilarChunk]: ...

    def sample_chunks(self, *, limit: int = 200) -> list[SampledChunk]: ...

    def chunk_count(self) -> int: ...


# Short alias for annotations in integrations.
VectorStoreLike = VectorStoreProtocol
