"""Analyze in-flight admission → HTTP 429."""

from __future__ import annotations

import pytest
from app.config import settings
from app.runtime import (
    analyze_in_flight_count,
    release_analyze_slot,
    try_acquire_analyze_slot,
)
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_analyze_slots():
    while analyze_in_flight_count() > 0:
        release_analyze_slot()
    yield
    while analyze_in_flight_count() > 0:
        release_analyze_slot()


def test_try_acquire_analyze_slot_respects_limit():
    assert try_acquire_analyze_slot(1)
    assert analyze_in_flight_count() == 1
    assert not try_acquire_analyze_slot(1)
    release_analyze_slot()
    assert try_acquire_analyze_slot(1)
    release_analyze_slot()


def test_try_acquire_unlimited_when_limit_zero():
    assert try_acquire_analyze_slot(0)
    assert analyze_in_flight_count() == 0


def test_analyze_returns_429_when_slot_denied(
    tmp_data_dir, monkeypatch: pytest.MonkeyPatch
):
    import app.api.sources as sources_module
    from app.main import app

    monkeypatch.setattr(settings, "analyze_max_in_flight", 2)
    monkeypatch.setattr(settings, "api_token", None)
    monkeypatch.setattr(sources_module, "try_acquire_analyze_slot", lambda _limit: False)

    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post(
        "/api/sources/analyze",
        data={"url": "https://example.com/article"},
    )
    assert response.status_code == 429
    assert "in flight" in response.json()["detail"].lower()
    assert response.headers.get("retry-after") == "30"
