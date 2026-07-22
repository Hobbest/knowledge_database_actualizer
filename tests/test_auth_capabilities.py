"""Tests for capability-tier API token auth."""

from __future__ import annotations

import pytest
from app.auth import (
    parse_api_token_capabilities,
    required_capabilities,
    token_grants_capabilities,
    validate_suggestion_content,
    validate_suggestion_note_path,
)
from app.config import settings
from fastapi.testclient import TestClient


def test_parse_empty_capabilities_grants_all():
    assert parse_api_token_capabilities("") == frozenset(
        {"read", "analyze", "write", "admin", "chat"}
    )


def test_parse_unknown_capability_raises():
    with pytest.raises(ValueError, match="Unknown"):
        parse_api_token_capabilities("read,fly")


def test_route_capability_mapping():
    assert required_capabilities("GET", "/api/status") == frozenset({"read"})
    assert required_capabilities("POST", "/api/sources/analyze") == frozenset({"analyze"})
    assert required_capabilities("POST", "/api/suggestions/apply") == frozenset({"write"})
    assert required_capabilities("POST", "/api/chat") == frozenset({"chat"})
    assert required_capabilities("GET", "/api/debug/recent-logs") == frozenset({"admin"})


def test_token_grants_capabilities():
    assert token_grants_capabilities({"read", "write"}, {"read"})
    assert not token_grants_capabilities({"read"}, {"write"})


def test_validate_checkpoint_note_path():
    assert validate_suggestion_note_path("notes/topic.md") == "notes/topic.md"
    with pytest.raises(ValueError):
        validate_suggestion_note_path("../escape.md")
    with pytest.raises(ValueError):
        validate_suggestion_note_path("notes/topic.txt")


def test_validate_checkpoint_content_size():
    validate_suggestion_content("hello")
    with pytest.raises(ValueError):
        validate_suggestion_content("x" * 500_001)


@pytest.fixture()
def authed_client(tmp_data_dir, monkeypatch: pytest.MonkeyPatch):
    from app.main import app

    monkeypatch.setattr(settings, "api_token", "cap-token")
    monkeypatch.setattr(settings, "api_token_capabilities", "read")
    return TestClient(app, base_url="http://127.0.0.1")


def test_read_only_token_blocks_write(authed_client: TestClient):
    ok = authed_client.get("/api/status", headers={"Authorization": "Bearer cap-token"})
    assert ok.status_code == 200

    denied = authed_client.post(
        "/api/suggestions/preview",
        headers={"Authorization": "Bearer cap-token"},
        json={
            "vault_path": str(settings.vault_path),
            "note_path": "test.md",
            "content": "body",
            "mode": "write",
        },
    )
    assert denied.status_code == 403
    assert "capabilities" in denied.json()["detail"].lower()
