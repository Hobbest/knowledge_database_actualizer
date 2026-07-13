"""Discover note templates from an Obsidian vault (.obsidian/app.json)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Common Templater snippets mapped to static values at draft time.
_TEMPLATER_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<%\s*tp\.file\.title\s*%>", re.I), "{{title}}"),
    (re.compile(r"<%\s*tp\.file\.folder\s*%>", re.I), "{{folder}}"),
    (re.compile(r"<%\s*tp\.file\.folder\s*\(\s*true\s*\)\s*%>", re.I), "{{folder}}"),
    (re.compile(r"<%\s*tp\.date\.now\([\"']YYYY-MM-DD[\"']\)\s*%>", re.I), "{{date}}"),
    (re.compile(r"<%\s*tp\.date\.now\([\"']YYYY-MM-DD HH:mm[\"']\)\s*%>", re.I), "{{datetime}}"),
    (re.compile(r"<%\s*tp\.file\.creation_date\([\"']YYYY-MM-DD[\"']\)\s*%>", re.I), "{{date}}"),
    (re.compile(r"<%\s*tp\.file\.last_modified_date\([\"']YYYY-MM-DD[\"']\)\s*%>", re.I), "{{date}}"),
)


def read_obsidian_template_folder(vault_path: Path) -> Path | None:
    """Resolve Obsidian's configured template folder inside a vault."""
    vault_path = vault_path.resolve()
    folder_name = "Templates"
    app_json = vault_path / ".obsidian" / "app.json"
    if app_json.is_file():
        try:
            data = json.loads(app_json.read_text(encoding="utf-8"))
            configured = data.get("templateFolder") or data.get("templatesFolder")
            if isinstance(configured, str) and configured.strip():
                folder_name = configured.strip().strip("/")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read Obsidian app.json: %s", exc)

    candidates = [
        vault_path / folder_name,
        vault_path / ".obsidian" / "templates",
        vault_path / "Templates",
    ]
    for path in candidates:
        if path.is_dir():
            return path.resolve()
    return None


def discover_obsidian_template(vault_path: Path | None) -> Path | None:
    """Pick a template file from the vault when none is configured explicitly."""
    if vault_path is None or not settings.use_obsidian_templates:
        return None

    templates_dir = read_obsidian_template_folder(vault_path)
    if templates_dir is None:
        return None

    if settings.obsidian_template_name:
        named = templates_dir / settings.obsidian_template_name
        if named.is_file():
            return named.resolve()
        named_md = templates_dir / f"{settings.obsidian_template_name}.md"
        if named_md.is_file():
            return named_md.resolve()

    preferred_names = (
        "Atomic Note.md",
        "Default.md",
        "New Note.md",
        "note.md",
        "Template.md",
    )
    for name in preferred_names:
        candidate = templates_dir / name
        if candidate.is_file():
            return candidate.resolve()

    for candidate in sorted(templates_dir.glob("*.md")):
        return candidate.resolve()

    return None


def normalize_templater_syntax(template: str) -> str:
    """Convert a subset of Templater tags to mustache placeholders we can fill."""
    text = template
    for pattern, replacement in _TEMPLATER_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def expand_template_placeholders(template: str, replacements: dict[str, str]) -> str:
    """Fill ``{{key}}`` placeholders and common Templater aliases."""
    now = datetime.now(timezone.utc)
    enriched = {
        **replacements,
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
    }
    rendered = normalize_templater_syntax(template)
    for key, value in enriched.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered
