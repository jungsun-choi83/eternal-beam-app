#!/usr/bin/env bash
# 마이크(/dev/snd) 점유 프로세스 정리
set -euo pipefail

echo "[*] Eternal Beam / 오디오 프로세스 종료…"
pkill -f voice_to_unity.py 2>/dev/null || true
pkill -f eternal_beam_pi.py 2>/dev/null || true
pkill -f s23_bridge_simple.py 2>/dev/null || true
pkill -f pi_display_bg.py 2>/dev/null || true
pkill -9 arecord 2>/dev/null || true
sleep 1

if command -v fuser >/dev/null 2>&1; then
  echo "[*] /dev/snd 점유 해제…"
  sudo fuser -k /dev/snd/* 2>/dev/null || true
  sleep 1
fi

# pulseaudio 가 잡는 경우 (있을 때만)
pkill -u pi pulseaudio 2>/dev/null || true
sleep 1

echo "[*] arecord 테스트…"
CARD="${VOICE_ALSA_CARD:-2}"
if arecord -D "plughw:${CARD},0" -f S16_LE -r 48000 -c 1 -d 1 /tmp/mic_ok.wav 2>/tmp/mic_ok.err; then
  echo "[OK] 마이크 사용 가능 (card $CARD)"
  rm -f /tmp/mic_ok.wav
else
  echo "[!] 아직 busy — sudo reboot 후 다시 시도 권장"
  cat /tmp/mic_ok.err 2>/dev/null || true
  exit 1
fi
