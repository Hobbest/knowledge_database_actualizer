"""Analyze-stream lifecycle: client disconnects must not leak worker threads,
and oversized uploads are rejected before any work starts."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from queue import Queue

import pytest
from app.config import settings
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir, vector_store, monkeypatch: pytest.MonkeyPatch):
    import app.main as main_module

    # Swap in the hermetic store (fake embeddings, tmp chroma dir) so streaming
    # tests never touch the real data dir or download models.
    monkeypatch.setattr(main_module, "vector_store", vector_store)
    return TestClient(main_module.app, base_url="http://127.0.0.1")


def _events(iterations: int, log: dict):
    """Endless-ish event generator that records how far it got."""
    try:
        for i in range(iterations):
            log["produced"] = i + 1
            yield {"type": "progress", "current": i}
    finally:
        log["closed"] = True


def test_ndjson_worker_stops_when_consumer_disconnects():
    from app.main import _ndjson_worker

    queue: Queue = Queue(maxsize=2)
    cancelled = threading.Event()
    log: dict = {"produced": 0, "closed": False}

    worker = threading.Thread(
        target=_ndjson_worker,
        args=(queue, lambda: _events(10_000, log), cancelled),
    )
    worker.start()

    # Nobody consumes: the bounded queue fills and the worker blocks in put().
    time.sleep(0.2)
    assert worker.is_alive()
    assert log["produced"] <= queue.maxsize + 1

    # Simulate the client disconnect (the async consumer sets this in finally).
    cancelled.set()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "worker must exit once the consumer is gone"
    assert log["closed"], "the analyze generator must be closed on cancel"
    assert log["produced"] < 10_000


def test_ndjson_worker_completes_and_signals_done():
    from app.main import _STREAM_DONE, _ndjson_worker

    queue: Queue = Queue(maxsize=32)
    cancelled = threading.Event()
    log: dict = {"produced": 0, "closed": False}

    _ndjson_worker(queue, lambda: _events(5, log), cancelled)

    items = []
    while True:
        item = queue.get_nowait()
        if item is _STREAM_DONE:
            break
        items.append(json.loads(item))
    assert len(items) == 5
    assert log["closed"]


def test_upload_over_limit_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"x" * (2 * 1024 * 1024)

    response = client.post("/api/sources/analyze", files={"file": ("big.txt", big)})
    assert response.status_code == 413
    assert "MAX_UPLOAD_MB" in response.json()["detail"]


def test_upload_reader_stops_after_first_over_limit_chunk():
    from app.main import _read_upload_bounded

    class ChunkedUpload:
        def __init__(self):
            self.reads = 0

        async def read(self, _size: int) -> bytes:
            self.reads += 1
            return b"x" * (1024 * 1024) if self.reads <= 3 else b""

    upload = ChunkedUpload()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_upload_bounded(upload, 1))  # type: ignore[arg-type]
    assert exc_info.value.status_code == 413
    assert upload.reads == 2


def test_upload_limit_zero_means_unlimited(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    big = b"# Note\n\nBody text here.\n" * 100

    response = client.post("/api/sources/analyze", files={"file": ("note.md", big)})
    assert response.status_code == 200


def test_consumer_close_sets_cancelled_for_producer():
    """When the response generator is closed (what ASGI does on client
    disconnect), the shared cancelled event must be set so the producer stops."""
    import asyncio

    from app.main import _queue_to_async_stream

    async def scenario() -> bool:
        queue: Queue = Queue(maxsize=32)
        cancelled = threading.Event()
        queue.put("first\n")
        queue.put("second\n")

        stream = _queue_to_async_stream(queue, cancelled)
        assert await anext(stream) == "first\n"
        assert not cancelled.is_set()
        # Client disconnect: the server closes the async generator mid-stream.
        await stream.aclose()
        return cancelled.is_set()

    assert asyncio.run(scenario()), "closing the stream must signal the producer"


def test_analyze_web_url_streams_ndjson_end_to_end(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """A web article URL is fetched, extracted, and streamed like other sources."""
    from tests.test_web_loader import ARTICLE_HTML

    monkeypatch.setattr("app.sources.web.fetch_html", lambda url: ARTICLE_HTML)
    monkeypatch.setattr("app.sources.validate_public_url", lambda url: url)

    with client.stream(
        "POST",
        "/api/sources/analyze",
        data={"url": "https://example.com/post?utm_source=newsletter#intro"},
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    types = {event["type"] for event in events}
    assert "result" in types
    result = next(event for event in events if event["type"] == "result")
    assert result["source"]["source_type"] == "web"
    assert result["source"]["source_ref"] == "https://example.com/post"
    assert result["source"]["title"] == "Understanding Vector Databases"
    assert result["suggestions"], "expected at least one drafted note"


def test_analyze_streams_ndjson_end_to_end(client: TestClient):
    """A small markdown upload streams progress events and a final result."""
    body = (
        b"# Alpha\n\nFirst concept body line about a topic.\n\n"
        b"# Beta\n\nSecond concept body line about another topic.\n"
    )

    with client.stream(
        "POST", "/api/sources/analyze", files={"file": ("notes.md", body)}
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    types = {event["type"] for event in events}
    assert "result" in types
    result = next(event for event in events if event["type"] == "result")
    assert result["source"]["source_ref"] == "notes.md"
    assert result["suggestions"], "expected at least one drafted note"
