"""Safe outbound HTTP helpers for user-controlled URLs."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from types import TracebackType
from typing import Protocol, cast

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_READ_CHUNK_BYTES = 64 * 1024


class UnsafeURLError(ValueError):
    """Raised when an outbound URL could reach a non-public network."""


class FetchSizeError(UnsafeURLError):
    """Raised when an outbound response exceeds the configured size limit."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


class PublicURLResponse(Protocol):
    headers: Message

    def read(self, amt: int = -1) -> bytes: ...

    def close(self) -> None: ...

    def __enter__(self) -> PublicURLResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


def _is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def validate_public_url(url: str) -> str:
    """Validate an HTTP(S) URL and every address returned by DNS."""
    candidate = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError(f"Invalid URL: {exc}") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("Only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise UnsafeURLError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("URLs containing credentials are not allowed")

    try:
        addresses = {
            str(item[4][0])
            for item in socket.getaddrinfo(
                parsed.hostname,
                port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve URL hostname: {parsed.hostname}") from exc

    if not addresses:
        raise UnsafeURLError(f"Could not resolve URL hostname: {parsed.hostname}")
    blocked = sorted(address for address in addresses if not _is_public_address(address))
    if blocked:
        raise UnsafeURLError(
            f"URL hostname resolves to a non-public address: {', '.join(blocked)}"
        )
    return candidate


def open_public_url(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> PublicURLResponse:
    """Open a public URL, validating DNS again for every redirect target."""
    current = validate_public_url(url)
    for redirect_count in range(max_redirects + 1):
        request = urllib.request.Request(current, headers=headers or {})
        try:
            return cast(PublicURLResponse, _NO_REDIRECT_OPENER.open(request, timeout=timeout))
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_STATUSES:
                raise
            location = exc.headers.get("Location")
            exc.close()
            if not location:
                raise UnsafeURLError("Redirect response is missing a Location header") from exc
            if redirect_count >= max_redirects:
                raise UnsafeURLError(f"Too many redirects (maximum {max_redirects})") from exc
            current = validate_public_url(urllib.parse.urljoin(current, location))

    raise UnsafeURLError(f"Too many redirects (maximum {max_redirects})")


def read_response_bounded(response: PublicURLResponse, limit_bytes: int) -> bytes:
    """Read a response body, optionally capped at ``limit_bytes`` (0 = unlimited)."""
    if limit_bytes < 0:
        raise ValueError("limit_bytes must be >= 0")

    content_length = response.headers.get("Content-Length")
    if limit_bytes > 0 and content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > limit_bytes:
            raise FetchSizeError(
                f"Remote response Content-Length ({declared} bytes) exceeds "
                f"limit of {limit_bytes} bytes"
            )

    if limit_bytes == 0:
        return response.read()

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit_bytes:
            raise FetchSizeError(
                f"Remote response exceeds size limit of {limit_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def response_charset(headers: Message) -> str:
    return headers.get_content_charset() or "utf-8"
