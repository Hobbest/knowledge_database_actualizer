from __future__ import annotations

from app.checkpoint import (
    SuggestionCheckpoint,
    checkpoint_matches_source,
    export_checkpoints,
    import_checkpoints,
    list_incomplete_checkpoints,
    load_checkpoint_by_key,
    load_checkpoint_for_source,
    load_latest_checkpoint,
)
from app.source_identity import normalize_source_key
from app.sources import SourceDispatcher


def test_normalize_youtube_variants():
    a = normalize_source_key("youtube", "https://youtu.be/dQw4w9wgXcQ")
    b = normalize_source_key("youtube", "https://www.youtube.com/watch?v=dQw4w9wgXcQ")
    c = normalize_source_key("youtube", "https://www.youtube.com/shorts/dQw4w9wgXcQ")
    assert a == b == c == "youtube:dQw4w9wgXcQ"


def test_checkpoint_resume_matches_web_url_variants(tmp_data_dir):
    ckpt = SuggestionCheckpoint.for_source("web", "https://example.com/post?utm_source=x")
    ckpt.start(
        {
            "title": "Article",
            "source_type": "web",
            "source_ref": "https://example.com/post?utm_source=x",
            "source_key": normalize_source_key("web", "https://example.com/post?utm_source=x"),
        }
    )
    ckpt.add({"note_path": "a.md", "concept_title": "A", "content": "x", "segment_indices": [0]})
    ckpt.finish(completed=False)

    saved = load_checkpoint_for_source("web", "https://example.com/post/#comments")
    assert saved and len(saved["suggestions"]) == 1
    assert checkpoint_matches_source(
        saved,
        "https://example.com/post/#comments",
        source_type="web",
    )
    assert not checkpoint_matches_source(
        saved,
        "https://other.example.com/post",
        source_type="web",
    )


def test_checkpoint_resume_matches_youtube_variants(tmp_data_dir):
    ckpt = SuggestionCheckpoint.for_source("youtube", "https://youtu.be/dQw4w9wgXcQ")
    ckpt.start(
        {
            "title": "vid",
            "source_type": "youtube",
            "source_ref": "https://youtu.be/dQw4w9wgXcQ",
            "source_key": normalize_source_key("youtube", "https://youtu.be/dQw4w9wgXcQ"),
        }
    )
    ckpt.add({"note_path": "a.md", "concept_title": "A", "content": "x", "segment_indices": [0]})
    ckpt.finish(completed=False)

    saved = load_checkpoint_for_source("youtube", "https://www.youtube.com/watch?v=dQw4w9wgXcQ")
    assert saved and len(saved["suggestions"]) == 1
    assert checkpoint_matches_source(
        saved,
        "https://www.youtube.com/watch?v=dQw4w9wgXcQ",
        source_type="youtube",
    )
    assert not checkpoint_matches_source(
        saved,
        "https://youtu.be/otheridxxxx",
        source_type="youtube",
    )


def test_checkpoint_start_clears_suggestions(tmp_data_dir):
    ckpt = SuggestionCheckpoint.for_source("text", "a.md")
    ckpt.start({"source_ref": "a.md", "source_type": "text", "source_key": "a.md"})
    ckpt.add({"note_path": "n.md", "content": "hi"})
    assert len(ckpt.suggestions) == 1
    ckpt.start({"source_ref": "a.md", "source_type": "text", "source_key": "a.md"})
    assert ckpt.suggestions == []


