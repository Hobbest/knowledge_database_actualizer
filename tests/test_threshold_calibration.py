from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.threshold_calibration import calibrate_thresholds
from app.vectorstore import VectorStore


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_module, "vector_store", vector_store)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def test_calibrate_empty_index(vector_store: VectorStore):
    result = calibrate_thresholds(vector_store)
    assert result.sample_size == 0
    assert result.fallback is True
    assert "empty" in result.message.lower()


def test_calibrate_from_indexed_vault(indexed_store: VectorStore):
    result = calibrate_thresholds(indexed_store)
    assert result.sample_size > 0
    assert 0.0 < result.recommended_novel_threshold < result.recommended_known_threshold <= 1.0
    assert result.cross_note_samples > 0


def test_calibrate_api_endpoint(client, indexed_store):
    response = client.get("/api/vault/thresholds/calibrate")
    assert response.status_code == 200
    data = response.json()
    assert "recommended_novel_threshold" in data
    assert "recommended_known_threshold" in data
    assert data["sample_size"] > 0
