from __future__ import annotations

import json
from io import BytesIO

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
