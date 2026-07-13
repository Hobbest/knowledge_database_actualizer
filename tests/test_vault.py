from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.vault import (
    VaultNote,
    aliases_from_frontmatter,
    embedding_text_for_note,
    extract_embeds,
    extract_wikilinks,
    load_note,
    load_vault,
    merge_note_tags,
    merge_note_wikilinks,
    tags_from_body,
    tags_from_frontmatter,
    transclusion_excerpts_for_note,
    wikilinks_from_frontmatter,
)
from app.wikilinks import build_wikilink_index, format_wikilink, normalize_wikilink_target


def test_load_sample_vault(sample_vault: Path):
    result = load_vault(sample_vault)
    assert result.note_count >= 3
    assert result.link_count >= 1
    python_note = next(n for n in result.notes if n.path.name == "Python Basics.md")
    assert "python" in python_note.tags
    assert "programming" in python_note.tags


def test_tags_from_frontmatter():
    assert tags_from_frontmatter({"tags": ["A", "B"]}) == ["A", "B"]
    assert tags_from_frontmatter({"tag": "solo"}) == ["solo"]
    assert tags_from_frontmatter({"tags": "one, two"}) == ["one", "two"]
    assert tags_from_frontmatter({}) == []


def test_tags_from_body_inline():
    body = "Topic about #python and #data-engineering/spark."
    assert tags_from_body(body) == ["python", "data-engineering/spark"]


def test_tags_from_body_excludes_code_blocks():
    body = "Real #tag\n\n```python\n#not-a-tag\n```\n\nInline `#also-not`."
    assert tags_from_body(body) == ["tag"]


def test_tags_from_body_ignores_headings():
    assert tags_from_body("# Heading\n\nBody #inline") == ["inline"]


def test_merge_note_tags_deduplicates():
    metadata = {"tags": ["python", "demo"]}
    body = "Uses #python and #vault tags."
    assert merge_note_tags(metadata, body) == ["python", "demo", "vault"]


def test_wikilinks_from_frontmatter():
    metadata = {
        "related": ["[[Data Structures]]", "plain text"],
        "summary": "See [[Python Basics|intro]] for context.",
        "tags": ["#not-a-link"],
    }
    assert wikilinks_from_frontmatter(metadata) == ["Data Structures", "Python Basics"]


def test_merge_note_wikilinks_body_and_frontmatter():
    metadata = {"related": "[[MOC Index]]"}
    body = "See [[Data Structures]] and ![[Python Basics]]."
    assert merge_note_wikilinks(metadata, body) == [
        "Data Structures",
        "Python Basics",
        "MOC Index",
    ]


def test_extract_embeds_only():
    text = "Link [[A]] and embed ![[B|label]]."
    assert extract_embeds(text) == ["B"]
    assert extract_wikilinks(text) == ["A", "B"]


def test_extract_wikilinks_includes_embeds():
    text = "See [[Note A]] and ![[Note B|display]]"
    assert extract_wikilinks(text) == ["Note A", "Note B"]


def test_format_wikilink():
    assert format_wikilink("Data Structures.md") == "[[Data Structures]]"


def test_aliases_from_frontmatter():
    assert aliases_from_frontmatter({"aliases": ["A", "B"]}) == ["A", "B"]
    assert aliases_from_frontmatter({"alias": "Solo"}) == ["Solo"]
    assert aliases_from_frontmatter({}) == []


def test_normalize_wikilink_target():
    assert normalize_wikilink_target("folder/Note.md#Heading") == "folder/Note"
    assert normalize_wikilink_target("Note^block") == "Note"
    assert normalize_wikilink_target("./Note") == "Note"


def test_wikilink_path_and_alias_resolution():
    notes = [
        VaultNote(
            path=Path("a/Foo.md"),
            title="Foo",
            content="",
            frontmatter={},
            wikilinks=["Bar"],
            aliases=["Foo Alias"],
        ),
        VaultNote(
            path=Path("b/Foo.md"),
            title="Foo",
            content="",
            frontmatter={},
            wikilinks=["a/Foo"],
            aliases=[],
        ),
        VaultNote(
            path=Path("Bar.md"),
            title="Bar",
            content="",
            frontmatter={},
            wikilinks=["Foo Alias"],
            aliases=[],
        ),
    ]
    index = build_wikilink_index(notes)
    assert index.resolve("Bar") == "Bar.md"
    assert index.resolve("a/Foo") == "a/Foo.md"
    assert index.resolve("Foo Alias") == "a/Foo.md"
    assert index.resolve("Foo") is None  # ambiguous stem
    assert "foo" in index.duplicate_stems


