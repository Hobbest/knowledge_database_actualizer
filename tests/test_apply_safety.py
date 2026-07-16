from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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


def test_atomic_write_preserves_existing_on_replace_failure(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "notes/a.md"
    note.parent.mkdir(parents=True)
    note.write_text("original\n", encoding="utf-8")

    original_replace = os.replace

    def flaky_replace(src, dst):
        if Path(dst) == note:
            raise OSError("simulated disk failure")
        return original_replace(src, dst)

    with patch("app.suggest.os.replace", side_effect=flaky_replace):
        result = apply_suggestion(vault, "notes/a.md", "replacement\n", overwrite=True)

    assert result.status == "error"
    assert note.read_text() == "original\n"
    assert not list(note.parent.glob("*.tmp"))


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


def test_refresh_notes_endpoint(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.main as main_module

    vault = tmp_path / "obsidian"
    vault.mkdir()
    note_path = vault / "fresh.md"
    note_path.write_text("# Fresh\n\nBody.\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_refresh(vault_path, written_paths):
        calls.append(list(written_paths))
        return {"indexed_notes": len(written_paths), "chunk_count": 42}

    monkeypatch.setattr(main_module, "_refresh_written_notes", fake_refresh)

    response = client.post(
        "/api/vault/refresh-notes",
        json={"vault_path": str(vault), "note_paths": ["fresh.md"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["indexed_notes"] == 1
    assert calls == [["fresh.md"]]


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


def test_request_vault_path_does_not_mutate_global_settings(
    client: TestClient,
    tmp_path: Path,
):
    from app.config import settings

    vault = tmp_path / "request-vault"
    vault.mkdir()
    note = vault / "existing.md"
    note.write_text("existing\n", encoding="utf-8")

    response = client.get(
        "/api/vault/note",
        params={"vault_path": str(vault), "note_path": "existing.md"},
    )
    assert response.status_code == 200
    assert settings.vault_path is None


def test_preview_returns_exact_content_apply_will_write(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import app.main as main_module

    vault = tmp_path / "preview-vault"
    vault.mkdir()
    target = vault / "topic.md"
    target.write_text("# Topic\n\n## Details\n\nOld text.\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "_refresh_written_notes", lambda *_args: {})
    payload = {
        "vault_path": str(vault),
        "note_path": "topic.md",
        "content": "---\ntags: [new]\n---\nFresh information.\n",
        "mode": "append",
        "append_heading": "Details",
    }

    preview_response = client.post("/api/suggestions/preview", json=payload)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["exists"] is True
    assert preview["existing_content"].startswith("# Topic")

    apply_response = client.post("/api/suggestions/apply", json=payload)
    assert apply_response.status_code == 200, apply_response.text
    assert target.read_text(encoding="utf-8") == preview["final_content"]


def test_preview_rejects_path_traversal(client: TestClient, tmp_path: Path):
    vault = tmp_path / "preview-vault"
    vault.mkdir()
    response = client.post(
        "/api/suggestions/preview",
        json={
            "vault_path": str(vault),
            "note_path": "../outside.md",
            "content": "nope",
            "mode": "write",
        },
    )
    assert response.status_code == 400


def test_preview_reflects_write_that_apply_would_skip(client: TestClient, tmp_path: Path):
    vault = tmp_path / "preview-vault"
    vault.mkdir()
    target = vault / "existing.md"
    target.write_text("keep me\n", encoding="utf-8")
    response = client.post(
        "/api/suggestions/preview",
        json={
            "vault_path": str(vault),
            "note_path": "existing.md",
            "content": "replacement\n",
            "mode": "write",
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    preview = response.json()
    assert preview["will_write"] is False
    assert preview["final_content"] == preview["existing_content"] == "keep me\n"


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
