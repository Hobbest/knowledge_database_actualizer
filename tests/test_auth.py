from __future__ import annotations

import pytest
from app.config import settings
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir, monkeypatch: pytest.MonkeyPatch):
    # Import after settings patches so module-level store uses temp data dir path
    # for chroma via settings.chroma_path on new VectorStore — main already constructed
    # vector_store; auth only needs the app.
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1")


def test_status_open_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "api_token", None)
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert "indexed_chunks" in payload
    assert "warnings" in payload


def test_api_requires_bearer_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "api_token", "secret-token")
    denied = client.get("/api/status")
    assert denied.status_code == 401

    ok = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200

    ok2 = client.get("/api/status", headers={"X-API-Token": "secret-token"})
    assert ok2.status_code == 200


def test_static_index_allowed_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "api_token", "secret-token")
    response = client.get("/")
    # Frontend may or may not exist in CI; either 200 or 404 is fine — not 401.
    assert response.status_code != 401
