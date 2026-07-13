from __future__ import annotations

from app.chunking import _split_into_units, chunk_text


def test_chunk_text_keeps_short_text_whole():
    text = "One short paragraph about graphs."
    chunks = chunk_text(text, chunk_size=200, chunk_overlap=20)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_chunk_text_splits_on_paragraph_boundaries():
    text = "Alpha paragraph one with enough words.\n\nBeta paragraph two with enough words."
    chunks = chunk_text(text, chunk_size=45, chunk_overlap=0)
    assert len(chunks) == 2
    assert "Alpha" in chunks[0].text
    assert "Beta" in chunks[1].text


def test_chunk_text_does_not_split_list_items():
    text = "- first item about cats\n- second item about dogs\n- third item about birds"
    units = _split_into_units(text)
    assert len(units) == 1
    assert "cats" in units[0] and "birds" in units[0]


def test_chunk_text_preserves_fenced_code_block():
    text = "Intro paragraph.\n\n```python\n# not a tag\nprint('hi')\n```\n\nOutro paragraph."
    units = _split_into_units(text)
    assert any("```" in unit for unit in units)
    assert any("# not a tag" in unit for unit in units)


def test_chunk_text_uses_heading_sections():
    text = "# Section A\n\nAlpha content here.\n\n# Section B\n\nBeta content here."
    chunks = chunk_text(text, chunk_size=80, chunk_overlap=0)
    assert any(chunk.heading == "Section A" for chunk in chunks)
    assert any("Beta content" in chunk.text for chunk in chunks)


def test_chunk_text_oversized_paragraph_splits_on_sentences():
    sentence = "This is sentence one. " * 5
    text = sentence + "Final sentence ends here."
    chunks = chunk_text(text, chunk_size=80, chunk_overlap=0)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.text) <= 120


def test_chunk_text_overlap_carries_trailing_context():
    paragraphs = [f"Paragraph {index} with some words." for index in range(6)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, chunk_size=70, chunk_overlap=30)
    assert len(chunks) >= 2
    assert chunks[0].text != chunks[1].text
