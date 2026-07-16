"""Note path, template, and append helpers for vault writes."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath

from app.atomic_notes import AtomicTopic
from app.chunking import _split_by_headings
from app.config import settings
from app.obsidian_templates import discover_obsidian_template, expand_template_placeholders
from app.sources.base import LoadedSource, SourceLocation
from app.text_utils import combine_segment_text
from app.vault import parse_note_text
from app.vectorstore import VectorStore

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")
_HEADING_COMPARE = re.compile(r"\s+")


def _safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "- _" else "_" for ch in value)
    return slug.strip("_") or "concept"


def normalize_vault_relative_path(
    path: str | Path,
    vault_path: Path | None = None,
) -> str | None:
    """Return a vault-relative POSIX path, or None if outside the vault."""
    vault = (vault_path or settings.vault_path)
    if vault is None:
        raw = str(path).replace("\\", "/").strip().lstrip("./")
        return raw or None

    vault = vault.resolve()
    candidate = Path(str(path))
    rel: PurePath
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(vault)
        except ValueError:
            return None
    else:
        rel = PurePosixPath(str(path).replace("\\", "/").strip().lstrip("./"))

    rel_posix = rel.as_posix()
    if ".." in rel_posix.split("/"):
        return None
    return rel_posix or None


def vault_relative_paths_equal(left: str | Path, right: str | Path) -> bool:
    left_norm = normalize_vault_relative_path(left)
    right_norm = normalize_vault_relative_path(right)
    return bool(left_norm and right_norm and left_norm.casefold() == right_norm.casefold())


def _sanitize_output_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip().lstrip("./")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Invalid note output path (path traversal): {path}")
    if not cleaned.lower().endswith(".md"):
        cleaned = f"{cleaned}.md" if cleaned else "note.md"
    return cleaned


def _default_output_pattern() -> str:
    if settings.note_output_layout.strip().lower() == "flat":
        return "{folder}/{concept_slug}.md"
    return "{folder}/{source_slug}/{concept_slug}.md"


def render_note_output_path(
    source: LoadedSource,
    concept_title: str,
    *,
    now: datetime | None = None,
) -> str:
    """Build a vault-relative note path from the configured pattern."""
    moment = now or datetime.now(timezone.utc)
    pattern = (settings.note_output_pattern or "").strip() or _default_output_pattern()
    folder = settings.note_output_folder.strip().strip("/")

    replacements = {
        "folder": folder,
        "source_slug": _safe_slug(source.title),
        "concept_slug": _safe_slug(concept_title),
        "source_title": source.title,
        "source_type": source.source_type,
        "date": moment.strftime("%Y-%m-%d"),
        "year": moment.strftime("%Y"),
        "month": moment.strftime("%m"),
        "day": moment.strftime("%d"),
    }

    rendered = pattern
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{key}}}", value)

    unknown = _PLACEHOLDER_PATTERN.findall(rendered)
    if unknown:
        logger.warning("Unknown note output placeholders ignored: %s", unknown)

    if folder and not rendered.startswith(f"{folder}/") and "{folder}" not in pattern:
        rendered = f"{folder}/{rendered.lstrip('/')}"

    return _sanitize_output_path(rendered)


def default_note_path(source: LoadedSource, concept_title: str) -> str:
    return render_note_output_path(source, concept_title)


def moc_note_path(source: LoadedSource) -> str:
    sample = render_note_output_path(source, "index")
    parent = PurePosixPath(sample).parent
    if str(parent) in {"", "."}:
        return "index.md"
    return f"{parent.as_posix()}/index.md"


def resolve_template_path(vault_path: Path | None = None) -> Path | None:
    raw = settings.note_template_path
    if raw is not None and str(raw).strip():
        path = Path(str(raw)).expanduser()
        if path.is_file():
            return path.resolve()
        if vault_path and not path.is_absolute():
            candidate = (vault_path / path).resolve()
            if candidate.is_file():
                return candidate.resolve()

    discovered = discover_obsidian_template(vault_path)
    if discovered is not None:
        logger.debug("Using Obsidian template: %s", discovered)
    return discovered


def apply_note_template(
    content: str,
    *,
    vault_path: Path | None,
    title: str,
    concept: str,
    tags: list[str],
    source: LoadedSource,
    location: SourceLocation,
) -> str:
    """Substitute placeholders when a template file is configured."""
    template_path = resolve_template_path(vault_path)
    if template_path is None:
        return content

    template = template_path.read_text(encoding="utf-8")
    if "{{body}}" not in template and "{{frontmatter}}" not in template and "{{title}}" not in template:
        return content

    frontmatter, body = parse_note_text(content)
    frontmatter_text = ""
    if content.lstrip().startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter_text = f"---{parts[1]}---"

    replacements = {
        "title": title,
        "concept": concept,
        "body": body.strip(),
        "tags": ", ".join(tags),
        "source_ref": source.source_ref,
        "source": source.source_ref,
        "source_type": source.source_type,
        "source_location": location.display(),
        "created": datetime.now(timezone.utc).isoformat(),
        "frontmatter": frontmatter_text.strip(),
    }
    rendered = expand_template_placeholders(template, replacements)
    return rendered.strip() + "\n"


def parse_append_target(target: str | None) -> tuple[str | None, str | None]:
    """Split ``notes/topic.md#Section`` into path and optional heading."""
    if not target:
        return None, None
    raw = target.strip()
    if "#" in raw:
        path, _, heading = raw.partition("#")
        path = path.strip()
        heading = heading.strip().lstrip("#").strip()
        return (path or None), (heading or None)
    return raw, None


