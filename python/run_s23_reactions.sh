#!/usr/bin/env bash
# Pi → S23 Unity (터치 + 소리 + NFC 배경)
# 사용: bash run_s23_reactions.sh S23_IP주소
# 예:   bash run_s23_reactions.sh 192.168.43.187
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-${UDP_HOST:-}}"
if [[ -z "$S23_IP" ]]; then
  echo "사용법: bash run_s23_reactions.sh <S23_WiFi_IP>"
  echo "예:     bash run_s23_reactions.sh 192.168.43.100"
  echo ""
  echo "S23 IP 확인: 설정 → Wi-Fi → 연결된 네트워크 → IP 주소"
  exit 1
fi

RAW="https://raw.githubusercontent.com/jungsun-choi83/eternal-beam-app/main/python"
for f in eternal_beam_pi.py voice_to_unity.py pi_sensors_to_unity_udp.py film_display_simple.py; do
  curl -fsSL -o "$f" "${RAW}/${f}" || true
done

echo "[*] 패키지 확인 (없으면 설치)..."
sudo apt-get install -y python3-pyaudio python3-numpy portaudio19-dev alsa-utils i2c-tools 2>/dev/null || true

export UDP_HOST="$S23_IP"
export UDP_PORT="${UDP_PORT:-5005}"
export BG_DISPLAY_HOST="${BG_DISPLAY_HOST:-127.0.0.1}"
export BG_DISPLAY_PORT="${BG_DISPLAY_PORT:-9999}"
export NFC_FALLBACK_THEME="${NFC_FALLBACK_THEME:-forest}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-800}"
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"

pkill -f film_nfc_auto.py 2>/dev/null || true
pkill -f eternal_beam_pi.py 2>/dev/null || true
sudo fuser -k 9999/udp 2>/dev/null || true

echo ""
echo "============================================"
echo " S23 IP: $S23_IP"
echo " 1) S23에 Pet.apk 실행 (회색 화면 대기)"
echo " 2) 손 5~12cm = touch / 말하기 = voice"
echo " 3) NFC 카드 = Pi 터치스크린 숲 배경"
echo "============================================"
echo ""
I2C_BUS="$(python3 -c 'from hardware import load_hardware_config; print(load_hardware_config().i2c_bus)' 2>/dev/null || echo 1)"
echo "[*] i2c 확인 (bus $I2C_BUS, 29=거리, 24=NFC):"
i2cdetect -y "$I2C_BUS" || true
echo ""

if [[ "${NO_BG:-0}" == "1" ]]; then
  echo "[*] 배경 디스플레이 없음 — film_display_simple.py 건너뜀"
  NFC_FLAG="--no-nfc"
else
  nohup python3 -u film_display_simple.py >> /tmp/eb-bg.log 2>&1 &
  sleep 1
  echo "[*] 배경 플레이어 시작 (로그: /tmp/eb-bg.log)"
  NFC_FLAG=""
fi

echo "[*] 센서 브리지 시작 (Ctrl+C 종료)"
exec python3 -u eternal_beam_pi.py --sse-port 0 --host "$S23_IP" $NFC_FLAG
