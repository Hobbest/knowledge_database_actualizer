"""Soft parser caps for PDF/EPUB/DOCX."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest
from app.sources.docx import DocxLoader
from app.sources.epub import EpubLoader
from app.sources.limits import truncate_segments_to_char_cap
from app.sources.pdf import PdfLoader
from app.sources.base import SourceLocation, SourceSegment
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject


def _pdf_bytes_for_pages(pages: list[str]) -> bytes:
    writer = PdfWriter()
    for page_text in pages:
        page = writer.add_blank_page(width=612, height=792)
        content = f"BT /F1 12 Tf 72 720 Td ({page_text}) Tj ET".encode()
        stream = StreamObject()
        stream._data = content
        stream[NameObject("/Length")] = NumberObject(len(content))

        font = DictionaryObject()
        font[NameObject("/Type")] = NameObject("/Font")
        font[NameObject("/Subtype")] = NameObject("/Type1")
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")

        resources = DictionaryObject()
        resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font})
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = stream

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_truncate_segments_to_char_cap():
    segments = [
        SourceSegment(text="abcd", location=SourceLocation(), index=0),
        SourceSegment(text="efgh", location=SourceLocation(), index=1),
    ]
    kept, warning = truncate_segments_to_char_cap(segments, max_chars=6)
    assert warning is not None
    assert "".join(s.text for s in kept) == "abcdef"


def test_pdf_max_pages_warns(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "max_pdf_pages", 2)
    monkeypatch.setattr(settings, "max_source_chars", 0)
    path = tmp_path / "long.pdf"
    path.write_bytes(_pdf_bytes_for_pages(["Page one", "Page two", "Page three"]))

    source = PdfLoader().load_from_path(path)
    assert len(source.segments) == 2
    assert any("MAX_PDF_PAGES" in w for w in source.load_warnings)


def test_docx_max_source_chars_warns(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "max_source_chars", 20)
    path = tmp_path / "notes.docx"
    path.write_bytes(b"docx-bytes")

    class FakeResult:
        value = "ABCDEFGHIJKLMNOPQRSTUVWXYZ more text after the cap"
        messages = []

    monkeypatch.setattr("mammoth.convert_to_markdown", lambda handle: FakeResult())
    source = DocxLoader().load_from_path(path)
    assert len(source.text) <= 20
    assert any("MAX_SOURCE_CHARS" in w for w in source.load_warnings)


def test_epub_rejects_too_many_zip_members(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "max_epub_zip_members", 3)
    path = tmp_path / "bomb.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("a", "1")
        archive.writestr("b", "2")
        archive.writestr("c", "3")
        archive.writestr("d", "4")

    with pytest.raises(ValueError, match="MAX_EPUB_ZIP_MEMBERS"):
        EpubLoader().load_from_path(path)