def test_per_source_checkpoints_do_not_overwrite_each_other(tmp_data_dir):
    ckpt_a = SuggestionCheckpoint.for_source("text", "source-a.md")
    ckpt_a.start({"title": "A", "source_type": "text", "source_ref": "source-a.md", "source_key": "source-a.md"})
    ckpt_a.add({"note_path": "a.md", "content": "from A"})
    ckpt_a.finish(completed=False)

    ckpt_b = SuggestionCheckpoint.for_source("text", "source-b.md")
    ckpt_b.start({"title": "B", "source_type": "text", "source_ref": "source-b.md", "source_key": "source-b.md"})
    ckpt_b.add({"note_path": "b.md", "content": "from B"})
    ckpt_b.finish(completed=False)

    saved_a = load_checkpoint_for_source("text", "source-a.md")
    saved_b = load_checkpoint_for_source("text", "source-b.md")
    assert saved_a and len(saved_a["suggestions"]) == 1
    assert saved_b and len(saved_b["suggestions"]) == 1
    assert saved_a["suggestions"][0]["content"] == "from A"
    assert saved_b["suggestions"][0]["content"] == "from B"

    incomplete = list_incomplete_checkpoints()
    assert len(incomplete) == 2
    assert load_checkpoint_by_key("source-a.md") is not None
    assert load_latest_checkpoint() is not None


def test_load_latest_checkpoint_skips_empty_incomplete(tmp_data_dir):
    """A newer empty run must not hide an older checkpoint that still has notes."""
    older = SuggestionCheckpoint.for_source("text", "older.md")
    older.start(
        {
            "title": "Older",
            "source_type": "text",
            "source_ref": "older.md",
            "source_key": "text:older.md",
        }
    )
    older.add({"note_path": "older.md", "content": "kept notes"})
    older.finish(completed=True)

    newer_empty = SuggestionCheckpoint.for_source("text", "newer.md")
    newer_empty.start(
        {
            "title": "Newer empty",
            "source_type": "text",
            "source_ref": "newer.md",
            "source_key": "text:newer.md",
        }
    )
    newer_empty.finish(completed=False)

    latest = load_latest_checkpoint()
    assert latest is not None
    assert latest["source"]["source_ref"] == "older.md"
    assert len(latest["suggestions"]) == 1
    assert latest["suggestions"][0]["content"] == "kept notes"


def test_checkpoint_bundle_round_trip(tmp_data_dir):
    checkpoint = SuggestionCheckpoint.for_source("text", "portable.md")
    checkpoint.start(
        {
            "title": "Portable",
            "source_type": "text",
            "source_ref": "portable.md",
            "source_key": "text:portable.md",
        }
    )
    checkpoint.add({"note_path": "portable.md", "content": "saved"})
    bundle = export_checkpoints("text:portable.md")
    assert bundle["version"] == 1
    assert len(bundle["checkpoints"]) == 1

    result = import_checkpoints(bundle)
    assert result["imported"] == 1
    restored = load_checkpoint_by_key("text:portable.md")
    assert restored and restored["suggestions"][0]["content"] == "saved"


def test_upload_checkpoint_identity_uses_content_and_filename(tmp_data_dir):
    dispatcher = SourceDispatcher()
    first = dispatcher.load_from_bytes("notes.md", b"# Note\n\nFirst body.\n")
    same = dispatcher.load_from_bytes("notes.md", b"# Note\n\nFirst body.\n")
    changed = dispatcher.load_from_bytes("notes.md", b"# Note\n\nChanged body.\n")

    assert first.source_ref == "notes.md"
    assert first.source_key == same.source_key
    assert first.source_key != changed.source_key
    assert first.source_key and first.source_key.endswith(":notes.md")

    checkpoint = SuggestionCheckpoint.for_source(
        first.source_type,
        first.source_ref,
        source_key=first.source_key,
    )
    checkpoint.start(
        {
            "title": first.title,
            "source_type": first.source_type,
            "source_ref": first.source_ref,
            "source_key": first.source_key,
        }
    )
    checkpoint.add({"note_path": "draft.md", "content": "draft"})
    checkpoint.finish(completed=False)

    assert load_checkpoint_for_source(
        same.source_type,
        same.source_ref,
        source_key=same.source_key,
    )
    assert (
        load_checkpoint_for_source(
            changed.source_type,
            changed.source_ref,
            source_key=changed.source_key,
        )
        is None
    )
