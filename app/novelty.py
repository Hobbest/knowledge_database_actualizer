from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from app.chunking import chunk_text
from app.config import settings
from app.llm import is_rate_limit_error
from app.relevance import filter_relevant_segments
from app.segmentation import split_large_segments
from app.similarity import classification_similarity
from app.sources.base import LoadedSource, SourceSegment
from app.text_limits import TEXT_LIMITS
from app.vectorstore import SimilarChunk, VectorStore

if TYPE_CHECKING:
    from app.atomic_notes import SegmentNovelty


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


@dataclass
class SourceSimilarityAnalysis:
    novelty: NoveltyResult
    planning_segments: list[SourceSegment]
    segment_scores: list[SegmentNovelty]
    warnings: list[str] = field(default_factory=list)


def _classify_chunk(
    base_similarity: float,
    query_tags: list[str] | None = None,
    match_tags: list[str] | None = None,
) -> tuple[bool, bool]:
    # Novelty is decided on the raw cosine so a shared tag cannot mask genuinely
    # new content. The "known" side uses the gray-zone tag nudge, letting an
    # on-topic match tip a borderline segment over KNOWN_THRESHOLD.
    is_novel = base_similarity < settings.novel_threshold
    effective = classification_similarity(base_similarity, query_tags, match_tags)
    is_known = effective >= settings.known_threshold
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
            top = matches[0] if matches else None
            best_similarity = top.content_similarity if top else 0.0
            is_novel, is_known = _classify_chunk(
                best_similarity,
                query_tags=source_tags,
                match_tags=top.tags if top else None,
            )

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


def analyze_source_similarity(
    source: LoadedSource,
    vector_store: VectorStore,
) -> SourceSimilarityAnalysis:
    """Score planning segments once and derive both verdict and topic scores."""
    # Local import avoids a module cycle: atomic_notes imports NoveltyResult.
    from app.atomic_notes import SegmentNovelty

    planning_segments = prepare_planning_segments(source)
    if not planning_segments:
        return SourceSimilarityAnalysis(
            novelty=NoveltyResult(
                verdict=Verdict.NOVEL,
                novelty_score=1.0,
                chunk_results=[],
                overlapping_notes=[],
                novel_chunks=[],
                known_chunks=[],
            ),
            planning_segments=[],
            segment_scores=[],
        )

    indexed = vector_store.chunk_count() > 0
    source_tags = list(source.tags) if source.tags else None
    matches_by_segment: list[list[SimilarChunk] | None] = [
        [] if not indexed else None for _ in planning_segments
    ]
    warnings: list[str] = []

    if indexed:
        batch_size = max(1, settings.embedding_query_batch_size)
        for start in range(0, len(planning_segments), batch_size):
            batch = planning_segments[start : start + batch_size]
            try:
                batch_matches = vector_store.query_similar_many(
                    [segment.text.strip() for segment in batch],
                    top_k=3,
                    query_tags=source_tags,
                )
            except Exception as exc:  # noqa: BLE001 - rate limits degrade to unknown
                if not is_rate_limit_error(exc):
                    raise
                warnings.append(
                    "Embedding rate limit during similarity scoring; unscored segments "
                    "were marked unknown (not novel) so the run can continue."
                )
                break
            for offset, matches in enumerate(batch_matches):
                matches_by_segment[start + offset] = matches

    chunk_results: list[ChunkNovelty] = []
    segment_scores: list[SegmentNovelty] = []
    note_overlap: dict[str, OverlappingNote] = {}
    novel_chunks: list[str] = []
    known_chunks: list[str] = []
    novelty_scores: list[float] = []
    novel_count = 0
    known_count = 0

    for segment, maybe_matches in zip(planning_segments, matches_by_segment, strict=True):
        is_unknown = maybe_matches is None
        matches = maybe_matches or []
        if not indexed:
            best_similarity = 0.0
            is_novel, is_known = True, False
        elif is_unknown:
            best_similarity = 0.0
            is_novel, is_known = False, False
        else:
            top = matches[0] if matches else None
            best_similarity = top.content_similarity if top else 0.0
            is_novel, is_known = _classify_chunk(
                best_similarity,
                query_tags=source_tags,
                match_tags=top.tags if top else None,
            )

        if not is_unknown:
            novelty_scores.append(1.0 - best_similarity)
        if is_novel:
            novel_count += 1
            novel_chunks.append(segment.text)
        if is_known:
            known_count += 1
            known_chunks.append(segment.text)

        for match in matches:
            existing = note_overlap.get(match.note_path)
            if not existing or match.similarity > existing.max_similarity:
                note_overlap[match.note_path] = OverlappingNote(
                    note_path=match.note_path,
                    note_title=match.note_title,
                    max_similarity=match.similarity,
                    sample_text=match.text[: TEXT_LIMITS.api_overlap_preview_chars],
                    tags=list(match.tags),
                    sample_heading=(match.heading or "").strip() or None,
                )

        chunk_results.append(
            ChunkNovelty(
                chunk_index=segment.index,
                text=segment.text,
                best_similarity=best_similarity,
                is_novel=is_novel,
                is_known=is_known,
                matches=matches,
            )
        )
        segment_scores.append(
            SegmentNovelty(
                segment=segment,
                best_similarity=best_similarity,
                is_novel=is_novel,
                is_unknown=is_unknown,
            )
        )

    reliable_total = sum(1 for item in segment_scores if not item.is_unknown)
    verdict = _aggregate_verdict(novel_count, known_count, reliable_total)
    novelty_score = sum(novelty_scores) / len(novelty_scores) if novelty_scores else 1.0
    novelty = NoveltyResult(
        verdict=verdict,
        novelty_score=round(novelty_score, 3),
        chunk_results=chunk_results,
        overlapping_notes=sorted(
            note_overlap.values(),
            key=lambda item: item.max_similarity,
            reverse=True,
        ),
        novel_chunks=novel_chunks,
        known_chunks=known_chunks,
    )
    return SourceSimilarityAnalysis(
        novelty=novelty,
        planning_segments=planning_segments,
        segment_scores=segment_scores,
        warnings=warnings,
    )


