#!/usr/bin/env bash
# Pi → S23 Unity (터치 + 음성만, 배경 디스플레이 없음)
# 사용: bash run_s23_sensors_only.sh S23_IP주소
# 예:   bash run_s23_sensors_only.sh 192.168.0.101
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-${UDP_HOST:-}}"
if [[ -z "$S23_IP" ]]; then
  echo "사용법: bash run_s23_sensors_only.sh <S23_WiFi_IP>"
  echo "예:     bash run_s23_sensors_only.sh 192.168.0.101"
  echo ""
  echo "S23 IP 확인: 설정 → Wi-Fi → 연결된 네트워크 → IP 주소"
  exit 1
fi

echo "[*] 패키지 확인 (없으면 설치)..."
sudo apt-get install -y python3-pyaudio python3-numpy portaudio19-dev alsa-utils i2c-tools 2>/dev/null || true

export UDP_HOST="$S23_IP"
export UDP_PORT="${UDP_PORT:-5005}"
export VOICE_RMS_THRESHOLD="${VOICE_RMS_THRESHOLD:-800}"

pkill -f film_nfc_auto.py 2>/dev/null || true
pkill -f film_display_simple.py 2>/dev/null || true
pkill -f eternal_beam_pi.py 2>/dev/null || true

echo ""
echo "============================================"
echo " S23 전용 (배경 디스플레이 없음)"
echo " S23 IP: $S23_IP"
echo " 1) S23에 PetVFX 앱 실행 (idle 강아지)"
echo " 2) 손 5~12cm = touch → action"
echo " 3) 말하기 = voice → action"
echo "============================================"
echo ""
I2C_BUS="$(python3 -c 'from hardware import load_hardware_config; print(load_hardware_config().i2c_bus)' 2>/dev/null || echo 1)"
echo "[*] i2c 확인 (bus $I2C_BUS, 29=거리센서):"
i2cdetect -y "$I2C_BUS" || true
echo ""

echo "[*] 센서 브리지 시작 (Ctrl+C 종료)"
exec python3 -u eternal_beam_pi.py \
  --host "$S23_IP" \
  --no-nfc \
  --sse-port 0
