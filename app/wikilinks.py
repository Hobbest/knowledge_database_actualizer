"""Obsidian-style wikilink parsing and resolution.

Supports:
- ``[[Note]]`` / ``[[Note|display]]`` by unique basename (stem)
- ``[[folder/Note]]`` / ``[[folder/Note.md]]`` by vault-relative path
- Frontmatter ``aliases`` / ``alias``
- Heading/block refs (``[[Note#Heading]]``, ``[[Note^block]]``) — target only

Ambiguous bare stems (duplicate filenames in different folders) do not resolve
unless the link is path-qualified.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.vault import VaultNote

logger = logging.getLogger(__name__)

# Strip Obsidian heading / block anchors from the link target.
_ANCHOR_PATTERN = re.compile(r"[#^].*$")


def wikilink_target_from_path(note_path: str) -> str:
    """Vault-relative path as an Obsidian wikilink target (no ``.md`` suffix)."""
    target = (note_path or "").replace("\\", "/").strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    while target.startswith("./"):
        target = target[2:]
    return target


def format_wikilink(note_path: str) -> str:
    """Format a vault-relative note path as a wikilink."""
    target = wikilink_target_from_path(note_path)
    return f"[[{target}]]" if target else ""


def normalize_wikilink_target(raw: str) -> str:
    """Normalize a wikilink target for lookup (no display text, no anchors)."""
    target = (raw or "").strip()
    if not target:
        return ""
    target = _ANCHOR_PATTERN.sub("", target).strip()
    target = target.replace("\\", "/")
    if target.lower().endswith(".md"):
        target = target[:-3]
    # Obsidian treats leading ./ as optional
    while target.startswith("./"):
        target = target[2:]
    return target.strip()


@dataclass
class WikilinkIndex:
    """Maps Obsidian link targets to vault-relative note paths."""

    # Exact keys (path without .md, path with .md, stem, aliases) → path
    exact: dict[str, str] = field(default_factory=dict)
    # casefolded name → list of paths (for unambiguous stem/alias resolution)
    by_name: dict[str, list[str]] = field(default_factory=dict)
    # stem → all paths that share it (for reporting duplicates)
    duplicate_stems: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, link: str) -> str | None:
        cleaned = normalize_wikilink_target(link)
        if not cleaned:
            return None

        # Path-qualified links (folder/Note) resolve via exact path keys only.
        if "/" in cleaned:
            if cleaned in self.exact:
                return self.exact[cleaned]
            folded = cleaned.casefold()
            for key, path in self.exact.items():
                if "/" in key and key.casefold() == folded:
                    return path
            return None

        # Bare note name or alias: only when unique across the vault.
        candidates = self.by_name.get(cleaned.casefold(), [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logger.debug(
                "Ambiguous wikilink [[%s]] matches %s — use a path-qualified link",
                link,
                candidates,
            )
        return None


def build_wikilink_index(notes: list[VaultNote]) -> WikilinkIndex:
    index = WikilinkIndex()
    stem_map: dict[str, list[str]] = {}

    for note in notes:
        path = note.path.as_posix()
        path_no_ext = path[:-3] if path.lower().endswith(".md") else path
        stem = note.path.stem

        def register_exact(key: str) -> None:
            if not key:
                return
            index.exact.setdefault(key, path)

        def register_name(name: str) -> None:
            if not name:
                return
            bucket = index.by_name.setdefault(name.casefold(), [])
            if path not in bucket:
                bucket.append(path)

        register_exact(path)
        register_exact(path_no_ext)
        register_name(stem)

        for alias in note.aliases:
            register_name(alias)

        stem_map.setdefault(stem.casefold(), []).append(path)

    index.duplicate_stems = {
        stem: paths for stem, paths in stem_map.items() if len(paths) > 1
    }
    if index.duplicate_stems:
        examples = list(index.duplicate_stems.items())[:5]
        logger.warning(
            "Duplicate note basenames in vault (path-qualify wikilinks): %s",
            {stem: paths for stem, paths in examples},
        )
    return index
