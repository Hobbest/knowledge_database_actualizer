#!/usr/bin/env bash
# Stop uvicorn dev servers for this project (frees port 8000 when reload fails).
set -euo pipefail
pkill -f "${PWD}/.venv/bin/uvicorn app.main:app" 2>/dev/null || true
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8000" 2>/dev/null || true
sleep 1
if ss -ltn 2>/dev/null | grep -q ':8000 '; then
  echo "Port 8000 still in use — try: kill -9 \$(ss -ltnp | grep ':8000' | grep -oP 'pid=\\K[0-9]+')"
  exit 1
fi
echo "Port 8000 is free."