def prepare_planning_segments(source: LoadedSource) -> list[SourceSegment]:
    base_segments = source.segments
    if settings.filter_boilerplate:
        base_segments = filter_relevant_segments(base_segments)
    return [
        segment
        for segment in split_large_segments(
            base_segments,
            target_chars=settings.segment_target_chars,
        )
        if segment.text.strip()
    ]


def novelty_to_checkpoint(novelty: NoveltyResult) -> dict:
    return {
        "verdict": novelty.verdict.value,
        "novelty_score": novelty.novelty_score,
        "chunk_results": [
            {
                "chunk_index": item.chunk_index,
                "text": item.text,
                "best_similarity": item.best_similarity,
                "is_novel": item.is_novel,
                "is_known": item.is_known,
            }
            for item in novelty.chunk_results
        ],
        "overlapping_notes": [
            {
                "note_path": item.note_path,
                "note_title": item.note_title,
                "max_similarity": item.max_similarity,
                "sample_text": item.sample_text,
                "tags": list(item.tags),
                "sample_heading": item.sample_heading,
            }
            for item in novelty.overlapping_notes
        ],
        "novel_chunks": list(novelty.novel_chunks),
        "known_chunks": list(novelty.known_chunks),
    }


def novelty_from_checkpoint(data: dict) -> NoveltyResult:
    return NoveltyResult(
        verdict=Verdict(data["verdict"]),
        novelty_score=float(data.get("novelty_score", 1.0)),
        chunk_results=[
            ChunkNovelty(
                chunk_index=int(item.get("chunk_index", 0)),
                text=str(item.get("text", "")),
                best_similarity=float(item.get("best_similarity", 0.0)),
                is_novel=bool(item.get("is_novel")),
                is_known=bool(item.get("is_known")),
                matches=[],
            )
            for item in data.get("chunk_results") or []
        ],
        overlapping_notes=[
            OverlappingNote(
                note_path=str(item.get("note_path", "")),
                note_title=str(item.get("note_title", "")),
                max_similarity=float(item.get("max_similarity", 0.0)),
                sample_text=str(item.get("sample_text", "")),
                tags=list(item.get("tags") or []),
                sample_heading=item.get("sample_heading"),
            )
            for item in data.get("overlapping_notes") or []
        ],
        novel_chunks=list(data.get("novel_chunks") or []),
        known_chunks=list(data.get("known_chunks") or []),
    )
