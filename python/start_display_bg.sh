#!/usr/bin/env bash
# Pi 터치스크린에 배경 띄우기 (SSH에서도 DISPLAY=:0 적용)
set -euo pipefail
cd "$(dirname "$0")"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"

if [[ ! -f "./backgrounds/fresh_forest.mp4" ]]; then
  echo "[!] backgrounds/fresh_forest.mp4 없음 — forest.mp4 를 복사하세요." >&2
  exit 1
fi

sudo fuser -k 9999/udp 2>/dev/null || true
exec python3 pi_display_bg.py --videos-dir ./backgrounds "$@"
