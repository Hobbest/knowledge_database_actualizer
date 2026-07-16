#!/usr/bin/env bash
# Start the Actualizer API using the project virtualenv (avoids Anaconda protobuf conflicts).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Missing project virtualenv. Create it with:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/pip install -r requirements.txt"
  exit 1
fi

exec "$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 --reload "$@"
