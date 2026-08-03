#!/usr/bin/env bash
# Pi 터치스크린 숲 배경 + S23 센서 브리지 (2디스플레이 촬영)
#   bash start_pi_forest.sh              # S23_IP from env / s23-bridge.env
#   bash start_pi_forest.sh 192.168.0.101
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-${S23_IP:-${UDP_HOST:-}}}"
ENV_FILE="${ENV_FILE:-./systemd/s23-bridge.env}"
if [[ -z "$S23_IP" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  S23_IP="${S23_IP:-${UDP_HOST:-}}"
fi
if [[ -z "$S23_IP" ]]; then
  echo "사용법: bash start_pi_forest.sh <S23_WiFi_IP>" >&2
  exit 1
fi

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"

if [[ ! -f "./backgrounds/fresh_forest.mp4" ]]; then
  echo "[!] backgrounds/fresh_forest.mp4 없음" >&2
  echo "    bash install_forest_background.sh 또는 PC에서 복사" >&2
  exit 1
fi

echo "[*] 기존 배경/브리지 정리…"
sudo fuser -k 9999/udp 2>/dev/null || true
pkill -f "pi_display_bg.py" 2>/dev/null || true
pkill -f "film_display_simple.py" 2>/dev/null || true
pkill -f "film_nfc_auto.py" 2>/dev/null || true

echo "[*] Pi 터치스크린 배경 대기 (NFC 시 forest) UDP :9999"
nohup python3 -u pi_display_bg.py --videos-dir ./backgrounds --wait-nfc \
  >>/tmp/pi-display-bg.log 2>&1 &
sleep 1

export S23_IP
export UDP_HOST="$S23_IP"
export PI_SSE_PORT="${PI_SSE_PORT:-8787}"
export NFC_FALLBACK_THEME="${NFC_FALLBACK_THEME:-forest}"
export VOICE_USE_ARECORD="${VOICE_USE_ARECORD:-1}"
export VOICE_ALSA_CARD="${VOICE_ALSA_CARD:-2}"
export VOICE_DEVICE_INDEX="${VOICE_DEVICE_INDEX:--1}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-400}"

echo ""
echo "============================================"
echo " Pi 디스플레이: NFC → 숲 배경 (mpv)"
echo " S23 Unity    : $S23_IP:5005 (터치·음성)"
echo " S23 웹앱 SSE : http://eternalbeam.local:8787/events"
echo "============================================"
echo "  journal: tail -f /tmp/pi-display-bg.log"
echo "  테스트 : bash send_nfc_test.sh"
echo ""

exec python3 -u s23_bridge_simple.py "$S23_IP"
