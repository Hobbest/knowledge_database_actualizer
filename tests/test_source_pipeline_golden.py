from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "vector_store", vector_store)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def _analyze_file(client: TestClient, filename: str, body: bytes) -> dict:
    with client.stream(
        "POST",
        "/api/sources/analyze",
        files={"file": (filename, body)},
    ) as response:
        assert response.status_code == 200, response.text
        events = [json.loads(line) for line in response.iter_lines() if line]
    return next(event for event in events if event["type"] == "result")


def _assert_golden_note(note: dict) -> None:
    content = note["content"]
    assert content.startswith("---\n")
    assert "tags:" in content
    assert "## Source" in content
    location = note.get("location") or {}
    assert location.get("display")


def test_markdown_upload_pipeline_golden_shape(client: TestClient):
    body = (
        b"---\ntags: [research]\n---\n"
        b"# Alpha\n\nFirst concept body line about a topic.\n\n"
        b"# Beta\n\nSecond concept body line about another topic.\n"
    )
    result = _analyze_file(client, "notes.md", body)
    assert result["source"]["source_type"] == "markdown"
    assert result["source"]["tags"] == ["research"]
    assert result["suggestions"]
    _assert_golden_note(result["suggestions"][0])


def test_web_url_pipeline_golden_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from tests.test_web_loader import ARTICLE_HTML

    monkeypatch.setattr("app.sources.web.fetch_html", lambda url: ARTICLE_HTML)
    monkeypatch.setattr("app.sources.validate_public_url", lambda url: url)

    with client.stream(
        "POST",
        "/api/sources/analyze",
        data={"url": "https://example.com/post"},
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    result = next(event for event in events if event["type"] == "result")
    assert result["source"]["source_type"] == "web"
    assert result["suggestions"]
    note = result["suggestions"][0]
    assert "## Source" in note["content"]
    assert "https://example.com/post" in note["content"]


def test_pdf_upload_pipeline_golden_shape(client: TestClient):
    from tests.test_pdf_loader import _pdf_bytes_for_pages

    result = _analyze_file(client, "paper.pdf", _pdf_bytes_for_pages(["PDF concept about graphs."]))
    assert result["source"]["source_type"] == "pdf"
    note = result["suggestions"][0]
    assert "## Source" in note["content"]
    assert "page" in note["location"]["display"].lower()


def test_epub_upload_pipeline_golden_shape(client: TestClient):
    import tempfile
    from pathlib import Path

    from tests.test_epub_loader import _write_sample_epub

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.epub"
        _write_sample_epub(path)
        result = _analyze_file(client, "book.epub", path.read_bytes())

    assert result["source"]["source_type"] == "epub"
    note = result["suggestions"][0]
    assert "## Source" in note["content"]
    assert "chapter" in note["location"]["display"].lower()
