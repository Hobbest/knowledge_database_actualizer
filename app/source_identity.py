"""Normalize source identifiers so checkpoint resume survives URL variants."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

# Keep in sync with app.sources.youtube patterns (avoid import cycles).
_YOUTUBE_ID_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([\w-]+)", re.I),
    re.compile(r"(?:https?://)?(?:www\.)?youtu\.be/([\w-]+)", re.I),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]+)", re.I),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([\w-]+)", re.I),
]


def extract_youtube_video_id(url: str) -> str | None:
    text = (url or "").strip()
    if not text:
        return None
    for pattern in _YOUTUBE_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    # Query-only fallbacks (e.g. weird hosts that still carry ?v=)
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        video_id = parse_qs(parsed.query).get("v", [None])[0]
        if video_id:
            return video_id
    except Exception:
        pass
    return None


# Tracking parameters that never change page content; dropped for identity.
_TRACKING_PARAM_PATTERN = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|mc_cid|mc_eid|ref)$", re.I)


def canonical_web_url(url: str) -> str:
    """Normalize a web URL: drop fragments and tracking params, trim trailing slash."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _TRACKING_PARAM_PATTERN.match(key)
    ]
    path = parsed.path.rstrip("/") if parsed.path not in ("", "/") else parsed.path
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            urlencode(query_pairs),
            "",  # fragment never changes served content
        )
    )


def normalize_source_key(source_type: str | None, source_ref: str | None) -> str:
    """Stable identity for a source, used for checkpoint resume matching.

    YouTube URLs collapse to ``youtube:<video_id>`` so
    ``youtu.be/ID`` and ``youtube.com/watch?v=ID`` match. Web article URLs
    collapse to ``web:<canonical url>`` (no fragment / tracking params).
    File paths use posix form of the basename when only a filename is known,
    otherwise the posix path string.
    """
    ref = (source_ref or "").strip()
    kind = (source_type or "").strip().lower()

    video_id = extract_youtube_video_id(ref)
    if kind == "youtube" or video_id:
        if video_id:
            return f"youtube:{video_id}"
        return f"youtube:{ref}"

    if kind == "web" or ref.lower().startswith(("http://", "https://")):
        return f"web:{canonical_web_url(ref)}"

    if not ref:
        return ""

    # Uploaded bytes often store just the original filename as source_ref.
    path = Path(ref)
    try:
        return path.as_posix()
    except Exception:
        return ref


def upload_source_key(filename: str, content: bytes) -> str:
    """Content-stable upload identity with a human-useful filename suffix."""
    digest = sha256(content).hexdigest()
    safe_name = Path(filename or "upload").name.replace(":", "_")
    return f"upload:sha256:{digest}:{safe_name}"


def canonical_youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
