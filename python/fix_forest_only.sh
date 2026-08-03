#!/usr/bin/env bash
# bg02 / idle 등 제거 → NFC 시 fresh_forest.mp4 만
#   bash fix_forest_only.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "[*] 다른 배경 재생 프로세스 종료…"
pkill -9 -f raspi_nfc_playback.py 2>/dev/null || true
pkill -9 -f raspi_player.py 2>/dev/null || true
pkill -9 -f film_display_simple.py 2>/dev/null || true
pkill -9 -f hardware_playback.py 2>/dev/null || true
pkill -9 -f eternal_beam_mvp.py 2>/dev/null || true
pkill -9 mpv 2>/dev/null || true
sudo fuser -k 9999/udp 2>/dev/null || true

echo "[*] bg02 / 잘못된 배경 파일 삭제…"
for f in \
  ./BG_02.mp4 ./bg02.mp4 ./BG02.mp4 \
  ./backgrounds/BG_02.mp4 ./backgrounds/bg02.mp4 \
  ./backgrounds/idle.mp4 \
  ../BG_02.mp4 ../forest.mp4; do
  if [[ -f "$f" ]]; then
    rm -f "$f"
    echo "  삭제: $f"
  fi
done

if [[ ! -f "./backgrounds/fresh_forest.mp4" ]]; then
  echo "[!] backgrounds/fresh_forest.mp4 없음 — PC에서 복사 필요"
  exit 1
fi

echo "[*] bg_theme_map.json → 숲만"
cat >./bg_theme_map.json <<'EOF'
{
  "_comment": "촬영 — 전 테마 숲 배경",
  "_default": "fresh_forest.mp4",
  "forest": "fresh_forest.mp4",
  "fresh_forest": "fresh_forest.mp4",
  "goya_bg": "fresh_forest.mp4",
  "snow_forest": "fresh_forest.mp4",
  "bg02": "fresh_forest.mp4",
  "BG_02": "fresh_forest.mp4"
}
EOF

echo "[*] nfc_theme_map — 카드 UID 숲"
if [[ -f nfc_theme_map.json ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
p = Path("nfc_theme_map.json")
raw = json.loads(p.read_text(encoding="utf-8"))
raw["04E9113DC82A81"] = "forest"
p.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("  OK nfc_theme_map.json")
PY
fi

echo ""
echo "[OK] bg02 제거·숲만 설정 완료"
echo "다시 실행:"
echo "  터미널1: export DISPLAY=:0; python3 pi_display_bg.py --videos-dir ./backgrounds --wait-nfc"
echo "  터미널2: python3 -u eternal_beam_pi.py --host 172.30.1.54 --no-tof"
