from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

ProgressFn = Callable[[str, int, int, str], None]


@dataclass
class NoteSuggestion:
    concept_title: str
    note_path: str
    content: str
    location: dict
    segment_indices: list[int]
    write_mode: str = "write"
    append_target: str | None = None
    append_heading: str | None = None
    overlap_similarity: float | None = None
    is_moc: bool = False
    # True when any reliably scored segment looked novel vs the vault.
    # False (known/partial/unknown) → UI default-deselects; drafting still runs.
    is_novel: bool = True
    quality_score: float | None = None
    quality_flags: list[str] | None = None
    duplicate_of: str | None = None
    duplicate_similarity: float | None = None
    update_type: str | None = None
    update_target: str | None = None
    update_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "concept_title": self.concept_title,
            "note_path": self.note_path,
            "content": self.content,
            "location": self.location,
            "segment_indices": self.segment_indices,
            "write_mode": self.write_mode,
            "append_target": self.append_target,
            "append_heading": self.append_heading,
            "overlap_similarity": self.overlap_similarity,
            "is_moc": self.is_moc,
            "is_novel": self.is_novel,
            "quality_score": self.quality_score,
            "quality_flags": list(self.quality_flags or []),
            "duplicate_of": self.duplicate_of,
            "duplicate_similarity": self.duplicate_similarity,
            "update_type": self.update_type,
            "update_target": self.update_target,
            "update_reason": self.update_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NoteSuggestion:
        return cls(
            concept_title=data.get("concept_title", ""),
            note_path=data.get("note_path", ""),
            content=data.get("content", ""),
            location=data.get("location") or {},
            segment_indices=list(data.get("segment_indices") or []),
            write_mode=data.get("write_mode", "write"),
            append_target=data.get("append_target"),
            append_heading=data.get("append_heading"),
            overlap_similarity=data.get("overlap_similarity"),
            is_moc=bool(data.get("is_moc")),
            # Missing key (older checkpoints) → treat as novel so we do not
            # surprise-deselect recovered drafts.
            is_novel=bool(data["is_novel"]) if "is_novel" in data else True,
            quality_score=data.get("quality_score"),
            quality_flags=list(data.get("quality_flags") or []) or None,
            duplicate_of=data.get("duplicate_of"),
            duplicate_similarity=data.get("duplicate_similarity"),
            update_type=data.get("update_type"),
            update_target=data.get("update_target"),
            update_reason=data.get("update_reason"),
        )


@dataclass
class ApplyNoteResult:
    """Per-note outcome from writing a suggestion into the vault."""

    note_path: str
    status: str  # written | appended | skipped_exists | error
    written_path: str | None = None
    error: str | None = None
    overwritten: bool = False
    backup_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MergedNotePreview:
    note_path: str
    mode: str
    exists: bool
    will_write: bool
    existing_content: str
    final_content: str

    def to_dict(self) -> dict:
        return asdict(self)

