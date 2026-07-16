"""Fail fast with actionable errors when the active Python env is broken."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType


def _looks_like_broken_protobuf(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "google.protobuf" in message
        or "fielddescriptor" in message
        or "partially initialized module" in message
    )


def import_chromadb() -> ModuleType:
    """Import ChromaDB, raising a helpful error when protobuf is broken."""
    try:
        import chromadb
    except ImportError as exc:
        if not _looks_like_broken_protobuf(exc):
            raise

        project_root = Path(__file__).resolve().parent.parent
        venv_python = project_root / ".venv" / "bin" / "python"
        raise RuntimeError(
            "ChromaDB could not import because google.protobuf is broken in the "
            f"active Python environment ({sys.executable}).\n\n"
            "This usually happens when Anaconda/base Python mixes conda and pip "
            "protobuf packages.\n\n"
            "Fix:\n"
            f"  cd {project_root}\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  pip install -r requirements.txt\n"
            "  .venv/bin/uvicorn app.main:app --reload\n\n"
            + (
                f"Or run: {venv_python} -m uvicorn app.main:app --reload"
                if venv_python.is_file()
                else ""
            )
        ) from exc
    return chromadb
