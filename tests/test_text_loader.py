from __future__ import annotations

from pathlib import Path

from app.sources.text import TextLoader


def test_markdown_loader_strips_frontmatter(tmp_path: Path):
    md = tmp_path / "export.md"
    md.write_text(
        "---\n"
        "tags: [demo, vault]\n"
        "title: Exported Note\n"
        "related: '[[Frontmatter Link]]'\n"
        "---\n"
        "# Heading\n\n"
        "Body with #inline and [[Data Structures]] and ![[Python Basics]].\n",
        encoding="utf-8",
    )

    loaded = TextLoader().load_from_path(md)

    assert loaded.title == "Exported Note"
    assert loaded.tags == ["demo", "vault", "inline"]
    assert loaded.wikilinks == ["Data Structures", "Python Basics", "Frontmatter Link"]
    assert "---" not in loaded.text
    assert loaded.text.startswith("# Heading")
    assert all("---" not in segment.text for segment in loaded.segments)
