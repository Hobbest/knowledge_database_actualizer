"""Tests for robust LLM JSON array extraction."""

from __future__ import annotations

import json

import pytest
from app.json_extract import extract_json_array


def test_extracts_plain_array():
    items = extract_json_array(
        '[{"title": "A", "body": "one"}, {"title": "B", "body": "two"}]'
    )
    assert [item["title"] for item in items] == ["A", "B"]


def test_ignores_wikilink_preamble_that_used_to_break_find():
    # Former bug: find("[") hit [[Note]] → Expecting value at char 2.
    raw = 'See [[Note]] first\n[{"title": "A", "body": "Hello [[World]]"}]'
    items = extract_json_array(raw)
    assert len(items) == 1
    assert items[0]["title"] == "A"
    assert "[[World]]" in items[0]["body"]


def test_ignores_markdown_reference_before_array():
    raw = 'Notes:\n- [1] foo\n[{"title": "A", "body": "b"}]'
    items = extract_json_array(raw)
    assert items[0]["title"] == "A"


def test_strips_markdown_fence():
    raw = '```json\n[{"title": "A", "body": "b"}]\n```'
    assert extract_json_array(raw)[0]["title"] == "A"


def test_raw_decode_ignores_trailing_extra_array():
    # Old find/rfind slice included both arrays → Extra data. raw_decode takes one.
    raw = (
        '[{"title": "A", "body": "a"}]\n'
        '[{"title": "B", "body": "b"}]'
    )
    items = extract_json_array(raw)
    assert len(items) == 1
    assert items[0]["title"] == "A"


def test_extra_data_after_single_array_is_ignored():
    raw = '[{"title": "A", "body": "a"}]\ntrailing commentary'
    items = extract_json_array(raw)
    assert items[0]["title"] == "A"


def test_prefers_array_with_title_body_over_incidental_list():
    raw = '[1, 2, 3]\n[{"title": "A", "body": "b", "summary": "s"}]'
    items = extract_json_array(raw)
    assert len(items) == 1
    assert items[0]["title"] == "A"


def test_normalizes_smart_quotes():
    raw = "[{“title”: “A”, “body”: “b”}]"
    items = extract_json_array(raw)
    assert items[0]["title"] == "A"


def test_wikilinks_inside_body_strings_are_fine():
    raw = json.dumps(
        [
            {
                "title": "Concept",
                "body": "# Concept\n\n## Related notes\n\n- [[Other note]]\n",
            }
        ]
    )
    items = extract_json_array(raw)
    assert "[[Other note]]" in items[0]["body"]


def test_empty_input_and_no_brackets():
    assert extract_json_array("") == []
    assert extract_json_array("no json here") == []


def test_raises_when_brackets_present_but_unusable():
    with pytest.raises(json.JSONDecodeError):
        extract_json_array("just [[wikilinks]] and [broken")


def test_planning_shaped_payload():
    raw = """Here is the plan with [[links]]:
[
  {"title": "T", "segment_indices": [0, 1], "summary": "About T"}
]
"""
    items = extract_json_array(raw)
    assert items[0]["segment_indices"] == [0, 1]


def test_recovers_objects_from_truncated_array():
    raw = (
        '[{"id": "0", "title": "A", "body": "alpha complete"},'
        ' {"id": "1", "title": "B", "body": "beta cut off mid'
    )
    items = extract_json_array(raw)
    assert len(items) == 1
    assert items[0]["id"] == "0"
    assert "alpha complete" in items[0]["body"]


def test_repairs_literal_newlines_inside_strings():
    raw = '[{"id": "0", "title": "A", "body": "line1\n\nline2"}]'
    items = extract_json_array(raw)
    assert items[0]["title"] == "A"
    assert "line1" in items[0]["body"]
    assert "line2" in items[0]["body"]


def test_extracts_notes_wrapper_object():
    raw = '{"notes": [{"id": "0", "title": "A", "body": "alpha"}]}'
    items = extract_json_array(raw)
    assert items[0]["id"] == "0"
