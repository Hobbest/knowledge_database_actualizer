from __future__ import annotations

from app.sources.pdf_quality import assess_pdf_extraction_quality, page_text_is_unreliable


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


def test_page_quality_marks_sparse_and_garbled_text_unreliable():
    assert page_text_is_unreliable("A short fragment.")
    assert page_text_is_unreliable(" ".join(["xqz", "qwx", "zzz"] * 20))


def test_page_quality_accepts_readable_page():
    text = (
        "Vector databases store embeddings for semantic search and efficient retrieval. "
        "Nearest-neighbor indexes make comparison practical across large collections. "
    )
    assert not page_text_is_unreliable(text)
