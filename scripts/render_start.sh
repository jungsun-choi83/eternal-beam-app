#!/usr/bin/env bash
# Render Web Service 시작 — repo 루트에서 PYTHONPATH 고정, python3 사용
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
