"""Same-origin/security posture: no CORS grants, Host allowlist, token compare."""

from __future__ import annotations

import pytest
from app.config import settings
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_data_dir):
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1")


def test_no_cors_headers_for_cross_origin_requests(client: TestClient):
    """No CORS middleware: a foreign Origin must not be granted anything."""
    response = client.get("/api/status", headers={"Origin": "http://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers

    preflight = client.options(
        "/api/suggestions/apply",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_foreign_host_header_is_rejected(client: TestClient):
    response = client.get("/api/status", headers={"Host": "attacker.example.com"})
    assert response.status_code == 400
    assert "ALLOWED_HOSTS" in response.json()["detail"]


def test_localhost_hosts_are_allowed(client: TestClient):
    for host in ("127.0.0.1", "127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]:8000"):
        response = client.get("/api/status", headers={"Host": host})
        assert response.status_code == 200, f"expected Host {host!r} to be allowed"


def test_extra_allowed_host_via_setting(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "allowed_hosts", "localhost,myserver.lan")
    ok = client.get("/api/status", headers={"Host": "myserver.lan:8000"})
    assert ok.status_code == 200
    blocked = client.get("/api/status", headers={"Host": "127.0.0.1"})
    assert blocked.status_code == 400


def test_empty_allowlist_disables_host_check(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "allowed_hosts", "")
    response = client.get("/api/status", headers={"Host": "anything.example"})
    assert response.status_code == 200


def test_bearer_token_grants_access(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "api_token", "secret-token")
    ok = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200
    denied = client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 401


def test_token_comparison_is_exact_and_handles_non_ascii():
    # HTTP clients cannot send raw non-ASCII headers, but the comparison itself
    # must not raise if one ever arrives percent-decoded or misconfigured.
    from app.main import _token_matches

    assert _token_matches("sécret-tökén", "sécret-tökén")
    assert not _token_matches("sécret", "secret")
    assert not _token_matches("secret-toke", "secret-token")


def test_empty_credentials_are_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "api_token", "secret")
    for headers in (
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "secret"},  # missing Bearer prefix
        {"X-API-Token": ""},
    ):
        response = client.get("/api/status", headers=headers)
        assert response.status_code == 401, f"expected 401 for headers {headers!r}"
