from __future__ import annotations

from pathlib import Path

import yaml
from app.config import settings
from app.note_output import (
    apply_note_template,
    content_for_append,
    default_note_path,
    moc_note_path,
)
from app.sources.base import LoadedSource, SourceLocation
from app.suggest import NoteSuggestion, _build_frontmatter, _build_moc_suggestion


def test_default_note_path_uses_output_folder(monkeypatch):
    monkeypatch.setattr(settings, "note_output_folder", "imports")
    source = LoadedSource(title="My Book", text="x", source_type="pdf", source_ref="book.pdf")
    assert default_note_path(source, "Concept A") == "imports/My Book/Concept A.md"


def test_moc_note_path():
    source = LoadedSource(title="Talk", text="x", source_type="youtube", source_ref="url")
    assert moc_note_path(source).endswith("sources/Talk/index.md")


def test_content_for_append_strips_frontmatter():
    content = "---\ntags: [a]\n---\n# Title\n\nBody text.\n"
    appended = content_for_append(content)
    assert appended.startswith("# Title")
    assert "---" not in appended


def test_build_frontmatter_includes_type_and_status(monkeypatch):
    monkeypatch.setattr(settings, "note_frontmatter_type", "atomic")
    monkeypatch.setattr(settings, "note_frontmatter_status", "draft")
    source = LoadedSource(title="Demo", text="body", source_type="pdf", source_ref="demo.pdf")
    frontmatter = _build_frontmatter(source, "Concept", SourceLocation(page=1), tags=["x"])
    assert "type: atomic" in frontmatter
    assert "status: draft" in frontmatter
    assert "source: demo.pdf" in frontmatter


def test_apply_note_template(tmp_path: Path, monkeypatch):
    template = tmp_path / "tpl.md"
    template.write_text(
        "---\ntype: from-template\n---\n# {{title}}\n\n{{body}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "note_template_path", template)
    source = LoadedSource(title="Src", text="x", source_type="text", source_ref="a.txt")
    rendered = apply_note_template(
        "---\ntags: [a]\n---\n# Concept\n\nHello.\n",
        vault_path=None,
        title="Concept",
        concept="Concept",
        tags=["a"],
        source=source,
        location=SourceLocation(),
    )
    assert "from-template" in rendered
    assert "# Concept" in rendered
    assert "Hello." in rendered


def test_build_moc_suggestion_links_concepts(monkeypatch):
    monkeypatch.setattr(settings, "generate_moc", True)
    source = LoadedSource(title="Demo Source", text="x", source_type="pdf", source_ref="d.pdf")
    concept = NoteSuggestion(
        concept_title="Alpha",
        note_path="sources/Demo_Source/Alpha.md",
        content="---\n---\n# Alpha\n",
        location={},
        segment_indices=[0],
    )
    moc = _build_moc_suggestion(source, [concept])
    assert moc.is_moc
    assert "[[sources/Demo_Source/Alpha]]" in moc.content
    parsed = yaml.safe_load(moc.content.split("---", 2)[1])
    assert parsed["type"] == "moc"
