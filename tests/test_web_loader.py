from __future__ import annotations

import pytest
from app.source_identity import canonical_web_url, normalize_source_key
from app.sources import SourceDispatcher
from app.sources.web import WebArticleLoader

ARTICLE_HTML = """<!DOCTYPE html><html><head>
<title>Understanding Vector Databases | Example Blog</title>
<meta name="author" content="Jane Doe"></head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<article>
<h1>Understanding Vector Databases</h1>
<p>Vector databases store embeddings so that similar items can be found quickly.
They are the backbone of modern semantic search systems and are widely used in
retrieval augmented generation.</p>
<h2>How indexes work</h2>
<p>Approximate nearest neighbor indexes trade a small amount of recall for a
large speedup. Popular algorithms include HNSW and IVF, each with different
memory and latency profiles.</p>
<p>Choosing the right index depends on the dataset size and the required query
latency for the application.</p>
</article>
<footer>Copyright 2026. <a href="/privacy">Privacy</a></footer>
</body></html>"""


def test_supports_only_non_youtube_http_urls():
    loader = WebArticleLoader()
    assert loader.supports_url("https://example.com/article")
    assert loader.supports_url("http://blog.example.org/post/123")
    assert not loader.supports_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not loader.supports_url("https://youtu.be/dQw4w9WgXcQ")
    assert not loader.supports_url("ftp://example.com/file")
    assert not loader.supports_url("notes/my-note.md")


def test_dispatcher_routes_generic_urls_to_web_loader(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.sources.web.fetch_html", lambda url: ARTICLE_HTML)
    monkeypatch.setattr("app.sources.validate_public_url", lambda url: url)
    dispatcher = SourceDispatcher()
    source = dispatcher.load_from_url("https://example.com/post?utm_source=x#section")

    assert source.source_type == "web"
    assert source.title == "Understanding Vector Databases"
    # Canonical ref: tracking params and fragment dropped.
    assert source.source_ref == "https://example.com/post"


def test_web_loader_extracts_article_and_segments(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.sources.web.fetch_html", lambda url: ARTICLE_HTML)
    source = WebArticleLoader().load_from_url("https://example.com/post")

    assert "Vector databases store embeddings" in source.text
    # Boilerplate is stripped by the extractor.
    assert "Copyright" not in source.text
    assert "Privacy" not in source.text

    # Heading-delimited segments with line locations.
    assert len(source.segments) >= 2
    assert any("How indexes work" in segment.text for segment in source.segments)
    assert all(segment.location.line_start is not None for segment in source.segments)


def test_web_loader_rejects_empty_extraction(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.sources.web.fetch_html",
        lambda url: "<html><body><script>app()</script></body></html>",
    )
    with pytest.raises(ValueError, match="No readable article content"):
        WebArticleLoader().load_from_url("https://example.com/spa")


def test_web_loader_wraps_fetch_errors(monkeypatch: pytest.MonkeyPatch):
    def boom(url: str) -> str:
        raise OSError("connection refused")

    monkeypatch.setattr("app.sources.web.fetch_html", boom)
    with pytest.raises(ValueError, match="Could not fetch"):
        WebArticleLoader().load_from_url("https://example.com/down")


def test_canonical_web_url_strips_tracking_and_fragment():
    assert (
        canonical_web_url("HTTPS://Example.COM/Post/?utm_source=tw&fbclid=abc&page=2#intro")
        == "https://example.com/Post?page=2"
    )
    # Meaningful query params survive.
    assert canonical_web_url("https://example.com/search?q=hnsw") == (
        "https://example.com/search?q=hnsw"
    )


def test_web_source_section_in_drafted_notes(monkeypatch: pytest.MonkeyPatch):
    from app.suggest import _source_section

    monkeypatch.setattr("app.sources.web.fetch_html", lambda url: ARTICLE_HTML)
    source = WebArticleLoader().load_from_url("https://example.com/post")
    location = source.segments[0].location

    section = _source_section(source, location)
    assert "Article: https://example.com/post" in section
    assert "extracted article text" in section


def test_normalize_source_key_for_web_urls():
    key_a = normalize_source_key("web", "https://example.com/post?utm_source=x")
    key_b = normalize_source_key("web", "https://example.com/post/#comments")
    assert key_a == key_b == "web:https://example.com/post"
    # YouTube URLs still collapse to the video id, never web:.
    assert normalize_source_key(None, "https://youtu.be/dQw4w9WgXcQ").startswith("youtube:")
