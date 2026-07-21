from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class GitCommitResult:
    status: str
    committed: bool = False
    commit: str | None = None
    paths: list[str] = field(default_factory=list)
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_git(
    cwd: Path,
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _safe_paths(vault_path: Path, relative_paths: Iterable[str | Path]) -> list[str]:
    vault = vault_path.resolve()
    safe: list[str] = []
    for raw_path in relative_paths:
        posix = PurePosixPath(str(raw_path).replace("\\", "/"))
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"Path must be vault-relative: {raw_path}")
        relative = posix.as_posix()
        if relative in {"", "."}:
            raise ValueError("Empty vault-relative path is not allowed")
        candidate = (vault / relative).resolve(strict=False)
        if not candidate.is_relative_to(vault):
            raise ValueError(f"Path escapes the vault: {raw_path}")
        if relative not in safe:
            safe.append(relative)
    return safe


def commit_written_paths(
    vault_path: Path,
    relative_paths: Iterable[str | Path],
    *,
    message: str = "Update knowledge vault",
) -> GitCommitResult:
    """Commit only explicitly supplied vault-relative paths.

    Existing staged or unstaged changes outside ``relative_paths`` are never
    included. The helper does not amend, force, push, or invoke a shell.
    """

    vault = vault_path.resolve()
    paths = _safe_paths(vault, relative_paths)
    if not paths:
        return GitCommitResult(status="no_paths", message=message)
    if not message.strip():
        raise ValueError("Commit message must not be empty")

    try:
        repo_result = _run_git(vault, "rev-parse", "--show-toplevel")
    except FileNotFoundError:
        return GitCommitResult(
            status="git_unavailable",
            paths=paths,
            message=message,
            error="git executable was not found",
        )
    if repo_result.returncode != 0:
        return GitCommitResult(
            status="not_a_repository",
            paths=paths,
            message=message,
            error=repo_result.stderr.strip() or "vault is not inside a Git repository",
        )

    repo_root = Path(repo_result.stdout.strip()).resolve()
    try:
        vault_prefix = vault.relative_to(repo_root)
    except ValueError:
        return GitCommitResult(
            status="not_a_repository",
            paths=paths,
            message=message,
            error="Git repository root does not contain the vault",
        )
    repo_paths = [(vault_prefix / path).as_posix() for path in paths]

    status_result = _run_git(
        repo_root, "status", "--porcelain", "--untracked-files=all", "--", *repo_paths
    )
    if status_result.returncode != 0:
        return GitCommitResult(
            status="error",
            paths=paths,
            message=message,
            error=status_result.stderr.strip() or "git status failed",
        )
    if not status_result.stdout.strip():
        return GitCommitResult(status="no_changes", paths=paths, message=message)

    add_result = _run_git(repo_root, "add", "--", *repo_paths)
    if add_result.returncode != 0:
        return GitCommitResult(
            status="error",
            paths=paths,
            message=message,
            error=add_result.stderr.strip() or "git add failed",
        )

    # --only with an explicit pathspec prevents pre-existing staged changes from
    # entering this commit. It also supports deletions and newly-added files
    # after the targeted `git add` above.
    commit_result = _run_git(
        repo_root, "commit", "--only", "-m", message, "--", *repo_paths
    )
    if commit_result.returncode != 0:
        return GitCommitResult(
            status="error",
            paths=paths,
            message=message,
            error=commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed",
        )
    revision = _run_git(repo_root, "rev-parse", "HEAD")
    commit = revision.stdout.strip() if revision.returncode == 0 else None
    return GitCommitResult(
        status="committed",
        committed=True,
        commit=commit,
        paths=paths,
        message=message,
    )


# Integration-friendly aliases for callers that describe the operation by vault
# rather than by the implementation detail of written paths.
commit_vault_changes = commit_written_paths
commit_written_files = commit_written_paths
