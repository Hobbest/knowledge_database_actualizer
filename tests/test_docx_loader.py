from __future__ import annotations

import pytest
from app.sources.docx import DocxLoader


def test_docx_loader_parses_markdown(monkeypatch: pytest.MonkeyPatch, tmp_path):
    path = tmp_path / "notes.docx"
    path.write_bytes(b"docx-bytes")

    class FakeMessage:
        def __init__(self, message: str) -> None:
            self.message = message

    class FakeResult:
        value = "# Intro\n\nParagraph one about vector search.\n\n## Details\n\nMore text."
        messages = [FakeMessage("info")]

    monkeypatch.setattr("mammoth.convert_to_markdown", lambda handle: FakeResult())

    source = DocxLoader().load_from_path(path)

    assert source.source_type == "docx"
    assert source.title == "notes"
    assert "vector search" in source.text
    assert len(source.segments) >= 2
    assert any("Details" in segment.text for segment in source.segments)


def test_docx_loader_rejects_empty_document(monkeypatch: pytest.MonkeyPatch, tmp_path):
    path = tmp_path / "empty.docx"
    path.write_bytes(b"docx-bytes")

    class FakeResult:
        value = "   "
        messages = []

    monkeypatch.setattr("mammoth.convert_to_markdown", lambda handle: FakeResult())

    with pytest.raises(ValueError, match="No extractable text"):
        DocxLoader().load_from_path(path)
