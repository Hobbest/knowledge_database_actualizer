from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from app.config import settings

if TYPE_CHECKING:
    from app.wikilinks import WikilinkIndex

logger = logging.getLogger(__name__)

# Matches both wikilinks ([[Note]]) and embeds (![[Note]]).
WIKILINK_PATTERN = re.compile(r"!?\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
EMBED_PATTERN = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Obsidian inline tag: #word or #word/subtag (not markdown headings).
INLINE_TAG_PATTERN = re.compile(r"(?<![\w/])#([a-zA-Z][\w/-]*)")
_FENCED_CODE_PATTERN = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")

# Frontmatter keys that are not scanned for wikilinks (tag/alias lists, styling).
_FRONTMATTER_WIKILINK_SKIP_KEYS = frozenset(
    {"tags", "tag", "aliases", "alias", "cssclasses", "cssclass", "cssClasses"}
)

# A leading YAML frontmatter block: --- ... --- at the very top of the file.
FRONTMATTER_BLOCK_PATTERN = re.compile(r"^\ufeff?---\s*\n.*?\n---\s*\n?", re.DOTALL)

# Skip Obsidian / system directories when walking the vault.
_SKIP_DIR_NAMES = {
    ".obsidian",
    ".trash",
    ".git",
    ".smart-env",
    "node_modules",
}


@dataclass
class VaultNote:
    path: Path
    title: str
    content: str
    frontmatter: dict
    wikilinks: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class VaultIndexResult:
    notes: list[VaultNote]
    note_count: int
    link_count: int
    duplicate_stems: dict[str, list[str]] = field(default_factory=dict)


def extract_wikilinks(text: str) -> list[str]:
    links = WIKILINK_PATTERN.findall(text)
    return list(dict.fromkeys(link.strip() for link in links if link.strip()))


def note_title_from_path(path: Path) -> str:
    return path.stem


def aliases_from_frontmatter(metadata: dict) -> list[str]:
    return _strings_from_frontmatter(metadata, "aliases", "alias")


def tags_from_frontmatter(metadata: dict) -> list[str]:
    """Parse Obsidian-style ``tags`` / ``tag`` frontmatter (string or list)."""
    raw_tags = _strings_from_frontmatter(metadata, "tags", "tag")
    normalized: list[str] = []
    for tag in raw_tags:
        for piece in tag.split(","):
            cleaned = piece.strip().lstrip("#")
            if cleaned:
                normalized.append(cleaned)
    return list(dict.fromkeys(normalized))


def _strip_code_for_tag_scan(text: str) -> str:
    """Remove fenced and inline code so ``#tags`` inside code are ignored."""
    stripped = _FENCED_CODE_PATTERN.sub(" ", text)
    return _INLINE_CODE_PATTERN.sub(" ", stripped)


def tags_from_body(text: str) -> list[str]:
    """Extract Obsidian inline ``#tags`` from note body (excludes code blocks)."""
    scan_text = _strip_code_for_tag_scan(text)
    tags = [match.group(1) for match in INLINE_TAG_PATTERN.finditer(scan_text)]
    return list(dict.fromkeys(tag for tag in tags if tag))


def merge_note_tags(metadata: dict, body: str) -> list[str]:
    """Frontmatter tags plus inline body tags, deduplicated in order."""
    combined = tags_from_frontmatter(metadata) + tags_from_body(body)
    return list(dict.fromkeys(combined))


def _metadata_value_as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value)


def wikilinks_from_frontmatter(metadata: dict) -> list[str]:
    """Extract ``[[wikilinks]]`` from frontmatter string/list fields."""
    links: list[str] = []
    for key, value in metadata.items():
        if key.casefold() in _FRONTMATTER_WIKILINK_SKIP_KEYS:
            continue
        links.extend(extract_wikilinks(_metadata_value_as_text(value)))
    return list(dict.fromkeys(link.strip() for link in links if link.strip()))


def merge_note_wikilinks(metadata: dict, body: str) -> list[str]:
    """Body wikilinks/embeds plus frontmatter wikilinks, deduplicated."""
    combined = extract_wikilinks(body) + wikilinks_from_frontmatter(metadata)
    return list(dict.fromkeys(combined))


def extract_embeds(text: str) -> list[str]:
    """Return targets from Obsidian embeds (``![[Note]]``) only."""
    embeds = EMBED_PATTERN.findall(text)
    return list(dict.fromkeys(embed.strip() for embed in embeds if embed.strip()))


def _strings_from_frontmatter(metadata: dict, *keys: str) -> list[str]:
    values: list[str] = []
    raw = None
    for key in keys:
        if key in metadata:
            raw = metadata[key]
            break
    if raw is None:
        return values
    if isinstance(raw, str):
        values.append(raw.strip())
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            text = str(item).strip()
            if text:
                values.append(text)
    return list(dict.fromkeys(v for v in values if v))


