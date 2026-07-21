from __future__ import annotations

import json
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from app.sources import SourceDispatcher
from app.sources.pdf import PdfLoader
from fastapi.testclient import TestClient
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


def test_pdf_loader_extracts_page_segments_and_warnings(tmp_path):
    path = tmp_path / "sparse.pdf"
    path.write_bytes(
        _pdf_bytes_for_pages(
            ["Readable page one", "", "", "", ""],
        )
    )

    source = PdfLoader().load_from_path(path)

    assert source.source_type == "pdf"
    assert source.title == "sparse"
    assert len(source.segments) == 1
    assert source.segments[0].location.page == 1
    assert "Readable page one" in source.text
    assert source.load_warnings
    assert any("page" in warning.lower() for warning in source.load_warnings)


def test_dispatcher_loads_pdf_bytes_with_content_hash_identity():
    pdf_bytes = _pdf_bytes_for_pages(["Uploaded PDF body"])

    source = SourceDispatcher().load_from_bytes("notes.pdf", pdf_bytes)

    assert source.source_type == "pdf"
    assert source.source_ref == "notes.pdf"
    assert source.source_key.startswith("upload:sha256:")
    assert source.segments
    assert "Uploaded PDF body" in source.text


def test_pdf_loader_uses_pdfplumber_for_sparse_pypdf_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    path = tmp_path / "fallback.pdf"
    path.write_bytes(b"mock-pdf")
    fallback_text = (
        "pdfplumber recovered a complete paragraph from a page whose original "
        "pypdf extraction contained too little useful text for reliable analysis."
    )

    class FakePypdfPage:
        def extract_text(self):
            return "x"

    class FakePlumberPage:
        def extract_text(self):
            return fallback_text

    class FakePlumberPdf:
        pages = [FakePlumberPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    import app.sources.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "PdfReader", lambda _path: SimpleNamespace(pages=[FakePypdfPage()]))
    monkeypatch.setattr(
        pdf_module,
        "settings",
        SimpleNamespace(include_media=False, pdf_ocr_enabled=False),
    )
    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        SimpleNamespace(open=lambda _path: FakePlumberPdf()),
    )

    source = PdfLoader().load_from_path(path)

    assert source.text == fallback_text
    assert source.segments[0].location.page == 1


def test_pdf_loader_warns_when_ocr_dependencies_are_unavailable(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf_bytes_for_pages([""]))

    import app.sources.pdf as pdf_module

    monkeypatch.setattr(
        pdf_module,
        "settings",
        SimpleNamespace(
            include_media=False,
            pdf_ocr_enabled=True,
            pdf_ocr_language="eng",
            pdf_ocr_dpi=300,
        ),
    )
    monkeypatch.setattr(pdf_module, "convert_from_path", None)
    monkeypatch.setattr(pdf_module, "pytesseract", None)

    with pytest.raises(ValueError, match="No extractable text"):
        PdfLoader().load_from_path(path)
    assert "optional OCR dependencies are unavailable" in caplog.text


def test_pdf_loader_ocrs_only_pages_still_sparse(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"mock-pdf")
    readable_text = (
        "This text page already contains enough readable material for reliable "
        "analysis and therefore must never be rendered by the OCR fallback."
    )
    ocr_text = "OCR recovered the scanned second page with useful readable text."
    render_calls = []

    class FakePage:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self):
            return self.text

    def fake_render(_path, **kwargs):
        render_calls.append(kwargs)
        return [object()]

    import app.sources.pdf as pdf_module

    monkeypatch.setattr(
        pdf_module,
        "PdfReader",
        lambda _path: SimpleNamespace(pages=[FakePage(readable_text), FakePage("")]),
    )
    monkeypatch.setattr(PdfLoader, "_extract_text_with_pdfplumber", lambda *_args: {})
    monkeypatch.setattr(
        pdf_module,
        "settings",
        SimpleNamespace(
            include_media=False,
            pdf_ocr_enabled=True,
            pdf_ocr_language="eng",
            pdf_ocr_dpi=250,
        ),
    )
    monkeypatch.setattr(pdf_module, "convert_from_path", fake_render)
    monkeypatch.setattr(
        pdf_module,
        "pytesseract",
        SimpleNamespace(image_to_string=lambda _image, lang: ocr_text),
    )

    source = PdfLoader().load_from_path(path)

    assert ocr_text in source.text
    assert render_calls == [{"dpi": 250, "first_page": 2, "last_page": 2}]


def test_pdf_loader_rejects_corrupt_pdf(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a pdf")

    with pytest.raises(ValueError, match="Invalid or unreadable PDF"):
        PdfLoader().load_from_path(path)


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "vector_store", vector_store)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def test_analyze_pdf_upload_streams_warnings_and_result(client: TestClient):
    pdf_bytes = _pdf_bytes_for_pages(["Readable page one", "", "", "", ""])

    with client.stream(
        "POST",
        "/api/sources/analyze",
        files={"file": ("sparse.pdf", pdf_bytes)},
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    warnings = [event["message"] for event in events if event["type"] == "warning"]
    result = next(event for event in events if event["type"] == "result")
    assert warnings
    assert result["source"]["source_type"] == "pdf"
    assert result["suggestions"]
