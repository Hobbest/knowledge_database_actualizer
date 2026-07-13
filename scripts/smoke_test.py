#!/usr/bin/env python3
"""Smoke test: index vault -> analyze source -> apply multi-note suggestions."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import app.suggest as suggest_module
from app.checkpoint import SuggestionCheckpoint
from app.config import settings
from app.embeddings import get_embedding_service
from app.graph import KnowledgeGraph
from app.media import (
    extract_markdown_tables,
    find_captions,
    media_for_location,
    merge_table_captions,
    render_media_section,
    table_to_markdown,
)
from app.novelty import Verdict, analyze_novelty
from app.relevance import (
    filter_relevant_segments,
    is_boilerplate,
    is_boilerplate_title,
    is_low_value_text,
    looks_like_table_of_contents,
)
from app.segmentation import split_large_segments
from app.sources import SourceDispatcher
from app.sources.base import LoadedSource, MediaItem, SourceLocation, SourceSegment
from app.suggest import apply_suggestions, draft_note_suggestions, iter_note_suggestions
from app.summarize import compose_title, key_points, refine_note_body, summarize_text
from app.text_utils import clean_extractive_text
from app.vectorstore import VectorStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = ROOT / "sample_vault"


def main() -> None:
    settings.data_dir = Path(tempfile.mkdtemp(prefix="actualizer-test-"))
    settings.vault_path = SAMPLE_VAULT

    # Keep the smoke test hermetic and fast: local embeddings, no external LLM.
    # This exercises the structural planning path (segmentation + splitting).
    settings.embedding_provider = "local"
    settings.embedding_model = "all-MiniLM-L6-v2"
    settings.embedding_api_key = None
    settings.llm_provider = None
    settings.llm_api_key = None
    settings.llm_model = None
    # Keep retry backoff instant so the rate-limit tests run fast.
    settings.llm_max_retries = 3
    settings.llm_retry_base_delay = 0.0
    settings.llm_retry_max_delay = 0.0
    settings.llm_disable_after_failures = 2
    get_embedding_service.cache_clear()

    vector_store = VectorStore()
    graph = KnowledgeGraph()
    dispatcher = SourceDispatcher()

    print("1) Indexing sample vault...")
    stats = vector_store.index_vault(SAMPLE_VAULT)
    graph.build_from_vault(SAMPLE_VAULT)
    print(f"   notes={stats['note_count']} chunks={stats['chunk_count']} links={stats['link_count']}")

    known_source_text = """
    Python is a readable programming language. It supports lists, dictionaries,
    functions, and virtual environments. FastAPI is a popular Python web framework.
  """

    novel_source_text = """# Rust Ownership

Rust is a systems programming language focused on memory safety without garbage collection.
Ownership means each value has a single owner at a time.

# Rust Borrowing

Borrowing allows references without transferring ownership.
Mutable and immutable borrows follow strict compile-time rules.

# Cargo

