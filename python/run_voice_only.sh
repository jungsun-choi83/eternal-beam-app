#!/usr/bin/env bash
# Pi 마이크 → S23 PetVFX voice 액션 (센서/터치 없음)
#   bash run_voice_only.sh
#   bash run_voice_only.sh 172.30.1.54
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-172.30.1.54}"

echo "[*] 마이크 점유 프로세스 종료…"
if [[ -f ./free_mic.sh ]]; then
  bash ./free_mic.sh || {
    echo "[!] free_mic 실패 — 계속 시도"
    pkill -9 -f voice_to_unity.py 2>/dev/null || true
    pkill -9 -f eternal_beam_pi.py 2>/dev/null || true
    pkill -9 -f s23_bridge_simple.py 2>/dev/null || true
    pkill -9 arecord 2>/dev/null || true
    sudo fuser -k /dev/snd/* 2>/dev/null || true
    sleep 2
  }
else
  pkill -f voice_to_unity.py 2>/dev/null || true
  pkill -f eternal_beam_pi.py 2>/dev/null || true
  pkill -f s23_bridge_simple.py 2>/dev/null || true
  pkill -f arecord 2>/dev/null || true
  sudo fuser -k /dev/snd/* 2>/dev/null || true
  sleep 2
fi

if [[ -f fix_voicehat_alsa.sh ]]; then
  bash fix_voicehat_alsa.sh || true
fi

export UDP_HOST="$S23_IP"
export UDP_PORT="${UDP_PORT:-5005}"
export VOICE_USE_ARECORD=1
export VOICE_ALSA_CARD="${VOICE_ALSA_CARD:-2}"
export VOICE_DEVICE_INDEX=-1
export VOICE_CHANNELS="${VOICE_CHANNELS:-1}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-350}"
export VOICE_HOLD_MS="${VOICE_HOLD_MS:-300}"
export VOICE_COOLDOWN_SEC="${VOICE_COOLDOWN_SEC:-3}"
export VOICE_DEBUG_RMS="${VOICE_DEBUG_RMS:-1}"

echo ""
echo "============================================"
echo " 마이크 → S23 voice ($S23_IP:$UDP_PORT)"
echo " PetVFX 앱 켜 둔 상태에서 말하기"
echo " rms peak 로그로 소리 크기 확인"
echo " 너무 안 민감하면:"
echo "   VOICE_RMS_THRESHOLD=250 bash run_voice_only.sh $S23_IP"
echo "============================================"
echo ""

exec python3 -u voice_to_unity.py
