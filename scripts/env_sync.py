#!/usr/bin/env python3
"""Keep environment files aligned with app/config.py without touching secrets.

Usage:
  python scripts/env_sync.py generate   # rewrite .env.example from Settings defaults
  python scripts/env_sync.py check      # exit 1 if .env.example is stale (for CI)
  python scripts/env_sync.py merge      # add missing keys to .env (never overwrite)
  python scripts/env_sync.py merge --dry-run

Your local ``.env`` is gitignored. Only you set API keys there; generation uses
empty placeholders for secret fields.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.env_sync import (  # noqa: E402
    ENV_EXAMPLE_PATH,
    env_example_is_current,
    merge_local_env,
    write_env_example,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate", help="Rewrite .env.example from Settings defaults")
    sub.add_parser("check", help="Fail if .env.example differs from generated output")

    merge_parser = sub.add_parser(
        "merge",
        help="Append missing keys from .env.example into .env (no overwrites)",
    )
    merge_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print keys that would be added without writing .env",
    )

    args = parser.parse_args()

    if args.command == "generate":
        write_env_example()
        print(f"Wrote {ENV_EXAMPLE_PATH}")
        return 0

    if args.command == "check":
        if env_example_is_current():
            print(".env.example is up to date with Settings.")
            return 0
        print(
            ".env.example is out of date. Run: python scripts/env_sync.py generate",
            file=sys.stderr,
        )
        return 1

    if args.command == "merge":
        added = merge_local_env(dry_run=args.dry_run)
        if not added:
            print("No missing keys; .env already has every variable from .env.example.")
            return 0
        if args.dry_run:
            print("Would add:", ", ".join(added))
        else:
            print("Added to .env:", ", ".join(added))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
