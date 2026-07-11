#!/usr/bin/env bash
# Pi 터치스크린 — 포레스트 배경 바로 띄우기 (한 번에 실행)
set -euo pipefail

for d in "$HOME/eternal-beam" "$HOME/eternal-beam-app"; do
  if [[ -d "$d/python" ]]; then
    REPO="$d"
    break
  fi
done

if [[ -z "${REPO:-}" ]]; then
  echo "[!] ~/eternal-beam 또는 ~/eternal-beam-app 을 찾을 수 없습니다." >&2
  exit 1
fi

echo "[*] repo=$REPO"
cd "$REPO"
git pull origin main || true
cd python

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"

FOREST="./backgrounds/fresh_forest.mp4"
if [[ ! -f "$FOREST" ]]; then
  echo "[!] $FOREST 없음 — git pull 후에도 없으면 PC에서 forest.mp4 복사 필요" >&2
  ls -la ./backgrounds/ 2>/dev/null || true
  exit 1
fi

echo "[*] mpv: $(command -v mpv || echo '없음 — sudo apt install mpv')"
sudo fuser -k 9999/udp 2>/dev/null || true
pkill -f "pi_display_bg.py" 2>/dev/null || true
sleep 0.5

echo "[*] 포레스트 배경 재생 (Ctrl+C 로 종료)"
exec python3 pi_display_bg.py --videos-dir ./backgrounds --test-forest
