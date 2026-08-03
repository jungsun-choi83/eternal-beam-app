#!/usr/bin/env bash
# Pi 센서 전부 켜기 → S23 Unity (터치 + 음성)
# 사용: bash bringup_sensors.sh 192.168.0.101
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-${UDP_HOST:-}}"
if [[ -z "$S23_IP" ]]; then
  echo "사용법: bash bringup_sensors.sh <S23_WiFi_IP>"
  exit 1
fi

echo "[*] 시스템 패키지 설치..."
sudo apt-get update -qq
sudo apt-get install -y python3-pip python3-pyaudio python3-numpy \
  portaudio19-dev alsa-utils i2c-tools gpiod libgpiod2 2>/dev/null || true

echo "[*] Python 센서 패키지 설치..."
pip3 install -q -r requirements-pi.txt 2>/dev/null || pip3 install -q \
  adafruit-circuitpython-vl53l0x adafruit-circuitpython-pn532 smbus2 gpiod pyyaml pyaudio numpy

echo "[*] I2C 활성화 확인 (Raspberry Pi 전용 — RK3566 등은 dtb/보드 설정으로 이미 켜져 있음)..."
if ! grep -q '^dtparam=i2c_arm=on' /boot/firmware/config.txt 2>/dev/null \
   && ! grep -q '^dtparam=i2c_arm=on' /boot/config.txt 2>/dev/null; then
  echo "  [!] Pi라면 I2C가 꺼져 있을 수 있습니다. raspi-config → Interface → I2C Enable"
fi
echo "[*] hardware_config.yaml active_board=$(python3 -c \"import yaml;print(yaml.safe_load(open('hardware_config.yaml'))['active_board'])\" 2>/dev/null || echo '?')"

pkill -f eternal_beam_pi.py 2>/dev/null || true
pkill -f s23_bridge_simple.py 2>/dev/null || true
pkill -f voice_to_unity.py 2>/dev/null || true
pkill -f film_display_simple.py 2>/dev/null || true
pkill -f film_nfc_auto.py 2>/dev/null || true
sleep 1

export UDP_HOST="$S23_IP"
export UDP_PORT="${UDP_PORT:-5005}"
export TOUCH_MIN_MM="${TOUCH_MIN_MM:-28}"
export TOUCH_MAX_MM="${TOUCH_MAX_MM:-120}"
export VOICE_USE_ARECORD="${VOICE_USE_ARECORD:-1}"
export VOICE_ALSA_CARD="${VOICE_ALSA_CARD:-2}"
export VOICE_DEVICE_INDEX="${VOICE_DEVICE_INDEX:--1}"
export VOICE_CHANNELS="${VOICE_CHANNELS:-1}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-400}"
export ACTION_RESET_SEC="${ACTION_RESET_SEC:-10}"
export DEBUG_DISTANCE="${DEBUG_DISTANCE:-1}"

echo ""
echo "============================================"
echo " S23 IP: $S23_IP"
echo " 터치 거리: ${TOUCH_MIN_MM}~${TOUCH_MAX_MM}mm"
echo " PetVFX 앱 켜 둔 상태에서:"
echo "  - 손 ${TOUCH_MIN_MM}~${TOUCH_MAX_MM}mm = touch → action"
echo "  - 말하기 = voice → action"
echo "  - 웹 포레스트 데모 → POST /demo/forest"
echo "============================================"
echo ""

exec python3 -u eternal_beam_pi.py \
  --host "$S23_IP" \
  --no-nfc \
  --sse-port 8787
