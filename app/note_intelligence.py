"""RAG context for drafting, plus quality scoring and duplicate detection."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.config import settings
from app.titling import title_is_grounded
from app.vector_protocol import VectorStoreProtocol as VectorStore


@dataclass(frozen=True)
class VaultContextChunk:
    note_path: str
    note_title: str
    heading: str | None
    excerpt: str
    similarity: float

    def to_prompt_block(self) -> str:
        where = self.note_title or self.note_path
        if self.heading:
            where = f"{where} › {self.heading}"
        return (
            f"- [[{self.note_path}]] ({where}, similarity={self.similarity:.2f})\n"
            f"  {self.excerpt}"
        )


def retrieve_vault_context(
    vector_store: VectorStore | None,
    query_text: str,
    *,
    query_tags: list[str] | None = None,
    top_k: int | None = None,
    min_similarity: float | None = None,
    excerpt_chars: int | None = None,
) -> list[VaultContextChunk]:
    """Fetch short vault excerpts to ground LLM drafting (RAG)."""
    if not settings.draft_rag_enabled or vector_store is None:
        return []
    text = (query_text or "").strip()
    if not text or vector_store.chunk_count() == 0:
        return []

    take = max(1, top_k if top_k is not None else settings.draft_rag_top_k)
    floor = (
        settings.novel_threshold
        if min_similarity is None
        else min_similarity
    )
    limit = max(80, excerpt_chars if excerpt_chars is not None else settings.draft_rag_excerpt_chars)

    try:
        matches = vector_store.query_similar(
            text,
            top_k=take,
            query_tags=query_tags,
        )
    except Exception:  # noqa: BLE001 - RAG is best-effort
        return []

    seen_paths: set[str] = set()
    chunks: list[VaultContextChunk] = []
    for match in matches:
        if match.content_similarity < floor:
            continue
        if not match.note_path or match.note_path in seen_paths:
            continue
        seen_paths.add(match.note_path)
        excerpt = " ".join((match.text or "").split())
        if len(excerpt) > limit:
            excerpt = excerpt[: limit - 1].rstrip() + "…"
        chunks.append(
            VaultContextChunk(
                note_path=match.note_path,
                note_title=match.note_title or match.note_path,
                heading=match.heading,
                excerpt=excerpt,
                similarity=round(match.content_similarity, 3),
            )
        )
    return chunks


def format_vault_context(chunks: list[VaultContextChunk]) -> str:
    if not chunks:
        return ""
    return "\n".join(chunk.to_prompt_block() for chunk in chunks)


def score_note_quality(*, concept_title: str, content: str, is_moc: bool = False) -> dict:
    """Heuristic 0–1 quality score for a drafted note (no extra LLM call)."""
    if is_moc or not settings.note_quality_scoring_enabled:
        return {"quality_score": None, "quality_flags": []}

    body = content or ""
    flags: list[str] = []
    points = 0.0
    max_points = 6.0

    # Frontmatter + body present.
    if body.lstrip().startswith("---"):
        points += 1.0
    else:
        flags.append("missing_frontmatter")

    title = (concept_title or "").strip()
    heading_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if heading_match and title and heading_match.group(1).strip().casefold() == title.casefold():
        points += 1.0
    else:
        flags.append("heading_mismatch")

    # Compact definition-ish intro after the H1.
    after_heading = re.split(r"^#\s+.+$", body, maxsplit=1, flags=re.MULTILINE)
    prose = after_heading[1] if len(after_heading) > 1 else body
    prose = re.sub(r"^---[\s\S]*?---\s*", "", prose, count=1).strip()
    first_para = next((line for line in prose.splitlines() if line.strip() and not line.startswith("#")), "")
    if 40 <= len(first_para) <= 400:
        points += 1.0
    else:
        flags.append("weak_definition")

    # H1 is forced to match concept_title during draft, so ground against body
    # prose only — not the heading line.
    if title and not title_is_grounded(title, prose):
        flags.append("title_ungrounded")

    bullet_count = len(re.findall(r"^[ \t]*[-*+]\s+\S", body, re.MULTILINE))
    if 2 <= bullet_count <= 12:
        points += 1.0
    elif bullet_count < 2:
        flags.append("few_bullets")
    else:
        flags.append("too_many_bullets")

    related = re.search(
        r"#{1,6}[ \t]*Related notes\b[\s\S]*?(?=\n#{1,6}\s|\Z)",
        body,
        re.IGNORECASE,
    )
    if related and "[[" in related.group(0):
        if re.search(r"-\s*none\b", related.group(0), re.IGNORECASE):
            flags.append("no_related_links")
            points += 0.25
        else:
            points += 1.0
    else:
        flags.append("missing_related_section")

    line_count = len([line for line in body.splitlines() if line.strip()])
    if 8 <= line_count <= settings.max_note_lines:
        points += 1.0
    elif line_count < 8:
        flags.append("too_short")
    else:
        flags.append("too_long")

    score = round(max(0.0, min(1.0, points / max_points)), 3)
    return {"quality_score": score, "quality_flags": flags}


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _fingerprint_text(concept_title: str, content: str) -> str:
    """Compact text used to embed a proposed note for duplicate detection."""
    body = re.sub(r"^---[\s\S]*?---\s*", "", content or "", count=1)
    body = re.sub(r"#{1,6}\s+", "", body)
    body = " ".join(body.split())
    return f"{concept_title.strip()}\n{body[:800]}".strip()


def detect_duplicates(
    items: list[dict],
    vector_store: VectorStore | None,
) -> list[dict]:
    """Annotate near-duplicate proposed notes (mutates and returns ``items``).

    Each item is a dict with at least ``concept_title``, ``content``, ``note_path``,
    and optionally ``is_moc`` / ``is_novel``. Duplicates get ``duplicate_of`` and
    ``duplicate_similarity``; the earlier / more-novel note stays canonical.
    """
    if not settings.duplicate_detection_enabled or vector_store is None:
        return items
    candidates = [
        (index, item)
        for index, item in enumerate(items)
        if not item.get("is_moc") and item.get("note_path") and item.get("content")
    ]
    if len(candidates) < 2:
        return items

    texts = [
        _fingerprint_text(str(item.get("concept_title", "")), str(item.get("content", "")))
        for _index, item in candidates
    ]
    try:
        vectors = vector_store.embedding_service.embed_texts(
            texts,
            task_type="RETRIEVAL_QUERY",
        )
    except Exception:  # noqa: BLE001 - duplicate detection is best-effort
        return items
    if len(vectors) != len(candidates):
        return items

    normalized = [_normalize_vector(vector) for vector in vectors]
    threshold = settings.duplicate_similarity_threshold
    claimed: set[int] = set()

    # Prefer keeping earlier novel notes as the canonical copy.
    order = sorted(
        range(len(candidates)),
        key=lambda i: (
            0 if candidates[i][1].get("is_novel", True) else 1,
            candidates[i][0],
        ),
    )
    for position, i in enumerate(order):
        if i in claimed:
            continue
        for j in order[position + 1 :]:
            if j in claimed:
                continue
            similarity = _cosine(normalized[i], normalized[j])
            if similarity < threshold:
                continue
            claimed.add(j)
            _index, item = candidates[j]
            item["duplicate_of"] = candidates[i][1].get("note_path")
            item["duplicate_similarity"] = round(similarity, 3)
            item["selected"] = False
    return items


def annotate_note_intelligence(
    suggestions: list,
    vector_store: VectorStore | None,
) -> None:
    """Attach quality scores and duplicate markers onto NoteSuggestion objects."""
    payloads: list[dict] = []
    for suggestion in suggestions:
        quality = score_note_quality(
            concept_title=suggestion.concept_title,
            content=suggestion.content,
            is_moc=suggestion.is_moc,
        )
        suggestion.quality_score = quality["quality_score"]
        suggestion.quality_flags = list(quality["quality_flags"])
        payloads.append(
            {
                "concept_title": suggestion.concept_title,
                "content": suggestion.content,
                "note_path": suggestion.note_path,
                "is_moc": suggestion.is_moc,
                "is_novel": suggestion.is_novel,
            }
        )

    detect_duplicates(payloads, vector_store)
    for suggestion, payload in zip(suggestions, payloads, strict=False):
        suggestion.duplicate_of = payload.get("duplicate_of")
        suggestion.duplicate_similarity = payload.get("duplicate_similarity")


# Re-export for typing convenience in tests.
__all__ = [
    "VaultContextChunk",
    "annotate_note_intelligence",
    "detect_duplicates",
    "format_vault_context",
    "retrieve_vault_context",
    "score_note_quality",
]
