from __future__ import annotations

from app.config import settings
from app.obsidian_uri import note_path_for_obsidian_uri, obsidian_open_uri


def test_obsidian_open_uri_with_vault(monkeypatch):
    monkeypatch.setattr(settings, "obsidian_vault_name", "My Vault")
    uri = obsidian_open_uri("folder/Note.md")
    assert uri.startswith("obsidian://open?")
    assert "vault=My" in uri or "vault=My+Vault" in uri
    assert "file=folder%2FNote" in uri or "file=folder/Note" in uri


def test_obsidian_open_uri_file_only():
    uri = obsidian_open_uri("Python Basics.md", vault_name=None)
    assert "obsidian://open?" in uri
    assert note_path_for_obsidian_uri("Python Basics.md") == "Python Basics"


def test_note_path_strips_md():
    assert note_path_for_obsidian_uri("a/b.md") == "a/b"
