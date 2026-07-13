from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.chunking import chunk_text
from app.config import settings
from app.text_limits import TEXT_LIMITS
from app.vectorstore import SimilarChunk, VectorStore


class Verdict(str, Enum):
    ALREADY_KNOWN = "Already known"
    PARTIALLY_NEW = "Partially new"
    NOVEL = "Novel"


@dataclass
class ChunkNovelty:
    chunk_index: int
    text: str
    best_similarity: float
    is_novel: bool
    is_known: bool
    matches: list[SimilarChunk] = field(default_factory=list)


@dataclass
class OverlappingNote:
    note_path: str
    note_title: str
    max_similarity: float
    sample_text: str
    tags: list[str] = field(default_factory=list)
    sample_heading: str | None = None


@dataclass
class NoveltyResult:
    verdict: Verdict
    novelty_score: float
    chunk_results: list[ChunkNovelty]
    overlapping_notes: list[OverlappingNote]
    novel_chunks: list[str]
    known_chunks: list[str]


def _classify_chunk(similarity: float) -> tuple[bool, bool]:
    is_novel = similarity < settings.novel_threshold
    is_known = similarity >= settings.known_threshold
    return is_novel, is_known


def _aggregate_verdict(novel_count: int, known_count: int, total: int) -> Verdict:
    if total == 0:
        return Verdict.NOVEL

    novel_ratio = novel_count / total
    known_ratio = known_count / total

    if known_ratio >= 0.8:
        return Verdict.ALREADY_KNOWN
    if novel_ratio >= 0.6:
        return Verdict.NOVEL
    return Verdict.PARTIALLY_NEW


def analyze_novelty(
    source_text: str,
    vector_store: VectorStore,
    *,
    top_k: int = 3,
    source_tags: list[str] | None = None,
) -> NoveltyResult:
    chunks = chunk_text(
        source_text,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    if not chunks:
        return NoveltyResult(
            verdict=Verdict.NOVEL,
            novelty_score=1.0,
            chunk_results=[],
            overlapping_notes=[],
            novel_chunks=[],
            known_chunks=[],
        )

    chunk_results: list[ChunkNovelty] = []
    note_overlap: dict[str, OverlappingNote] = {}
    novel_chunks: list[str] = []
    known_chunks: list[str] = []
    novel_count = 0
    known_count = 0
    novelty_scores: list[float] = []

    indexed = vector_store.chunk_count() > 0
    batch_size = max(1, settings.embedding_query_batch_size)

    # Precompute matches in batches when an index exists.
    matches_by_chunk: list[list[SimilarChunk]] = [[] for _ in chunks]
    if indexed:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            texts = [chunk.text for chunk in batch]
            batch_matches = vector_store.query_similar_many(
                texts, top_k=top_k, query_tags=source_tags
            )
            for offset, matches in enumerate(batch_matches):
                matches_by_chunk[start + offset] = matches

    for chunk, matches in zip(chunks, matches_by_chunk, strict=True):
        if not indexed:
            is_novel, is_known = True, False
            best_similarity = 0.0
            matches = []
        else:
            best_similarity = matches[0].similarity if matches else 0.0
            is_novel, is_known = _classify_chunk(best_similarity)

            for match in matches:
                key = match.note_path
                existing = note_overlap.get(key)
                if not existing or match.similarity > existing.max_similarity:
                    heading = (match.heading or "").strip() or None
                    note_overlap[key] = OverlappingNote(
                        note_path=match.note_path,
                        note_title=match.note_title,
                        max_similarity=match.similarity,
                        sample_text=match.text[: TEXT_LIMITS.api_overlap_preview_chars],
                        tags=list(match.tags),
                        sample_heading=heading,
                    )

        if is_novel:
            novel_count += 1
            novel_chunks.append(chunk.text)
        if is_known:
            known_count += 1
            known_chunks.append(chunk.text)

        novelty_scores.append(1.0 - best_similarity)

        chunk_results.append(
            ChunkNovelty(
                chunk_index=chunk.index,
                text=chunk.text,
                best_similarity=best_similarity,
                is_novel=is_novel,
                is_known=is_known,
                matches=matches,
            )
        )

    total = len(chunks)
    verdict = _aggregate_verdict(novel_count, known_count, total)
    novelty_score = sum(novelty_scores) / len(novelty_scores) if novelty_scores else 1.0

    overlapping_notes = sorted(
        note_overlap.values(),
        key=lambda item: item.max_similarity,
        reverse=True,
    )

    return NoveltyResult(
        verdict=verdict,
        novelty_score=round(novelty_score, 3),
        chunk_results=chunk_results,
        overlapping_notes=overlapping_notes,
        novel_chunks=novel_chunks,
        known_chunks=known_chunks,
    )
