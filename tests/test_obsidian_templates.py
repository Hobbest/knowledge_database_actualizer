from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.note_output import apply_note_template, resolve_template_path
from app.obsidian_templates import (
    discover_obsidian_template,
    expand_template_placeholders,
    read_obsidian_template_folder,
)
from app.sources.base import LoadedSource, SourceLocation


def test_read_obsidian_template_folder(sample_vault: Path):
    folder = read_obsidian_template_folder(sample_vault)
    assert folder is not None
    assert folder.name == "Templates"


def test_discover_obsidian_template(sample_vault: Path, monkeypatch):
    monkeypatch.setattr(settings, "note_template_path", None)
    monkeypatch.setattr(settings, "use_obsidian_templates", True)
    path = discover_obsidian_template(sample_vault)
    assert path is not None
    assert path.name == "Atomic Note.md"


def test_resolve_template_prefers_explicit(tmp_path: Path, monkeypatch):
    explicit = tmp_path / "custom.md"
    explicit.write_text("# {{title}}\n\n{{body}}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "note_template_path", explicit)
    assert resolve_template_path(None) == explicit.resolve()


def test_expand_templater_title():
    rendered = expand_template_placeholders(
        "# <% tp.file.title %>\n\n{{body}}",
        {"title": "Concept", "body": "Hello"},
    )
    assert "# Concept" in rendered
    assert "Hello" in rendered


def test_apply_note_template_from_obsidian_folder(sample_vault: Path, monkeypatch):
    monkeypatch.setattr(settings, "note_template_path", None)
    monkeypatch.setattr(settings, "use_obsidian_templates", True)
    source = LoadedSource(title="Src", text="x", source_type="pdf", source_ref="book.pdf")
    content = apply_note_template(
        "---\ntags: [a]\n---\n# Draft\n\nBody here.\n",
        vault_path=sample_vault,
        title="My Concept",
        concept="My Concept",
        tags=["import"],
        source=source,
        location=SourceLocation(page=3),
    )
    assert "My Concept" in content
    assert "Body here." in content
    assert "book.pdf" in content
