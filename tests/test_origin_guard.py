"""Origin / Sec-Fetch-Site guard when API_TOKEN is unset."""

from __future__ import annotations

import pytest
from app.config import settings
from fastapi.testclient import TestClient


@pytest.fixture()
def open_client(tmp_data_dir, monkeypatch: pytest.MonkeyPatch):
    from app.main import app

    monkeypatch.setattr(settings, "api_token", None)
    return TestClient(app, base_url="http://127.0.0.1")


def test_cross_site_mutating_request_blocked(open_client: TestClient):
    denied = open_client.post(
        "/api/vault/watch",
        json={"enabled": False},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert denied.status_code == 403
    assert "cross-site" in denied.json()["detail"].lower()


def test_mismatched_origin_blocked(open_client: TestClient):
    denied = open_client.post(
        "/api/vault/watch",
        json={"enabled": False},
        headers={"Origin": "https://evil.example"},
    )
    assert denied.status_code == 403
    assert "origin" in denied.json()["detail"].lower()


def test_same_origin_mutating_allowed(open_client: TestClient):
    ok = open_client.post(
        "/api/vault/watch",
        json={"enabled": False},
        headers={
            "Origin": "http://127.0.0.1",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    # May 400 if no vault indexed yet; must not be origin-blocked.
    assert ok.status_code != 403


def test_curl_without_origin_headers_allowed(open_client: TestClient):
    ok = open_client.post("/api/vault/watch", json={"enabled": False})
    assert ok.status_code != 403


def test_guard_skipped_when_api_token_set(tmp_data_dir, monkeypatch: pytest.MonkeyPatch):
    from app.main import app

    monkeypatch.setattr(settings, "api_token", "secret")
    monkeypatch.setattr(settings, "api_token_capabilities", "")
    client = TestClient(app, base_url="http://127.0.0.1")
    # Cross-site still needs a valid token; origin guard itself is off.
    denied = client.post(
        "/api/vault/watch",
        json={"enabled": False},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert denied.status_code == 401
