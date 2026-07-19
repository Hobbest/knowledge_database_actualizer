from __future__ import annotations

from collections.abc import Callable
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


@dataclass
class _ChunkScore:
    """A novelty-scoring chunk carved out of a (larger) planning segment."""

    segment_pos: int
    text: str
    best_similarity: float
    is_novel: bool
    is_known: bool
    is_unknown: bool
    matches: list[SimilarChunk] = field(default_factory=list)


def _chunk_segment_for_scoring(segment: SourceSegment) -> list[str]:
    """Split one planning segment into chunk-sized units for novelty scoring.

    Novelty is judged at ``chunk_size`` -- the same granularity the vault is
    indexed at -- so source text is compared against vault chunks span-for-span.
    Querying with a whole (larger) planning segment instead averages its
    embedding and blurs a genuinely novel passage sitting next to familiar
    content, which is why scoring and planning granularity are decoupled.
    """
    chunks = chunk_text(
        segment.text,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    texts = [chunk.text for chunk in chunks if chunk.text.strip()]
    if texts:
        return texts
    stripped = segment.text.strip()
    return [stripped] if stripped else []


def _score_segments_via_chunks(
    segments: list[SourceSegment],
    vector_store: VectorStore,
    *,
    source_tags: list[str] | None,
    top_k: int,
    on_batch: Callable[..., None] | None = None,
) -> tuple[list[SegmentNovelty], list[_ChunkScore], bool]:
    """Score planning segments at chunk granularity, aggregated per segment.

    Returns ``(segment_scores, chunk_scores, rate_limited)``. Each planning
    segment gets one aggregated verdict (novel if ANY of its chunks is novel),
    while ``chunk_scores`` keeps the finer per-chunk detail used for the source
    verdict and overlap reporting. On an embedding rate limit the remaining
    chunks are marked unknown (not novel) so a run can still finish.
    """
    # Local import avoids a module cycle: atomic_notes imports NoveltyResult.
    from app.atomic_notes import SegmentNovelty

    nonempty = [segment for segment in segments if segment.text.strip()]
    if not nonempty:
        return [], [], False

    if vector_store.chunk_count() == 0:
        segment_scores = [
            SegmentNovelty(
                segment=segment,
                best_similarity=0.0,
                is_novel=True,
                is_unknown=False,
            )
            for segment in nonempty
        ]
        chunk_scores = [
            _ChunkScore(
                segment_pos=pos,
                text=segment.text,
                best_similarity=0.0,
                is_novel=True,
                is_known=False,
                is_unknown=False,
            )
            for pos, segment in enumerate(nonempty)
        ]
        return segment_scores, chunk_scores, False

    chunk_texts: list[str] = []
    chunk_owner: list[int] = []
    for pos, segment in enumerate(nonempty):
        for text in _chunk_segment_for_scoring(segment):
            chunk_texts.append(text)
            chunk_owner.append(pos)

    total_chunks = len(chunk_texts)
    matches_per_chunk: list[list[SimilarChunk] | None] = [None] * total_chunks
    batch_size = max(1, settings.embedding_query_batch_size)
    rate_limited = False

    for start in range(0, total_chunks, batch_size):
        batch_texts = chunk_texts[start : start + batch_size]
        try:
            batch_matches = vector_store.query_similar_many(
                batch_texts, top_k=top_k, query_tags=source_tags
            )
        except Exception as exc:  # noqa: BLE001 - rate limits degrade to unknown
            if not is_rate_limit_error(exc):
                raise
            rate_limited = True
            if on_batch is not None:
                on_batch(total_chunks, total_chunks, rate_limited=True)
            break
        for offset, matches in enumerate(batch_matches):
            matches_per_chunk[start + offset] = matches
        if on_batch is not None:
            on_batch(min(start + len(batch_texts), total_chunks), total_chunks, rate_limited=False)

    chunk_scores = []
    for pos, text, matches in zip(chunk_owner, chunk_texts, matches_per_chunk, strict=True):
        if matches is None:
            chunk_scores.append(
                _ChunkScore(
                    segment_pos=pos,
                    text=text,
                    best_similarity=0.0,
                    is_novel=False,
                    is_known=False,
                    is_unknown=True,
                )
            )
            continue
        top = matches[0] if matches else None
        best_similarity = top.content_similarity if top else 0.0
        is_novel, is_known = _classify_chunk(
            best_similarity,
            query_tags=source_tags,
            match_tags=top.tags if top else None,
        )
        chunk_scores.append(
            _ChunkScore(
                segment_pos=pos,
                text=text,
                best_similarity=best_similarity,
                is_novel=is_novel,
                is_known=is_known,
                is_unknown=False,
                matches=matches,
            )
        )

    chunks_by_segment: dict[int, list[_ChunkScore]] = {}
    for chunk in chunk_scores:
        chunks_by_segment.setdefault(chunk.segment_pos, []).append(chunk)

    segment_scores = []
    for pos, segment in enumerate(nonempty):
        reliable = [chunk for chunk in chunks_by_segment.get(pos, []) if not chunk.is_unknown]
        if not reliable:
            segment_scores.append(
                SegmentNovelty(
                    segment=segment,
                    best_similarity=0.0,
                    is_novel=False,
                    is_unknown=True,
                )
            )
            continue
        # A segment is novel if ANY chunk carries new content; best_similarity is
        # its least-covered chunk, so is_novel == best_similarity < NOVEL_THRESHOLD.
        best_similarity = min(chunk.best_similarity for chunk in reliable)
        segment_scores.append(
            SegmentNovelty(
                segment=segment,
                best_similarity=best_similarity,
                is_novel=any(chunk.is_novel for chunk in reliable),
                is_unknown=False,
            )
        )

    return segment_scores, chunk_scores, rate_limited


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
    """Score a source once and derive both the verdict and per-topic scores.

    Novelty is scored at ``chunk_size`` (matching the vault index) while topics
    are planned at planning-segment granularity: each planning segment is split
    into chunks, every chunk is scored, and the chunk verdicts are aggregated
    back up to their segment for planning.
    """
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

    source_tags = list(source.tags) if source.tags else None
    segment_scores, chunk_scores, rate_limited = _score_segments_via_chunks(
        planning_segments,
        vector_store,
        source_tags=source_tags,
        top_k=3,
    )

    warnings: list[str] = []
    if rate_limited:
        warnings.append(
            "Embedding rate limit during similarity scoring; unscored segments "
            "were marked unknown (not novel) so the run can continue."
        )

    chunk_results: list[ChunkNovelty] = []
    note_overlap: dict[str, OverlappingNote] = {}
    novel_chunks: list[str] = []
    known_chunks: list[str] = []
    novelty_scores: list[float] = []
    novel_count = 0
    known_count = 0
    reliable_total = 0

    for index, chunk in enumerate(chunk_scores):
        if not chunk.is_unknown:
            reliable_total += 1
            novelty_scores.append(1.0 - chunk.best_similarity)
        if chunk.is_novel:
            novel_count += 1
            novel_chunks.append(chunk.text)
        if chunk.is_known:
            known_count += 1
            known_chunks.append(chunk.text)

        for match in chunk.matches:
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
                chunk_index=index,
                text=chunk.text,
                best_similarity=chunk.best_similarity,
                is_novel=chunk.is_novel,
                is_known=chunk.is_known,
                matches=chunk.matches,
            )
        )

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
