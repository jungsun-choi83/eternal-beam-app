#!/usr/bin/env bash
# 어제 됐는데 오늘 안 될 때 — Pi에서 한 번에 복구
#   bash restore_pi_today.sh              # S23 IP 자동(109)
#   bash restore_pi_today.sh 172.30.1.54
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-172.30.1.54}"

echo "============================================"
echo " Eternal Beam Pi 복구 (S23=$S23_IP)"
echo "============================================"

echo "[1/5] 충돌 프로세스 종료…"
pkill -f eternal_beam_pi.py 2>/dev/null || true
pkill -f s23_bridge_simple.py 2>/dev/null || true
pkill -f voice_to_unity.py 2>/dev/null || true
pkill -f pi_display_bg.py 2>/dev/null || true
pkill -f film_display_simple.py 2>/dev/null || true
pkill -9 mpv 2>/dev/null || true
sudo fuser -k 9999/udp 2>/dev/null || true
sleep 1

echo "[2/5] 필수 파일 확인…"
for f in voice_to_unity.py eternal_beam_pi.py pi_display_bg.py s23_bridge_simple.py; do
  if [[ ! -f "$f" ]]; then
    echo "[!] 없음: $f — PC에서 sync_pc_to_pi.ps1 실행 필요" >&2
    exit 1
  fi
done

echo "[3/5] 숲 배경만 + ALSA(마이크 card 2)…"
bash fix_forest_only.sh
bash fix_voicehat_alsa.sh

echo "[4/5] S23 IP 갱신…"
bash update_s23_ip.sh "$S23_IP"

mkdir -p systemd
ENV_FILE="./systemd/s23-bridge.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp ./systemd/s23-bridge.env.example "$ENV_FILE"
fi
for kv in \
  "S23_IP=$S23_IP" \
  "VOICE_USE_ARECORD=1" \
  "VOICE_ALSA_CARD=2" \
  "VOICE_DEVICE_INDEX=-1" \
  "VOICE_CHANNELS=1" \
  "VOICE_RMS_THRESHOLD=400" \
  "PI_SSE_PORT=8787"; do
  key="${kv%%=*}"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s/^${key}=.*/${kv}/" "$ENV_FILE"
  else
    echo "$kv" >>"$ENV_FILE"
  fi
done
echo "[+] $ENV_FILE"
grep -E '^(S23_IP|VOICE_|PI_SSE)' "$ENV_FILE" || true

echo "[5/5] NFC 카드 UID 확인 (선택)…"
python3 - <<'PY' 2>/dev/null || echo "  (NFC 스킵 — i2c/PN532 나중에 확인)"
import json
from pathlib import Path
p = Path("nfc_theme_map.json")
raw = json.loads(p.read_text(encoding="utf-8"))
if "04E9113DC82A81" in raw:
    print("  NFC UID 04E9113DC82A81 → forest OK")
PY

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"
export S23_IP UDP_HOST="$S23_IP"
export VOICE_USE_ARECORD=1 VOICE_ALSA_CARD=2 VOICE_DEVICE_INDEX=-1
export VOICE_RMS_THRESHOLD=400 PI_SSE_PORT=8787

echo ""
echo "============================================"
echo " 복구 완료 — 아래 두 터미널로 실행"
echo "============================================"
echo ""
echo "터미널1 (Pi 터치스크린 숲):"
echo "  cd ~/eternal-beam/python"
echo "  export DISPLAY=:0"
echo "  python3 -u pi_display_bg.py --videos-dir ./backgrounds --wait-nfc"
echo ""
echo "터미널2 (NFC + 터치 + 음성 → S23):"
echo "  cd ~/eternal-beam/python"
echo "  export VOICE_USE_ARECORD=1 VOICE_ALSA_CARD=2 UDP_HOST=$S23_IP"
echo "  python3 -u eternal_beam_pi.py --host $S23_IP"
echo ""
echo "또는 한 줄:"
echo "  bash start_pi_forest.sh $S23_IP"
echo ""
echo "S23 웹: https://device.eternalbeam.com/?demo=device&pi=\$(hostname -I | awk '{print \$1}')"
