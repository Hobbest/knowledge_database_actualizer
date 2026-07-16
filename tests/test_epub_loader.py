from __future__ import annotations

from app.sources import SourceDispatcher
from app.sources.epub import EpubLoader
from ebooklib import epub


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
    assert all(segment.location.chapter is not None for segment in source.segments)
    assert all(segment.location.page is None for segment in source.segments)


def test_dispatcher_loads_epub_bytes(tmp_path):
    path = tmp_path / "book.epub"
    _write_sample_epub(path)
    content = path.read_bytes()

    source = SourceDispatcher().load_from_bytes("imported.epub", content)

    assert source.source_type == "epub"
    assert source.source_ref == "imported.epub"
    assert source.title == "imported"
    assert source.segments


def _write_epub_with_broken_image_manifest(path) -> None:
    import zipfile

    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/xhtml/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Broken Image EPUB</dc:title>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter01.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="OEBPS/html/graphics/71hfd.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>"""
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Chapter</h1>
    <p>Text survives even when image manifest hrefs are malformed.</p>
    <img src="../html/graphics/71hfd.jpg" alt="figure"/>
  </body>
</html>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/xhtml/content.opf", opf)
        archive.writestr("OEBPS/xhtml/chapter01.xhtml", chapter)
        archive.writestr("OEBPS/html/graphics/71hfd.jpg", b"fakejpeg")


def test_epub_loader_tolerates_broken_image_manifest_paths(tmp_path):
    path = tmp_path / "broken-images.epub"
    _write_epub_with_broken_image_manifest(path)

    source = EpubLoader().load_from_path(path)

    assert source.title == "Broken Image EPUB"
    assert "Text survives even when image manifest hrefs are malformed." in source.text
    assert source.segments
