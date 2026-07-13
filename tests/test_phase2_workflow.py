from __future__ import annotations

from pathlib import Path

import pytest
from app import main as main_module
from app.index_meta import stale_note_count
from app.suggest import apply_suggestion
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_module, "vector_store", vector_store)
    monkeypatch.setattr(main_module.settings, "vault_path", None)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def test_stale_note_count_zero_after_index(indexed_store, sample_vault: Path):
    assert stale_note_count(sample_vault) == 0


def test_stale_note_count_after_edit(indexed_store, sample_vault: Path):
    note = sample_vault / "Python Basics.md"
    original = note.read_text(encoding="utf-8")
    try:
        note.write_text(original + "\nNew paragraph about asyncio.\n", encoding="utf-8")
        assert stale_note_count(sample_vault) >= 1
    finally:
        note.write_text(original, encoding="utf-8")


def test_get_vault_note_api(client: TestClient, sample_vault: Path):
    response = client.get(
        "/api/vault/note",
        params={"note_path": "Python Basics.md", "vault_path": str(sample_vault)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert "Python" in data["content"]


def test_index_if_stale_skips_fresh(client: TestClient, indexed_store, sample_vault: Path):
    response = client.post(
        "/api/vault/index",
        json={"vault_path": str(sample_vault), "if_stale": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("skipped") is True
    assert data.get("stale_note_count") == 0


def test_status_includes_stale_count(client: TestClient, indexed_store):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "stale_note_count" in data
    assert data["stale_note_count"] == 0


def test_append_mode_strips_frontmatter(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "existing.md"
    target.write_text("# Existing\n\nOld content.\n", encoding="utf-8")
    draft = "---\ntags: [x]\n---\n## New section\n\nFresh info.\n"
    result = apply_suggestion(vault, "existing.md", draft, mode="append")
    assert result.status == "appended"
    text = target.read_text(encoding="utf-8")
    assert "Old content." in text
    assert "## Update" in text or "## New section" in text
    assert "tags:" not in text.split("Old content.")[-1]
