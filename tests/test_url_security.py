from __future__ import annotations

import socket
import urllib.error
from email.message import Message

import pytest
from app.url_security import UnsafeURLError, open_public_url, validate_public_url


def _dns(address: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.0.2.1", "::1", "fc00::1"],
)
def test_validate_public_url_blocks_non_public_dns(
    address: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _dns(address))
    with pytest.raises(UnsafeURLError, match="non-public"):
        validate_public_url("https://attacker.example/resource")


def test_validate_public_url_rejects_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: _dns("93.184.216.34"))
    with pytest.raises(UnsafeURLError, match="credentials"):
        validate_public_url("https://user:pass@example.com/")


def test_redirect_target_is_revalidated(monkeypatch: pytest.MonkeyPatch):
    import app.url_security as security

    def resolve(host: str, *_args, **_kwargs):
        address = "169.254.169.254" if host == "metadata.example" else "93.184.216.34"
        return _dns(address)

    headers = Message()
    headers["Location"] = "http://metadata.example/latest/meta-data/"

    class RedirectingOpener:
        def open(self, request, timeout):  # noqa: ANN001
            raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(security, "_NO_REDIRECT_OPENER", RedirectingOpener())

    with pytest.raises(UnsafeURLError, match="non-public"):
        open_public_url("https://public.example/start", timeout=1)
