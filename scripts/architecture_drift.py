#!/usr/bin/env python3
"""Fail CI when ARCHITECTURE.md drifts from the running FastAPI app / module tree.

Usage:
  python scripts/architecture_drift.py check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
APP_DIR = ROOT / "app"

# Top-level modules that must appear in the §3 module map (basename without .py).
# Keep in sync when adding public app/ modules that belong in the architecture doc.
CURATED_MODULES = frozenset(
    {
        "analytics",
        "atomic_notes",
        "block_refs",
        "chat",
        "checkpoint",
        "chunking",
        "deps",
        "indexing",
        "auth",
        "embeddings",
        "env_sync",
        "git_integration",
        "graph",
        "index_meta",
        "json_extract",
        "llm",
        "llm_budget",
        "main",
        "media",
        "note_intelligence",
        "note_output",
        "novelty",
        "observability",
        "obsidian_templates",
        "obsidian_uri",
        "plugin_api",
        "preflight",
        "progressive",
        "prompt_domains",
        "prompts",
        "qdrant_store",
        "relevance",
        "reports",
        "runtime",
        "segmentation",
        "settings_persistence",
        "similarity",
        "source_identity",
        "suggest",
        "summarize",
        "threshold_calibration",
        "thresholds",
        "titling",
        "update_detection",
        "url_security",
        "vault",
        "vault_fingerprints",
        "vault_index",
        "vault_watcher",
        "vector_protocol",
        "vectorstore",
        "vision",
        "wikilinks",
    }
)

# Paths that are intentionally not part of the public API surface table.
IGNORED_ROUTE_PATHS = frozenset({"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def _load_architecture_text() -> str:
    if not ARCHITECTURE.is_file():
        raise FileNotFoundError(f"Missing {ARCHITECTURE}")
    return ARCHITECTURE.read_text(encoding="utf-8")


def _documented_api_paths(text: str) -> set[str]:
    """Extract path tokens from the HTTP API surface table (backtick-quoted)."""
    paths: set[str] = set()
    # Match paths inside backticks, including multi-path cells like `GET /`, `/static/*`
    for match in re.finditer(r"`((?:GET|POST|PUT|PATCH|DELETE)\s+)?(/[^`\s,*]+)`", text):
        paths.add(match.group(2))
    # Also catch bare `/static/*` style entries without a method prefix in the same cell
    for match in re.finditer(r"`(/static/\*)`", text):
        paths.add(match.group(1))
    return paths


def _walk_routes(routes: list) -> list:
    """Flatten nested Starlette/FastAPI routers (include_router → _IncludedRouter)."""
    found: list = []
    for route in routes:
        nested = getattr(route, "routes", None)
        if nested:
            found.extend(_walk_routes(list(nested)))
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            found.extend(_walk_routes(list(original.routes)))
            continue
        found.append(route)
    return found


def _app_api_paths() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from app.main import app  # noqa: E402

    paths: set[str] = set()
    for route in _walk_routes(list(app.routes)):
        path = getattr(route, "path", None)
        if not path or path in IGNORED_ROUTE_PATHS:
            continue
        if path.startswith("/api/"):
            paths.add(path)
    return paths


def _missing_modules(text: str) -> list[str]:
    missing: list[str] = []
    for name in sorted(CURATED_MODULES):
        # Accept mentions as module.py, module/, or `module`
        if not re.search(rf"(?m)(^|[\s`|/]){re.escape(name)}(\.py|/|\.md|`|\s|,|$)", text):
            missing.append(name)
    return missing


def check() -> int:
    text = _load_architecture_text()
    errors: list[str] = []

    documented = _documented_api_paths(text)
    live = _app_api_paths()
    undocumented = sorted(live - documented)
    if undocumented:
        errors.append(
            "API routes missing from ARCHITECTURE.md §12 table:\n  - "
            + "\n  - ".join(undocumented)
        )

    stale_doc_only = sorted(
        p for p in documented if p.startswith("/api/") and p not in live and "*" not in p
    )
    if stale_doc_only:
        errors.append(
            "ARCHITECTURE.md documents API paths not present on the FastAPI app:\n  - "
            + "\n  - ".join(stale_doc_only)
        )

    missing_mods = _missing_modules(text)
    if missing_mods:
        errors.append(
            "Curated app/ modules missing from ARCHITECTURE.md module map:\n  - "
            + "\n  - ".join(missing_mods)
        )

    if errors:
        print("Architecture drift detected:\n", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
            print(file=sys.stderr)
        print("Update ARCHITECTURE.md to match app/, then re-run this check.", file=sys.stderr)
        return 1

    print(
        f"ARCHITECTURE.md is in sync ({len(live)} /api routes; "
        f"{len(CURATED_MODULES)} curated modules)."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Fail if ARCHITECTURE.md drifts from code")
    args = parser.parse_args()
    if args.command == "check":
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
