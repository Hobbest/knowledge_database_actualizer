"""Obsidian block reference injection for checkable note lines."""

from __future__ import annotations

import re
import secrets

from app.config import settings

_BLOCK_ID_PATTERN = re.compile(r"\s+\^[a-zA-Z0-9-]+$")
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+")
_LIST_PATTERN = re.compile(r"^(\s*[-*+]|\s*\d+\.)\s+")
_SKIP_PREFIXES = ("```", "|", "---")


def _make_block_id() -> str:
    return secrets.token_hex(4)


def _should_tag_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _HEADING_PATTERN.match(stripped):
        return False
    if stripped.startswith(_SKIP_PREFIXES):
        return False
    if stripped.startswith(">"):
        return False
    if _BLOCK_ID_PATTERN.search(stripped):
        return False
    return bool(_LIST_PATTERN.match(stripped) or len(stripped) >= 24)


def inject_block_references(content: str) -> str:
    """Append ``^block-id`` suffixes to substantive lines when enabled."""
    if not settings.include_block_ids:
        return content

    if content.lstrip().startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = f"---{parts[1]}---"
            body = parts[2]
            return frontmatter + "\n" + _inject_body_block_refs(body.lstrip("\n"))

    return _inject_body_block_refs(content)


def _inject_body_block_refs(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        if _should_tag_line(line):
            lines.append(f"{line.rstrip()} ^{_make_block_id()}")
        else:
            lines.append(line)
    text = "\n".join(lines)
    if body.endswith("\n"):
        text += "\n"
    return text
