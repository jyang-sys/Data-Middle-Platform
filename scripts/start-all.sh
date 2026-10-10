#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m app.worker &
worker_pid=$!
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
api_pid=$!
cleanup() {
  kill "$worker_pid" "$api_pid" 2>/dev/null || true
  wait "$worker_pid" "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
wait -n "$worker_pid" "$api_pid"
