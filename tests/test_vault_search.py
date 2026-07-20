from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_keyword_search_finds_indexed_content(indexed_store):
    results = indexed_store.search_keyword("python readability", top_k=5)
    assert results
    assert all("python" in item.text.casefold() or "python" in item.note_title.casefold() for item in results)


def test_search_endpoint_returns_serialized_matches(
    indexed_store,
    sample_vault,
    tmp_data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from app import main

    monkeypatch.setattr(main, "get_vector_store", lambda _vault=None: indexed_store)
    client = TestClient(main.app, base_url="http://127.0.0.1")
    response = client.get(
        "/api/vault/search",
        params={
            "q": "python readability",
            "mode": "keyword",
            "top_k": 3,
            "vault_path": str(sample_vault),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "keyword"
    assert payload["results"]
    assert {"note_path", "note_title", "snippet", "score"} <= payload["results"][0].keys()