def transclusion_excerpts_for_note(
    note: VaultNote,
    *,
    notes_by_path: dict[str, VaultNote] | None = None,
    link_index: WikilinkIndex | None = None,
) -> list[str]:
    """Bounded excerpts from directly embedded notes (``![[…]]``) for indexing."""
    if settings.transclude_depth <= 0 or not notes_by_path or link_index is None:
        return []

    from app.wikilinks import normalize_wikilink_target

    excerpts: list[str] = []
    max_chars = settings.transclude_excerpt_chars
    for embed in extract_embeds(note.content):
        target_path = link_index.resolve(embed)
        if not target_path:
            continue
        target = notes_by_path.get(target_path)
        if target is None:
            continue
        excerpt = target.content.strip()
        if not excerpt:
            continue
        if len(excerpt) > max_chars:
            excerpt = excerpt[: max_chars - 1].rstrip() + "…"
        label = normalize_wikilink_target(embed) or target.title
        excerpts.append(f"[Transcluded: {label}]\n{excerpt}")
    return excerpts


def embedding_text_for_note(
    note: VaultNote,
    *,
    notes_by_path: dict[str, VaultNote] | None = None,
    link_index: WikilinkIndex | None = None,
) -> str:
    """Text to embed: title, aliases, tags, transclusions, then body."""
    header_parts: list[str] = []
    if settings.rich_note_embeddings:
        if note.title:
            header_parts.append(note.title)
        if note.aliases:
            header_parts.extend(note.aliases)
    if note.tags:
        header_parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in note.tags))

    body = note.content.strip()
    transclusions = transclusion_excerpts_for_note(
        note,
        notes_by_path=notes_by_path,
        link_index=link_index,
    )
    if transclusions:
        transclusion_block = "\n\n".join(transclusions)
        body = f"{body}\n\n{transclusion_block}".strip() if body else transclusion_block

    if header_parts:
        header = " | ".join(header_parts)
        return f"{header}\n\n{body}" if body else header
    return body


def parse_note_text(raw: str, source: str = "<note>") -> tuple[dict, str]:
    """Parse frontmatter + body, tolerating malformed YAML frontmatter.

    Obsidian vaults frequently contain notes with invalid YAML (unquoted colons,
    tabs, etc.). A single bad note must not abort indexing, so on a parse error we
    drop the frontmatter block and keep the body.
    """
    try:
        post = frontmatter.loads(raw)
        return dict(post.metadata), post.content or ""
    except Exception as exc:  # noqa: BLE001 - any YAML/parse failure is non-fatal
        logger.warning("Ignoring unparseable frontmatter in %s: %s", source, exc)
        stripped = FRONTMATTER_BLOCK_PATTERN.sub("", raw, count=1)
        return {}, stripped


def _should_skip_dir(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    if any(part in _SKIP_DIR_NAMES for part in path.parts):
        return True
    # Obsidian template folders hold snippets, not indexed knowledge notes.
    return any(part in {"templates", "template"} for part in parts)


def _note_from_file(vault_path: Path, md_path: Path) -> VaultNote:
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    metadata, body = parse_note_text(raw, source=str(md_path))
    note_rel = md_path.relative_to(vault_path)
    return VaultNote(
        path=note_rel,
        title=note_title_from_path(note_rel),
        content=body.strip(),
        frontmatter=metadata,
        wikilinks=merge_note_wikilinks(metadata, body),
        aliases=aliases_from_frontmatter(metadata),
        tags=merge_note_tags(metadata, body),
    )


def load_note(vault_path: Path, relative_path: str | Path) -> VaultNote | None:
    """Load a single vault note by vault-relative path, or None if missing."""
    vault_path = vault_path.resolve()
    rel = Path(relative_path)
    md_path = (vault_path / rel).resolve()
    if not md_path.is_relative_to(vault_path) or not md_path.is_file():
        return None
    if md_path.name.startswith(".") or md_path.suffix.lower() != ".md":
        return None
    if _should_skip_dir(md_path.relative_to(vault_path)):
        return None
    return _note_from_file(vault_path, md_path)


def load_vault(vault_path: Path) -> VaultIndexResult:
    vault_path = vault_path.resolve()
    if not vault_path.is_dir():
        raise ValueError(f"Vault path does not exist or is not a directory: {vault_path}")

    notes: list[VaultNote] = []
    link_count = 0
    stem_map: dict[str, list[str]] = {}

    for md_path in sorted(vault_path.rglob("*.md")):
        if md_path.name.startswith("."):
            continue
        rel = md_path.relative_to(vault_path)
        if _should_skip_dir(rel):
            continue

        note = _note_from_file(vault_path, md_path)
        link_count += len(note.wikilinks)
        notes.append(note)
        stem_map.setdefault(note.path.stem.casefold(), []).append(note.path.as_posix())

    duplicate_stems = {
        stem: paths for stem, paths in stem_map.items() if len(paths) > 1
    }

    return VaultIndexResult(
        notes=notes,
        note_count=len(notes),
        link_count=link_count,
        duplicate_stems=duplicate_stems,
    )
