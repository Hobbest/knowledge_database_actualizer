from __future__ import annotations

from ebooklib import epub

from app.sources import SourceDispatcher
from app.sources.epub import EpubLoader


def _write_sample_epub(path) -> None:
    book = epub.EpubBook()
    book.set_identifier("sample-epub")
    book.set_title("Sample EPUB")
    book.set_language("en")

    chapter_one = epub.EpubHtml(title="Chapter One", file_name="chap01.xhtml", lang="en")
    chapter_one.content = (
        b"<html><body><h1>Chapter One</h1>"
        b"<p>Knowledge graphs connect entities and relationships for semantic search.</p>"
        b"</body></html>"
    )
    chapter_two = epub.EpubHtml(title="Chapter Two", file_name="chap02.xhtml", lang="en")
    chapter_two.content = (
        b"<html><body><h2>Chapter Two</h2>"
        b"<p>Vector indexes accelerate nearest-neighbor lookup in high dimensions.</p>"
        b"</body></html>"
    )

    book.add_item(chapter_one)
    book.add_item(chapter_two)
    book.spine = ["nav", chapter_one, chapter_two]
    book.toc = [chapter_one, chapter_two]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book, {})


def test_epub_loader_extracts_chapters(tmp_path):
    path = tmp_path / "book.epub"
    _write_sample_epub(path)

    source = EpubLoader().load_from_path(path)

    assert source.source_type == "epub"
    assert source.title == "Sample EPUB"
    assert "Knowledge graphs connect entities" in source.text
    assert "nearest-neighbor lookup" in source.text
    assert len(source.segments) >= 2
    assert all(segment.location.page is not None for segment in source.segments)


def test_dispatcher_loads_epub_bytes(tmp_path):
    path = tmp_path / "book.epub"
    _write_sample_epub(path)
    content = path.read_bytes()

    source = SourceDispatcher().load_from_bytes("imported.epub", content)

    assert source.source_type == "epub"
    assert source.source_ref == "imported.epub"
    assert source.title == "imported"
    assert source.segments
