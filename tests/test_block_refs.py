from __future__ import annotations

import re

from app.block_refs import inject_block_references
from app.config import settings


def test_inject_block_refs_disabled(monkeypatch):
    monkeypatch.setattr(settings, "include_block_ids", False)
    content = "- First point\n\nParagraph with enough text here.\n"
    assert inject_block_references(content) == content


def test_inject_block_refs_on_lists_and_paragraphs(monkeypatch):
    monkeypatch.setattr(settings, "include_block_ids", True)
    content = "- First point\n\nParagraph with enough text here.\n"
    result = inject_block_references(content)
    assert result.count(" ^") == 2
    assert re.search(r"\^[\da-f]{8}", result)


def test_inject_block_refs_preserves_frontmatter(monkeypatch):
    monkeypatch.setattr(settings, "include_block_ids", True)
    content = "---\ntags: [a]\n---\n- Bullet item\n"
    result = inject_block_references(content)
    assert result.startswith("---\ntags: [a]\n---\n")
    assert " ^" in result.split("---", 2)[2]


def test_inject_block_refs_skips_headings(monkeypatch):
    monkeypatch.setattr(settings, "include_block_ids", True)
    content = "## Section\n\n- Item\n"
    result = inject_block_references(content)
    assert "## Section" in result
    assert result.count(" ^") == 1