Cargo is Rust's package manager and build tool.
It handles dependencies, builds, tests, and publishing crates.
"""

    print("2) Analyzing mostly-known source...")
    known = analyze_novelty(known_source_text, vector_store)
    print(f"   verdict={known.verdict.value} score={known.novelty_score}")
    assert known.verdict in {Verdict.ALREADY_KNOWN, Verdict.PARTIALLY_NEW}

    print("3) Analyzing novel source...")
    novel = analyze_novelty(novel_source_text, vector_store)
    print(f"   verdict={novel.verdict.value} score={novel.novelty_score}")
    assert novel.verdict in {Verdict.NOVEL, Verdict.PARTIALLY_NEW}
    assert novel.novelty_score >= known.novelty_score

    print("4) Drafting and applying multi-note suggestions...")
    source = LoadedSource(
        title="rust-intro",
        text=novel_source_text.strip(),
        source_type="markdown",
        source_ref="rust-intro.md",
        segments=[
            SourceSegment(
                text=section.strip(),
                location=SourceLocation(line_start=idx * 5 + 1, line_end=idx * 5 + 5),
                index=idx,
            )
            for idx, section in enumerate(novel_source_text.strip().split("\n\n"))
            if section.strip()
        ],
    )
    suggestions = draft_note_suggestions(source, novel, vector_store)
    print(f"   suggestions={len(suggestions)}")
    assert len(suggestions) >= 3

    large_source_text = "\n\n".join(
        f"# Topic {idx}\n\nDiscussion of topic {idx} with unique details about concept {idx}."
        for idx in range(1, 13)
    )
    large_source = LoadedSource(
        title="large-source",
        text=large_source_text,
        source_type="markdown",
        source_ref="large-source.md",
        segments=[
            SourceSegment(
                text=section.strip(),
                location=SourceLocation(line_start=idx * 3 + 1, line_end=idx * 3 + 3),
                index=idx,
            )
            for idx, section in enumerate(large_source_text.split("\n\n"))
            if section.strip()
        ],
    )
    large_suggestions = draft_note_suggestions(large_source, novel, vector_store)
    print(f"   large_source_suggestions={len(large_suggestions)}")
    assert len(large_suggestions) >= 8
    for suggestion in suggestions:
        assert suggestion.location["display"]
        assert "## Source" in suggestion.content
        assert "source_location:" in suggestion.content

    print("5) Drafting from a large single-page PDF (no headings)...")
    big_page_text = " ".join(
        f"Concept {idx} explains a distinct mechanism with concrete example number {idx}."
        for idx in range(1, 61)
    )
    big_page_source = LoadedSource(
        title="handbook",
        text=big_page_text,
        source_type="pdf",
        source_ref="handbook.pdf",
        segments=[
            SourceSegment(
                text=big_page_text,
                location=SourceLocation(page=1, page_end=1),
                index=0,
            )
        ],
    )
    planning_units = split_large_segments([big_page_source.segments[0]], target_chars=1200)
    print(f"   single_page_chars={len(big_page_text)} planning_units={len(planning_units)}")
    assert len(planning_units) >= 3

    big_page_suggestions = draft_note_suggestions(big_page_source, novel, vector_store)
    print(f"   large_pdf_page_suggestions={len(big_page_suggestions)}")
    assert len(big_page_suggestions) >= 3
    for suggestion in big_page_suggestions:
        assert suggestion.location["display"] == "page 1"

    print("6) Per-page provenance across a multi-page PDF...")
    multi_page = LoadedSource(
        title="multi-page",
        text="",
        source_type="pdf",
        source_ref="multi-page.pdf",
        segments=[
            SourceSegment(
                text=f"Distinct topic on page {page}. It covers idea {page} in enough depth to matter.",
                location=SourceLocation(page=page, page_end=page),
                index=page - 1,
            )
            for page in range(1, 6)
        ],
    )
    multi_suggestions = draft_note_suggestions(multi_page, novel, vector_store)
    pages_seen = {s.location.get("page") for s in multi_suggestions}
    print(f"   notes={len(multi_suggestions)} distinct_pages={sorted(p for p in pages_seen if p)}")
    assert pages_seen == {1, 2, 3, 4, 5}

    print("7) Boilerplate filtering...")
    assert is_boilerplate_title("Acknowledgements")
    assert is_boilerplate_title("## Table of Contents")
    assert is_boilerplate_title("Chapter 3: Summary")
    assert is_boilerplate("References\n\n[1] Some Author. A Paper. 2020.")
    assert is_boilerplate("For media inquiries, contact press@example.com")
    assert not is_boilerplate_title("Summary of gradient descent optimization")
    assert not is_boilerplate("Gradient descent iteratively minimizes a loss function.")

    # Table of contents: dot leaders + trailing page numbers, even without a
    # "Contents" heading, must be treated as boilerplate rather than a note.
    toc_page = (
        "Introduction .......................... 1\n"
        "Chapter 1 Getting Started ............. 5\n"
        "1.1 Installation ...................... 6\n"
        "1.2 Configuration ..................... 9\n"
        "Chapter 2 Core Concepts ............... 14\n"
    )
    assert looks_like_table_of_contents(toc_page)
    assert is_boilerplate(toc_page)
    assert is_boilerplate_title("1.2 Configuration ..................... 9")
    assert not looks_like_table_of_contents(
        "Gradient descent iteratively minimizes a loss function over 10 steps."
    )
    # An ellipsis in real prose must not be mistaken for a dot leader.
    assert not is_boilerplate("The idea, roughly... is that gradients flow backward.")

    # Symbol noise (dot leaders / decorative rules) is stripped from note text.
    cleaned = clean_extractive_text("Overview .......... 12\n\nRule ======== here.")
    assert "....." not in cleaned and "========" not in cleaned

    # Link/reference dumps (no "References" heading) are low-value, not notes.
    link_dump = (
        "33ff.[17] http://labs.consol.de/nagios/check_mssql_health[18] "
        "http://www.cse.wustl.edu/jain/net_traffic_monitors2[19] "
        "http://msdn.microsoft.com/en-us/library/ms141744.aspx[20] "
        "http://msdn.microsoft.com/en-us/library/ms140246.aspx OceanofPDF.com"
    )
    assert is_low_value_text(link_dump)
    assert is_boilerplate(link_dump)
    # Real prose that merely contains a link or an inline citation is kept.
    assert not is_low_value_text(
        "The method (described at http://example.com/paper) improves accuracy by "
        "three percent over the baseline across every benchmark that was tested."
    )
    assert not is_low_value_text(
        "According to Smith [1], gradient descent converges under mild assumptions "
        "on the learning rate, and later work extended this to stochastic settings."
    )

    boilerplate_source_segments = [
        SourceSegment(text="Acknowledgements\n\nThanks to everyone involved.",
                      location=SourceLocation(page=1, page_end=1), index=0),
        SourceSegment(text="Backpropagation computes gradients via the chain rule.",
                      location=SourceLocation(page=2, page_end=2), index=1),
        SourceSegment(text="References\n\n[1] Author. Title. Year.",
                      location=SourceLocation(page=3, page_end=3), index=2),
        SourceSegment(text=toc_page,
                      location=SourceLocation(page=4, page_end=4), index=3),
        SourceSegment(text=link_dump,
                      location=SourceLocation(page=5, page_end=5), index=4),
    ]
    kept = filter_relevant_segments(boilerplate_source_segments)
    print(f"   segments in={len(boilerplate_source_segments)} kept={len(kept)}")
    assert len(kept) == 1 and kept[0].location.page == 2

    print("7b) Summarization, title composition, and refinement...")
    # compose_title strips markdown, enumeration, leaders, and normalizes part markers.
    assert compose_title("## 1.2 Gradient Descent") == "Gradient Descent"
    assert compose_title("introduction .......... 5") == "Introduction"
    assert compose_title("Chapter 3: Backpropagation") == "Backpropagation"
    assert compose_title("Neural nets (part 2)") == "Neural nets (part 2)"

    article = (
        "Gradient descent is an optimization algorithm. "
        "It iteratively updates parameters to minimize a loss function. "
        "The learning rate controls the step size of each update. "
        "A learning rate that is too large can cause divergence. "
        "Gradient descent is widely used to train neural networks."
    )
    summary = summarize_text(article, max_sentences=2)
    assert 0 < len(summary) < len(article)
    assert summary.count(".") <= 2
    points = key_points(article, max_points=3, min_chars=20, exclude=summary)
    # Key points must not duplicate the summary sentences.
    assert points and all(point not in summary for point in points)

    # refine_note_body: dedupe bullets, collapse blank runs, keep code fences intact.
    messy = "# Title\n\n\n\n## Points\n- a\n- a\n- \n- b\n\n\n```\n- code stays\n- code stays\n```\n"
    refined = refine_note_body(messy)
    assert "\n\n\n" not in refined
    assert refined.count("- a") == 1
    assert refined.count("- code stays") == 2  # fenced content untouched
    assert refined.endswith("\n")

    print("7c) Tables & figures parsing...")
    # Caption detection distinguishes figures from tables and ignores prose.
    caps = find_captions(
        "Figure 3-1: Latency over time.\nTable 2. Model accuracy summary.\n"
        "This sentence merely mentions the figure inline.",
        SourceLocation(page=5, page_end=5),
    )
    kinds = {(c.kind, c.label) for c in caps}
    assert ("figure", "Figure 3-1") in kinds
    assert ("table", "Table 2") in kinds
    assert len(caps) == 2  # the plain sentence is not a caption

    # A ToC "List of Figures" line collapses to no caption text (dot leaders).
    toc_caps = find_captions("Figure 4 .......................... 88", SourceLocation(page=2))
    assert toc_caps and toc_caps[0].caption == ""

    # pdfplumber-style grid (cells may be None) becomes a clean markdown table.
    grid = [["Model", "Params", None], ["A", "7B", "82.1"], ["B", "13B", "85.4"]]
    table_md = table_to_markdown(grid)
    assert table_md and table_md.count("\n") >= 3 and "| Model | Params |" in table_md

    # Markdown pipe tables are detected with their line range.
    md_lines = (
        "# Data\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\nAfter table."
    ).splitlines()
    md_tables = extract_markdown_tables(md_lines)
    assert len(md_tables) == 1 and md_tables[0].location.line_start == 3

    # Same-page caption merges into the extracted table.
    merged = merge_table_captions([
        MediaItem(kind="table", label="Table 1", caption="Accuracy", location=SourceLocation(page=1)),
        MediaItem(kind="table", label="Table (page 1, #1)", markdown=table_md, location=SourceLocation(page=1)),
    ])
    assert len(merged) == 1 and merged[0].label == "Table 1" and merged[0].markdown

    # Media is attached to a note only when it falls on the note's page/lines.
    media = [
        MediaItem(kind="table", label="Table 1", markdown=table_md, location=SourceLocation(page=3, page_end=3)),
        MediaItem(kind="figure", label="Figure 9", caption="Off-page", location=SourceLocation(page=99)),
    ]
    on_note = media_for_location(SourceLocation(page=2, page_end=4), media)
    assert len(on_note) == 1 and on_note[0].label == "Table 1"
    section = render_media_section(on_note)
    assert "## Tables & figures" in section and "| Model | Params |" in section

    # End-to-end: a markdown source with a table + captions embeds a media section.
    media_md = (
        b"# Findings\n\nWe summarize measured results across configurations here.\n\n"
        b"Figure 1: Throughput trend.\n\n| Cfg | Score |\n| --- | --- |\n| A | 9 |\n| B | 7 |\n"
    )
    media_source = dispatcher.load_from_bytes("findings.md", media_md)
    assert media_source.media, "expected tables/figures to be detected"
    media_notes = draft_note_suggestions(media_source, novel, None)
    joined = "\n".join(note.content for note in media_notes)
    print(f"   media_items={len(media_source.media)} note_has_section={'## Tables & figures' in joined}")
    assert "## Tables & figures" in joined
    assert "| Cfg | Score |" in joined
    # The pipe rows must not leak into the prose summary.
    assert "## Summary" in joined and "| A | 9 |" in joined.split("## Tables & figures", 1)[1]

    print("8) Loading from bytes preserves segment locations...")
    md_bytes = (
        b"# Alpha\n\nFirst concept body line.\n\n# Beta\n\nSecond concept body line.\n"
    )
    byte_source = dispatcher.load_from_bytes("notes.md", md_bytes)
    print(f"   segments={len(byte_source.segments)} ref={byte_source.source_ref}")
    assert len(byte_source.segments) >= 2
    assert byte_source.source_ref == "notes.md"
    assert any(seg.location.line_start for seg in byte_source.segments)

    print("9) Progress events are emitted while drafting...")
    events = list(iter_note_suggestions(big_page_source, novel, vector_store))
    stages = {e["stage"] for e in events if e.get("type") == "progress"}
    final = [e for e in events if e.get("type") == "suggestions"]
    print(f"   progress_stages={sorted(stages)} suggestion_events={len(final)}")
    assert "drafting" in stages
    assert len(final) == 1

    print("10) Rate-limit resilience + incremental checkpoint saving...")

    class _RateLimitedProvider:
        def complete(self, prompt, *, system=None):
            raise RuntimeError("429 Too Many Requests: rate limit exceeded")

    ckpt_path = Path(settings.data_dir) / "checkpoints" / "test.json"
    checkpoint = SuggestionCheckpoint(path=ckpt_path)
    original_provider = suggest_module.get_llm_provider
    suggest_module.get_llm_provider = lambda: _RateLimitedProvider()
    try:
        rl_events = list(
            iter_note_suggestions(big_page_source, novel, vector_store, checkpoint=checkpoint)
        )
    finally:
        suggest_module.get_llm_provider = original_provider

    final_rl = next(e for e in rl_events if e.get("type") == "suggestions")
    warnings = final_rl["warnings"]
    saved = json.loads(ckpt_path.read_text(encoding="utf-8"))
    print(
        f"   notes={len(final_rl['suggestions'])} warnings={len(warnings)} "
        f"checkpoint_notes={len(saved['suggestions'])} completed={saved['completed']}"
    )
    assert len(final_rl["suggestions"]) >= 3
    assert any("rate limit" in w.lower() for w in warnings)
    assert len(saved["suggestions"]) == len(final_rl["suggestions"])
    assert saved["completed"] is True
    assert all(note["content"] for note in saved["suggestions"])
    # Fallback notes should be clean prose, not per-line PDF fragments.
    assert "## Summary" in saved["suggestions"][0]["content"]

    print("11) Transient rate limit is retried, then succeeds via the LLM...")

    class _FlakyProvider:
        def __init__(self, fail_times: int):
            self.calls = 0
            self.fail_times = fail_times

        def complete(self, prompt, *, system=None):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: rate limit exceeded")
            return "# Recovered Concept\n\nLLM_AUTHORED_MARKER: drafted after backoff retries.\n"

    flaky = _FlakyProvider(fail_times=2)
    suggest_module.get_llm_provider = lambda: flaky
    try:
        flaky_events = list(iter_note_suggestions(big_page_source, novel, vector_store))
    finally:
        suggest_module.get_llm_provider = original_provider

    final_flaky = next(e for e in flaky_events if e.get("type") == "suggestions")
    llm_authored = [s for s in final_flaky["suggestions"] if "LLM_AUTHORED_MARKER" in s.content]
    print(
        f"   provider_calls={flaky.calls} llm_authored={len(llm_authored)} "
        f"warnings={len(final_flaky['warnings'])}"
    )
    assert llm_authored, "expected the note to be drafted by the LLM after retries"
    # The informational "LLM usage this run" summary always appears when the
    # LLM was used; only problem warnings (fallbacks, exhausted retries) count.
    problem_warnings = [
        w for w in final_flaky["warnings"] if not w.startswith("LLM usage this run")
    ]
    assert not problem_warnings, f"a transient rate limit should not warn: {problem_warnings}"

    print("11c) Resuming an interrupted run reuses saved notes...")
    # Deterministic (structural) baseline: what a full run should cover.
    suggest_module.get_llm_provider = lambda: None
    try:
        base_events = list(iter_note_suggestions(big_page_source, novel, vector_store))
    finally:
        suggest_module.get_llm_provider = original_provider
    base_notes = [s.to_dict() for s in next(
        e for e in base_events if e.get("type") == "suggestions"
    )["suggestions"]]
    assert len(base_notes) >= 2

    # Pretend the run was interrupted after the first note was saved.
    recovered = base_notes[:1]

    class _MarkerProvider:
        def complete(self, prompt, *, system=None):
            return "# Drafted\n\nRESUME_MARKER: freshly drafted while resuming.\n"

    resume_ckpt = SuggestionCheckpoint(path=ckpt_path)
    suggest_module.get_llm_provider = lambda: _MarkerProvider()
    try:
        resume_events = list(iter_note_suggestions(
            big_page_source, novel, vector_store,
            checkpoint=resume_ckpt, resume_suggestions=recovered,
        ))
    finally:
        suggest_module.get_llm_provider = original_provider

    resume_notes = next(e for e in resume_events if e.get("type") == "suggestions")["suggestions"]
    reuse_events = [
        e for e in resume_events
        if e.get("type") == "progress" and "Reusing recovered" in e.get("message", "")
    ]
    reused = [s for s in resume_notes if s.content == recovered[0]["content"]]
    fresh = [s for s in resume_notes if "RESUME_MARKER" in s.content]
    saved_resume = json.loads(ckpt_path.read_text(encoding="utf-8"))
    print(
        f"   total={len(resume_notes)} reused={len(reused)} fresh={len(fresh)} "
        f"reuse_events={len(reuse_events)} checkpoint={len(saved_resume['suggestions'])}"
    )
    assert len(resume_notes) == len(base_notes)          # same coverage as a full run
    assert len(reused) == 1 and reuse_events              # recovered note kept verbatim
    assert len(fresh) == len(base_notes) - 1             # only the rest were drafted
    assert len(saved_resume["suggestions"]) == len(resume_notes)
    assert saved_resume["completed"] is True

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        results = apply_suggestions(
            vault_path=vault,
            notes=[
                {"note_path": item.note_path, "content": item.content, "mode": "write"}
                for item in suggestions
            ],
        )
        written = [r.written_path for r in results if r.written_path]
        assert all(r.status == "written" for r in results)
        assert len(written) == len(suggestions)
        for rel_path in written:
            assert (vault / rel_path).exists()

        # Existing notes are skipped unless overwrite=True; overwrite keeps a .bak.
        blocked = apply_suggestions(
            vault_path=vault,
            notes=[
                {
                    "note_path": suggestions[0].note_path,
                    "content": "replaced",
                    "mode": "write",
                    "overwrite": False,
                }
            ],
        )
        assert blocked[0].status == "skipped_exists"
        assert (vault / suggestions[0].note_path).read_text(encoding="utf-8") != "replaced"

        forced = apply_suggestions(
            vault_path=vault,
            notes=[
                {
                    "note_path": suggestions[0].note_path,
                    "content": "replaced",
                    "mode": "write",
                    "overwrite": True,
                }
            ],
        )
        assert forced[0].status == "written" and forced[0].overwritten
        assert forced[0].backup_path and (vault / forced[0].backup_path).exists()
        assert (vault / suggestions[0].note_path).read_text(encoding="utf-8") == "replaced"
        print(f"   wrote {len(written)} notes (overwrite guard ok)")

        # Incremental re-index should pick up newly written notes without a full rebuild.
        before = vector_store.chunk_count()
        refresh = vector_store.upsert_notes(vault, written)
        graph_refresh = graph.upsert_notes(vault, written)
        assert refresh["indexed_notes"] == len(written)
        assert vector_store.chunk_count() >= before
        assert graph_refresh["updated_notes"] == len(written)
        assert suggestions[0].note_path in graph.graph.nodes
        print(f"   incremental index: +{refresh['chunk_count_added']} chunks")

    graph_json = graph.to_vis_json()
    assert len(graph_json["nodes"]) >= 3
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
