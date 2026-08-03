#!/usr/bin/env bash
# 터치 + 음성만 테스트 (NFC 없음)
#   bash test_touch_voice.sh 172.30.1.54
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-172.30.1.54}"

echo "[*] 마이크 장치 목록:"
python3 voice_to_unity.py --list-devices || true
echo ""

export NO_NFC=1
export UDP_HOST="$S23_IP"
export TOUCH_MIN_MM="${TOUCH_MIN_MM:-28}"
export TOUCH_MAX_MM="${TOUCH_MAX_MM:-120}"
export VOICE_USE_ARECORD="${VOICE_USE_ARECORD:-1}"
export VOICE_ALSA_CARD="${VOICE_ALSA_CARD:-2}"
export VOICE_DEVICE_INDEX="${VOICE_DEVICE_INDEX:--1}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-400}"
export DEBUG_DISTANCE="${DEBUG_DISTANCE:-1}"

pkill -f s23_bridge_simple.py 2>/dev/null || true
pkill -f eternal_beam_pi.py 2>/dev/null || true

echo "S23 IP: $S23_IP"
echo "PetVFX 켜 둔 상태에서 손 가까이 / 말하기"
echo ""

exec python3 -u s23_bridge_simple.py "$S23_IP"
