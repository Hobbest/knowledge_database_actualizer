from __future__ import annotations

import sys
from pathlib import Path

from app.config import settings
from app.embeddings import embedding_collection_suffix
from app.graph import KnowledgeGraph
from app.qdrant_store import QdrantVectorStore
from app.sources import SourceDispatcher
from app.vault_index import vault_collection_token
from app.vector_protocol import VectorStoreProtocol
from app.vectorstore import ChromaVectorStore

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"


def _create_vector_store(vault_path: Path | None = None) -> VectorStoreProtocol:
    backend = (settings.vector_backend or "chroma").lower()
    if backend == "qdrant":
        token = vault_collection_token(vault_path) if settings.multi_vault_index_enabled else ""
        collection = (
            f"{settings.qdrant_collection}_{embedding_collection_suffix()}{token}"
        )
        return QdrantVectorStore(
            collection_name=collection,
            vector_size=settings.qdrant_vector_size,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    if backend == "chroma":
        return ChromaVectorStore(vault_path=vault_path)

    if not settings.disable_plugin_discovery:
        from app.plugin_api import discover_vector_stores

        plugins = discover_vector_stores(allowlist=settings.plugin_allowlist_set or None)
        factory = plugins.plugins.get(backend)
        if factory is not None:
            created = factory(vault_path=vault_path)
            if not isinstance(created, VectorStoreProtocol):
                raise TypeError(
                    f"Vector store plugin {backend!r} must return VectorStoreProtocol"
                )
            return created

    raise ValueError(
        f"Unsupported VECTOR_BACKEND '{settings.vector_backend}'. "
        "Use 'chroma', 'qdrant', or install an actualizer.vector_stores plugin."
    )


vector_store = _create_vector_store()
_vector_stores: dict[str, VectorStoreProtocol] = {}
graph = KnowledgeGraph()
source_dispatcher = SourceDispatcher()


def get_vector_store(vault_path: Path | None = None) -> VectorStoreProtocol:
    """Return the vector store for a vault (supports optional multi-vault indexing)."""
    if not settings.multi_vault_index_enabled:
        main_mod = sys.modules.get("app.main")
        if main_mod is not None:
            main_store = getattr(main_mod, "vector_store", None)
            if main_store is not None and main_store is not vector_store:
                return main_store
        return vector_store
    resolved = vault_path or settings.vault_path
    if resolved is None:
        return vector_store
    key = str(resolved.resolve())
    store = _vector_stores.get(key)
    if store is None:
        store = _create_vector_store(resolved)
        _vector_stores[key] = store
    return store
