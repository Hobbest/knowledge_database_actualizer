from __future__ import annotations

from app.sources.pdf_quality import assess_pdf_extraction_quality


def test_pdf_quality_warns_on_sparse_pages():
    warnings = assess_pdf_extraction_quality(
        page_count=10,
        pages_with_text=2,
        text="Short note.",
    )
    assert any("only 2 of 10 pages" in message for message in warnings)


def test_pdf_quality_warns_on_garbled_text():
    garbled = " ".join(["xqz" * 3, "qwx", "zzz", "xqk", "zzz"] * 20)
    warnings = assess_pdf_extraction_quality(
        page_count=4,
        pages_with_text=4,
        text=garbled,
    )
    assert any("garbled" in message.lower() for message in warnings)


def test_pdf_quality_accepts_readable_text():
    readable = (
        "Vector databases store embeddings for semantic search. "
        "Approximate nearest neighbor indexes trade recall for speed. "
    ) * 5
    warnings = assess_pdf_extraction_quality(
        page_count=4,
        pages_with_text=4,
        text=readable,
    )
    assert warnings == []
