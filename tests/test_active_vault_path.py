from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.index_meta import active_vault_path, save_index_meta


def test_active_vault_path_prefers_settings(tmp_path: Path, monkeypatch):
    vault = tmp_path / "configured"
    vault.mkdir()
    monkeypatch.setattr(settings, "vault_path", vault)
    assert active_vault_path() == vault.resolve()


def test_active_vault_path_falls_back_to_index_meta(tmp_path: Path, monkeypatch):
    vault = tmp_path / "indexed"
    vault.mkdir()
    monkeypatch.setattr(settings, "vault_path", None)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    save_index_meta(vault_path=vault, chunk_count=12, note_count=3)
    assert active_vault_path() == vault.resolve()
