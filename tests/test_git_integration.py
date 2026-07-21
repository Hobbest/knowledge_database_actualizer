from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from app.git_integration import commit_written_paths


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_commit_contains_only_explicit_paths(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "selected.md").write_text("before\n")
    (tmp_path / "unrelated.md").write_text("before\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    (tmp_path / "selected.md").write_text("selected\n")
    (tmp_path / "unrelated.md").write_text("unrelated\n")
    _git(tmp_path, "add", "unrelated.md")

    result = commit_written_paths(tmp_path, ["selected.md"], message="Selected only")

    assert result.committed
    assert _git(tmp_path, "show", "--name-only", "--format=", "HEAD").strip() == "selected.md"
    assert "unrelated.md" in _git(tmp_path, "diff", "--cached", "--name-only")


def test_commit_rejects_paths_outside_vault(tmp_path: Path):
    with pytest.raises(ValueError, match="vault-relative"):
        commit_written_paths(tmp_path, ["../outside.md"])
