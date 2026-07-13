from __future__ import annotations

from app.relevance import (
    filter_relevant_segments,
    is_boilerplate,
    is_boilerplate_title,
    is_low_value_text,
    looks_like_table_of_contents,
)
from app.sources.base import SourceLocation, SourceSegment
from app.summarize import compose_title, refine_note_body


def test_boilerplate_titles_and_toc():
    assert is_boilerplate_title("Acknowledgements")
    assert is_boilerplate_title("## Table of Contents")
    assert is_boilerplate("References\n\n[1] Some Author. A Paper. 2020.")
    toc = (
        "Introduction .......................... 1\n"
        "Chapter 1 Getting Started ............. 5\n"
        "Chapter 2 Core Ideas .................. 12\n"
        "Chapter 3 Advanced Topics ............. 40\n"
        "Appendix A ............................ 90\n"
    )
    assert looks_like_table_of_contents(toc)
    assert is_boilerplate(toc)
    assert not is_boilerplate("Gradient descent iteratively minimizes a loss function.")


def test_filter_relevant_segments():
    segments = [
        SourceSegment(
            text="Acknowledgements\n\nThanks.",
            location=SourceLocation(page=1),
            index=0,
        ),
        SourceSegment(
            text="Backpropagation computes gradients via the chain rule.",
            location=SourceLocation(page=2),
            index=1,
        ),
        SourceSegment(
            text="References\n\n[1] Author. Title.",
            location=SourceLocation(page=3),
            index=2,
        ),
    ]
    kept = filter_relevant_segments(segments)
    assert len(kept) == 1 and kept[0].location.page == 2


def test_link_dump_is_low_value():
    dump = (
        "[17] http://example.com/a[18] http://example.com/b OceanofPDF.com"
    )
    assert is_low_value_text(dump)


def test_compose_title_and_refine():
    assert compose_title("## 1.2 Gradient Descent") == "Gradient Descent"
    messy = "# Title\n\n\n\n## Points\n- a\n- a\n- \n- b\n\n\n"
    refined = refine_note_body(messy)
    assert "\n\n\n" not in refined
    assert refined.count("- a") == 1
