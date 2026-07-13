from __future__ import annotations

from pathlib import Path

import pytest
from app import main as main_module
from app.config import settings
from app.note_output import (
    normalize_vault_relative_path,
    render_note_output_path,
    vault_relative_paths_equal,
)
from app.sources.base import LoadedSource
from app.suggest import NoteSuggestion, _apply_analyze_in_place
from app.vault_watcher import _VaultWatchHandler
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_module, "vector_store", vector_store)
    monkeypatch.setattr(main_module.settings, "vault_path", None)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def test_flat_note_output_layout(monkeypatch):
    monkeypatch.setattr(settings, "note_output_folder", "inbox")
    monkeypatch.setattr(settings, "note_output_layout", "flat")
    monkeypatch.setattr(settings, "note_output_pattern", None)
    source = LoadedSource(title="My Book", text="x", source_type="pdf", source_ref="book.pdf")
    assert render_note_output_path(source, "Concept A") == "inbox/Concept A.md"


def test_custom_note_output_pattern(monkeypatch):
    monkeypatch.setattr(settings, "note_output_folder", "sources")
    monkeypatch.setattr(
        settings,
        "note_output_pattern",
        "{year}/{source_type}/{concept_slug}.md",
    )
    source = LoadedSource(title="Talk", text="x", source_type="youtube", source_ref="url")
    path = render_note_output_path(
        source,
        "Key Idea",
        now=__import__("datetime").datetime(2026, 7, 13, tzinfo=__import__("datetime").timezone.utc),
    )
    assert path == "sources/2026/youtube/Key Idea.md"


def test_normalize_vault_relative_path(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "notes" / "alpha.md"
    note.parent.mkdir()
    note.write_text("# Alpha\n", encoding="utf-8")
    monkeypatch.setattr(settings, "vault_path", vault)

    assert normalize_vault_relative_path("notes/alpha.md") == "notes/alpha.md"
    assert normalize_vault_relative_path(note) == "notes/alpha.md"
    assert normalize_vault_relative_path("/outside/note.md") is None


def test_vault_relative_paths_equal():
    assert vault_relative_paths_equal("Notes/Alpha.md", "notes/alpha.md")
    assert not vault_relative_paths_equal("a.md", "b.md")


def test_apply_analyze_in_place_sets_append(monkeypatch):
    monkeypatch.setattr(settings, "analyze_in_place_enabled", True)
    suggestion = NoteSuggestion(
        concept_title="Topic",
        note_path="sources/Book/Topic.md",
        content="# Topic\n",
        location={},
        segment_indices=[0],
        write_mode="write",
        append_target="notes/existing.md",
    )
    _apply_analyze_in_place(suggestion, "notes/existing.md")
    assert suggestion.write_mode == "append"
    assert suggestion.note_path == "notes/existing.md"


def test_apply_analyze_in_place_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "analyze_in_place_enabled", False)
    suggestion = NoteSuggestion(
        concept_title="Topic",
        note_path="sources/Book/Topic.md",
        content="# Topic\n",
        location={},
        segment_indices=[0],
        write_mode="write",
        append_target="notes/existing.md",
    )
    _apply_analyze_in_place(suggestion, "notes/existing.md")
    assert suggestion.write_mode == "write"
    assert suggestion.note_path == "sources/Book/Topic.md"


def test_status_includes_vault_watch(client: TestClient):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "vault_watch" in data
    assert "enabled" in data["vault_watch"]
    assert "note_output" in data
    assert data["note_output"]["folder"] == settings.note_output_folder
    assert "analyze_in_place_enabled" in data


def test_vault_watch_handler_schedules_on_markdown_events():
    scheduled: list[str] = []

    handler = _VaultWatchHandler(lambda: scheduled.append("run"))

    class Event:
        is_directory = False
        src_path = "/vault/notes/test.md"

    handler.on_modified(Event())
    assert scheduled == ["run"]

    scheduled.clear()

    class MoveEvent:
        is_directory = False
        src_path = "/vault/notes/.tmp"
        dest_path = "/vault/notes/test.md"

    handler.on_moved(MoveEvent())
    assert scheduled == ["run"]


def test_set_vault_watch_endpoint(client: TestClient, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main_module.settings, "vault_path", tmp_path)
    monkeypatch.setattr(main_module.vault_watch, "set_enabled", lambda enabled: None)
    monkeypatch.setattr(
        main_module.vault_watch,
        "status",
        lambda: {"enabled": True, "active": False},
    )

    response = client.post("/api/vault/watch", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
