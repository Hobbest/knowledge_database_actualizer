from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.note_output import merge_append_into_note, parse_append_target
from app.obsidian_templates import normalize_templater_syntax
from app.suggest import apply_suggestion
from app.vault_index import vault_collection_token


def test_parse_append_target_splits_heading():
    path, heading = parse_append_target("notes/topic.md#Related concepts")
    assert path == "notes/topic.md"
    assert heading == "Related concepts"


def test_merge_append_into_matching_heading():
    existing = "---\ntags: [a]\n---\n# Topic\n\n## Related concepts\n\nOld bullet.\n"
    draft = "---\n---\n## Update\n\nNew bullet.\n"
    merged = merge_append_into_note(
        existing,
        draft,
        target_heading="Related concepts",
    )
    assert "## Related concepts" in merged
    assert "Old bullet." in merged
    assert "New bullet." in merged


def test_apply_suggestion_append_under_heading(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "existing.md"
    target.write_text(
        "# Existing\n\n## Notes\n\nFirst point.\n",
        encoding="utf-8",
    )
    draft = "---\ntags: [x]\n---\n## Update\n\nSecond point.\n"
    result = apply_suggestion(
        vault,
        "existing.md",
        draft,
        mode="append",
        append_heading="Notes",
    )
    assert result.status == "appended"
    text = target.read_text(encoding="utf-8")
    assert "First point." in text
    assert "Second point." in text


def test_normalize_templater_folder_tag():
    template = "Folder: <% tp.file.folder %>"
    normalized = normalize_templater_syntax(template)
    assert "{{folder}}" in normalized


def test_vault_collection_token_disabled_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "multi_vault_index_enabled", False)
    assert vault_collection_token(tmp_path / "vault") == ""


def test_vault_collection_token_when_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "multi_vault_index_enabled", True)
    token = vault_collection_token(tmp_path / "vault")
    assert token.startswith("_")
    assert len(token) > 1
