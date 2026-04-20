#!/usr/bin/env bash
# Render Native Python 시작 (Docker가 아닐 때만 사용)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
