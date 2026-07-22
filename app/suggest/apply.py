from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from app.block_refs import inject_block_references
from app.note_output import merge_append_into_note
from app.suggest.models import ApplyNoteResult, MergedNotePreview


def _resolve_vault_target(vault_path: Path, note_path: str) -> Path:
    vault_path = vault_path.resolve()
    target = (vault_path / note_path).resolve()
    if not target.is_relative_to(vault_path):
        raise ValueError("Refusing to write outside the configured vault path")
    return target


def _backup_existing_note(target: Path, vault_path: Path) -> str:
    """Copy an existing note to ``*.md.bak`` before overwrite; return vault-relative path."""
    backup = target.with_name(target.name + ".bak")
    shutil.copy2(target, backup)
    return backup.relative_to(vault_path.resolve()).as_posix()


def _atomic_write_text(path: Path, content: str) -> None:
    """Write UTF-8 text via temp file + rename so crashes cannot truncate notes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def preview_suggestion_merge(
    vault_path: Path,
    note_path: str,
    content: str,
    mode: str = "write",
    *,
    overwrite: bool = False,
    append_heading: str | None = None,
) -> MergedNotePreview:
    """Return the exact bytes-as-text that apply would write, without mutating the vault."""
    if mode not in {"write", "append"}:
        raise ValueError("mode must be 'write' or 'append'")
    target = _resolve_vault_target(vault_path, note_path)
    rel = target.relative_to(vault_path.resolve()).as_posix()
    exists = target.is_file()
    existing = target.read_text(encoding="utf-8") if exists else ""
    prepared = inject_block_references(content)
    if mode == "append" and exists:
        final = merge_append_into_note(
            existing,
            prepared,
            target_heading=append_heading,
            fallback_heading="Update",
        )
    elif mode == "append":
        final = inject_block_references(content.strip()) + "\n"
    else:
        final = existing if exists and not overwrite else prepared
    will_write = mode == "append" or not exists or overwrite
    return MergedNotePreview(
        note_path=rel,
        mode=mode,
        exists=exists,
        will_write=will_write,
        existing_content=existing,
        final_content=final,
    )


def apply_suggestion(
    vault_path: Path,
    note_path: str,
    content: str,
    mode: str = "write",
    *,
    overwrite: bool = False,
    append_heading: str | None = None,
) -> ApplyNoteResult:
    """Write one note into the vault.

    For ``mode="write"``, an existing file is left untouched unless ``overwrite``
    is True. Overwrites keep a ``.bak`` sibling copy of the previous content.
    """
    try:
        target = _resolve_vault_target(vault_path, note_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        preview = preview_suggestion_merge(
            vault_path,
            note_path,
            content,
            mode,
            overwrite=overwrite,
            append_heading=append_heading,
        )
        rel = preview.note_path

        if mode == "append" and target.exists():
            _atomic_write_text(target, preview.final_content)
            return ApplyNoteResult(note_path=note_path, status="appended", written_path=rel)

        if mode == "append" and not target.exists():
            _atomic_write_text(target, preview.final_content)
            return ApplyNoteResult(note_path=note_path, status="written", written_path=rel)

        if mode == "write" and target.exists() and not overwrite:
            return ApplyNoteResult(
                note_path=note_path,
                status="skipped_exists",
                error="Note already exists; pass overwrite=true to replace (a .bak backup is kept)",
            )

        backup_path: str | None = None
        overwritten = False
        if mode == "write" and target.exists() and overwrite:
            backup_path = _backup_existing_note(target, vault_path)
            overwritten = True

        _atomic_write_text(target, preview.final_content)
        return ApplyNoteResult(
            note_path=note_path,
            status="written",
            written_path=rel,
            overwritten=overwritten,
            backup_path=backup_path,
        )
    except Exception as exc:  # noqa: BLE001 - surface per-note failures to the batch API
        return ApplyNoteResult(note_path=note_path, status="error", error=str(exc))


def apply_suggestions(
    vault_path: Path,
    notes: list[dict],
) -> list[ApplyNoteResult]:
    """Apply many notes, collecting per-note results instead of aborting on the first failure."""
    results: list[ApplyNoteResult] = []
    for note in notes:
        result = apply_suggestion(
            vault_path=vault_path,
            note_path=note["note_path"],
            content=note["content"],
            mode=note.get("mode", "write"),
            overwrite=bool(note.get("overwrite", False)),
            append_heading=note.get("append_heading"),
        )
        results.append(result)
    return results

