#!/usr/bin/env bash
# 터치 + 음성만 (NFC/배경 없음) — 촬영용
#   bash start_touch_voice.sh                  # S23 IP 물어봄
#   bash start_touch_voice.sh 172.30.1.54      # 폰 Wi-Fi IP
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-${S23_IP:-${UDP_HOST:-}}}"
if [[ -z "$S23_IP" ]]; then
  echo "S23(폰) Wi-Fi IP 주소를 입력하세요 (폰 설정 → Wi-Fi → IP 주소):"
  read -r S23_IP
fi
if [[ -z "$S23_IP" ]]; then
  echo "사용법: bash start_touch_voice.sh <S23_WiFi_IP>" >&2
  exit 1
fi

echo "[*] 충돌 프로세스 종료 (I2C/마이크 해제)…"
pkill -f eternal_beam_pi.py 2>/dev/null || true
pkill -f s23_bridge_simple.py 2>/dev/null || true
pkill -f voice_to_unity.py 2>/dev/null || true
pkill -f pi_display_bg.py 2>/dev/null || true
pkill -f film_display_simple.py 2>/dev/null || true
pkill -9 mpv 2>/dev/null || true
sleep 2

if [[ -f fix_voicehat_alsa.sh ]]; then
  bash fix_voicehat_alsa.sh || echo "[!] ALSA 설정 경고 — 계속 진행"
fi

echo ""
echo "[*] 센서 점검…"
python3 sensor_check.py || true
echo ""

export NO_NFC=1
export NO_TOF="${NO_TOF:-0}"
export UDP_HOST="$S23_IP"
export UDP_PORT="${UDP_PORT:-5005}"
export TOUCH_MIN_MM="${TOUCH_MIN_MM:-28}"
export TOUCH_MAX_MM="${TOUCH_MAX_MM:-120}"
export VOICE_USE_ARECORD="${VOICE_USE_ARECORD:-1}"
export VOICE_ALSA_CARD="${VOICE_ALSA_CARD:-2}"
export VOICE_DEVICE_INDEX="${VOICE_DEVICE_INDEX:--1}"
export VOICE_CHANNELS="${VOICE_CHANNELS:-1}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-400}"
export VOICE_COOLDOWN_SEC="${VOICE_COOLDOWN_SEC:-4}"
export DEBUG_DISTANCE="${DEBUG_DISTANCE:-1}"
export PI_SSE_PORT="${PI_SSE_PORT:-0}"

echo "============================================"
echo " 터치+음성 → S23 $S23_IP:$UDP_PORT"
echo " PetVFX 앱 켜 둔 상태에서:"
echo "  손 ${TOUCH_MIN_MM}~${TOUCH_MAX_MM}mm 가까이 = approach"
echo "  말하기 (rms>=$VOICE_RMS_THRESHOLD) = voice"
echo "============================================"
echo ""

exec python3 -u s23_bridge_simple.py "$S23_IP"
