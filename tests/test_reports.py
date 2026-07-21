from app.main import app
from app.reports import generate_html_report, generate_markdown_report
from fastapi.testclient import TestClient


def test_reports_include_analysis_sections_and_escape_html():
    data = {
        "source": {
            "title": "<script>alert(1)</script>",
            "source_type": "web",
            "source_ref": "https://example.test/?a=<unsafe>",
        },
        "novelty": {
            "verdict": "Partially new",
            "novelty_score": 0.75,
            "overlapping_notes": [
                {"note_title": "Existing <Note>", "max_similarity": 0.9}
            ],
        },
        "suggestions": [{"title": "Add & improve", "quality": "high"}],
        "duplicate_suggestions": ["Duplicate <one>"],
    }

    markdown = generate_markdown_report(data)
    report_html = generate_html_report(data)

    assert "Partially new" in markdown
    assert "Existing <Note>" in markdown
    assert "<script>" not in report_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report_html
    assert "Add &amp; improve" in report_html
    assert report_html.startswith("<!doctype html>")


def test_report_export_endpoint_returns_download():
    response = TestClient(app).post(
        "/api/reports/export",
        json={
            "result": {"source": {"title": "Example"}},
            "format": "markdown",
            "title": "Run report",
        },
        headers={"Host": "localhost"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(
        'filename="actualizer-report.md"'
    )
    assert response.text.startswith("# Run report")
