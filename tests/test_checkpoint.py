from __future__ import annotations

from app.checkpoint import (
    SuggestionCheckpoint,
    checkpoint_matches_source,
    list_incomplete_checkpoints,
    load_checkpoint_by_key,
    load_checkpoint_for_source,
    load_latest_checkpoint,
)
from app.source_identity import normalize_source_key


def test_normalize_youtube_variants():
    a = normalize_source_key("youtube", "https://youtu.be/dQw4w9wgXcQ")
    b = normalize_source_key("youtube", "https://www.youtube.com/watch?v=dQw4w9wgXcQ")
    c = normalize_source_key("youtube", "https://www.youtube.com/shorts/dQw4w9wgXcQ")
    assert a == b == c == "youtube:dQw4w9wgXcQ"


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
