from __future__ import annotations

from pathlib import Path

import pytest
from app.suggest import apply_suggestion, apply_suggestions
from fastapi.testclient import TestClient


def test_apply_write_and_skip_existing(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    first = apply_suggestion(vault, "notes/a.md", "one\n", overwrite=False)
    assert first.status == "written"
    assert (vault / "notes/a.md").read_text() == "one\n"

    blocked = apply_suggestion(vault, "notes/a.md", "two\n", overwrite=False)
    assert blocked.status == "skipped_exists"
    assert (vault / "notes/a.md").read_text() == "one\n"

    forced = apply_suggestion(vault, "notes/a.md", "two\n", overwrite=True)
    assert forced.status == "written" and forced.overwritten
    assert forced.backup_path and (vault / forced.backup_path).exists()
    assert (vault / "notes/a.md").read_text() == "two\n"


def test_apply_path_traversal_blocked(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    result = apply_suggestion(vault, "../outside.md", "x", overwrite=True)
    assert result.status == "error"


def test_apply_batch_continues_after_error(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    results = apply_suggestions(
        vault,
        [
            {"note_path": "ok.md", "content": "good", "mode": "write"},
            {"note_path": "../bad.md", "content": "nope", "mode": "write"},
            {"note_path": "ok2.md", "content": "also", "mode": "write"},
        ],
    )
    assert results[0].status == "written"
    assert results[1].status == "error"
    assert results[2].status == "written"


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "vector_store", vector_store)
    monkeypatch.setattr(main_module.settings, "vault_path", None)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def test_apply_batch_accepts_vault_path_from_request(client: TestClient, tmp_path: Path):
    vault = tmp_path / "obsidian"
    vault.mkdir()

    response = client.post(
        "/api/suggestions/apply-batch",
        json={
            "vault_path": str(vault),
            "notes": [
                {
                    "note_path": "sources/demo/hello.md",
                    "content": "# Hello\n\nBody.\n",
                    "mode": "write",
                    "overwrite": False,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 1
    assert (vault / "sources/demo/hello.md").is_file()


def test_apply_batch_keeps_writes_when_index_refresh_fails(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import app.main as main_module

    vault = tmp_path / "obsidian"
    vault.mkdir()

    def boom(*_args, **_kwargs):
        raise RuntimeError("embedding offline")

    monkeypatch.setattr(main_module.vector_store, "upsert_notes", boom)

    response = client.post(
        "/api/suggestions/apply-batch",
        json={
            "vault_path": str(vault),
            "notes": [
                {
                    "note_path": "kept.md",
                    "content": "survives refresh failure\n",
                    "mode": "write",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 1
    assert (vault / "kept.md").read_text() == "survives refresh failure\n"
    assert data["index_refresh"].get("warning")
