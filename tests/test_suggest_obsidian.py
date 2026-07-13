from __future__ import annotations

from app.novelty import OverlappingNote
from app.sources.base import LoadedSource, SourceLocation
from app.suggest import _build_frontmatter, _infer_note_tags, _related_links
from app.wikilinks import format_wikilink, wikilink_target_from_path


def test_format_wikilink_path_qualified():
    assert wikilink_target_from_path("folder/Note.md") == "folder/Note"
    assert format_wikilink("a/Foo.md") == "[[a/Foo]]"


def test_related_links_use_path_not_stem():
    links = _related_links(
        [
            OverlappingNote(
                note_path="topics/Python Basics.md",
                note_title="Python Basics",
                max_similarity=0.9,
                sample_text="sample",
                tags=["python"],
            )
        ]
    )
    assert links == ["[[topics/Python Basics]]"]


def test_infer_note_tags_merges_overlap_and_defaults(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "default_note_tags", "source-import, review")
    tags = _infer_note_tags(
        [
            OverlappingNote(
                note_path="Python Basics.md",
                note_title="Python Basics",
                max_similarity=0.9,
                sample_text="sample",
                tags=["python", "programming"],
            )
        ]
    )
    assert tags == ["source-import", "review", "python", "programming"]


def test_build_frontmatter_includes_tags():
    source = LoadedSource(
        title="Demo",
        text="body",
        source_type="pdf",
        source_ref="demo.pdf",
    )
    frontmatter = _build_frontmatter(
        source,
        "Concept",
        SourceLocation(page=1),
        tags=["source-import", "python"],
    )
    assert "tags:" in frontmatter
    assert "source-import" in frontmatter
    assert "python" in frontmatter