def test_load_note_missing(sample_vault: Path):
    assert load_note(sample_vault, "does-not-exist.md") is None


def test_templates_folder_skipped_during_index(sample_vault: Path):
    result = load_vault(sample_vault)
    paths = {note.path.as_posix() for note in result.notes}
    assert not any("Templates/" in path for path in paths)


def test_embedding_text_rich_includes_title_aliases_tags(monkeypatch):
    monkeypatch.setattr(settings, "rich_note_embeddings", True)
    note = VaultNote(
        path=Path("Note.md"),
        title="My Title",
        content="Body text.",
        frontmatter={"tags": ["alpha"]},
        wikilinks=[],
        aliases=["Alias One"],
        tags=["alpha"],
    )
    text = embedding_text_for_note(note)
    assert "My Title" in text
    assert "Alias One" in text
    assert "#alpha" in text
    assert "Body text." in text


def test_embedding_text_plain_body_when_rich_disabled(monkeypatch):
    monkeypatch.setattr(settings, "rich_note_embeddings", False)
    note = VaultNote(
        path=Path("Note.md"),
        title="My Title",
        content="Body text.",
        frontmatter={},
        wikilinks=[],
        aliases=["Alias One"],
        tags=["alpha"],
    )
    text = embedding_text_for_note(note)
    assert "My Title" not in text
    assert "Alias One" not in text
    assert "#alpha" in text
    assert "Body text." in text


def test_load_note_merges_inline_tags_and_frontmatter_links(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note_path = vault / "Topic.md"
    note_path.write_text(
        "---\n"
        "tags: [meta]\n"
        "related: '[[Related Note]]'\n"
        "---\n"
        "Body with #inline tag and [[Body Link]].\n",
        encoding="utf-8",
    )

    note = load_note(vault, "Topic.md")
    assert note is not None
    assert note.tags == ["meta", "inline"]
    assert note.wikilinks == ["Body Link", "Related Note"]


def test_transclusion_excerpts_in_embedding_text(monkeypatch):
    monkeypatch.setattr(settings, "transclude_depth", 1)
    monkeypatch.setattr(settings, "transclude_excerpt_chars", 200)

    target = VaultNote(
        path=Path("Target.md"),
        title="Target",
        content="Embedded knowledge about graph databases.",
        frontmatter={},
    )
    host = VaultNote(
        path=Path("Host.md"),
        title="Host",
        content="Overview ![[Target]].",
        frontmatter={},
    )
    notes_by_path = {
        "Host.md": host,
        "Target.md": target,
    }
    link_index = build_wikilink_index([host, target])

    excerpts = transclusion_excerpts_for_note(
        host,
        notes_by_path=notes_by_path,
        link_index=link_index,
    )
    assert len(excerpts) == 1
    assert "graph databases" in excerpts[0]

    embedded = embedding_text_for_note(
        host,
        notes_by_path=notes_by_path,
        link_index=link_index,
    )
    assert "graph databases" in embedded
    assert "[Transcluded: Target]" in embedded


def test_transclusion_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "transclude_depth", 0)
    target = VaultNote(
        path=Path("Target.md"),
        title="Target",
        content="Should not appear.",
        frontmatter={},
    )
    host = VaultNote(
        path=Path("Host.md"),
        title="Host",
        content="Overview ![[Target]].",
        frontmatter={},
    )
    notes_by_path = {"Host.md": host, "Target.md": target}
    link_index = build_wikilink_index([host, target])

    assert transclusion_excerpts_for_note(
        host,
        notes_by_path=notes_by_path,
        link_index=link_index,
    ) == []
    assert "Should not appear." not in embedding_text_for_note(
        host,
        notes_by_path=notes_by_path,
        link_index=link_index,
    )
