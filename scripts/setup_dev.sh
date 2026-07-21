#!/usr/bin/env bash
# Create or update a development environment without replacing local configuration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  "$PYTHON" -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements-dev.txt"
"$ROOT/.venv/bin/python" "$ROOT/scripts/env_sync.py" merge

echo
echo "Development environment is ready."
echo "Run: .venv/bin/uvicorn app.main:app --reload"
