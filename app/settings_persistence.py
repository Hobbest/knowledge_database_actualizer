"""Small, guarded updates to user-owned runtime settings."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


def update_env_values(path: Path, updates: dict[str, str]) -> None:
    """Atomically replace only named assignments, preserving comments and secrets."""
    normalized = {key.strip().upper(): str(value) for key, value in updates.items()}
    if not normalized:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found: set[str] = set()
    lines: list[str] = []
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in text.splitlines():
        match = assignment.match(line)
        key = match.group(1).upper() if match else ""
        if key in normalized:
            lines.append(f"{key}={normalized[key]}")
            found.add(key)
        else:
            lines.append(line)
    if lines and lines[-1] != "":
        lines.append("")
    for key, value in normalized.items():
        if key not in found:
            lines.append(f"{key}={value}")
    payload = "\n".join(lines).rstrip() + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