def _normalize_heading(value: str) -> str:
    return _HEADING_COMPARE.sub(" ", value.strip().casefold())


def topic_overlap_match(
    vector_store: VectorStore | None,
    topic: AtomicTopic,
    *,
    query_tags: list[str] | None = None,
) -> tuple[str, float, str | None] | None:
    """Best vault note match for a topic when similarity meets KNOWN_THRESHOLD."""
    if vector_store is None or vector_store.chunk_count() == 0:
        return None
    text = combine_segment_text(topic.segments).strip()
    if not text:
        return None
    matches = vector_store.query_similar(text, top_k=1, query_tags=query_tags)
    if not matches:
        return None
    best = matches[0]
    if best.similarity < settings.known_threshold:
        return None
    return best.note_path, best.similarity, best.heading


def content_for_append(full_content: str, *, heading: str = "Update") -> str:
    """Strip YAML frontmatter and wrap body for appending to an existing note."""
    _, body = parse_note_text(full_content)
    body = body.strip()
    if not body:
        return ""
    if body.lstrip().startswith("#"):
        return body
    return f"## {heading}\n\n{body}"


def merge_append_into_note(
    existing_content: str,
    draft_content: str,
    *,
    target_heading: str | None = None,
    fallback_heading: str = "Update",
) -> str:
    """Append draft body to an existing note, optionally under a matching heading."""
    _, existing_body = parse_note_text(existing_content)
    append_body = content_for_append(draft_content, heading=fallback_heading)
    if not append_body:
        return existing_content.rstrip() + "\n"

    if not target_heading:
        return existing_content.rstrip() + "\n\n" + append_body.strip() + "\n"

    target_norm = _normalize_heading(target_heading)
    sections = _split_by_headings(existing_body)
    if not sections:
        return existing_content.rstrip() + "\n\n" + append_body.strip() + "\n"

    rebuilt: list[str] = []
    matched = False
    for index, (heading, section_body) in enumerate(sections):
        if heading is None:
            rebuilt.append(section_body.strip())
            continue

        rebuilt.append(f"## {heading}")
        if _normalize_heading(heading) == target_norm:
            matched = True
            merged = section_body.rstrip()
            if append_body.lstrip().startswith("#"):
                merged = merged + "\n\n" + append_body.strip()
            else:
                merged = merged + "\n\n" + append_body.strip()
            rebuilt.append(merged)
        else:
            rebuilt.append(section_body.strip())

        if index < len(sections) - 1:
            rebuilt.append("")

    if not matched:
        return existing_content.rstrip() + "\n\n" + append_body.strip() + "\n"

    new_body = "\n".join(part for part in rebuilt if part is not None).strip() + "\n"
    if existing_content.lstrip().startswith("---"):
        parts = existing_content.split("---", 2)
        if len(parts) >= 3:
            return f"---{parts[1]}---\n{new_body}"
    return new_body
