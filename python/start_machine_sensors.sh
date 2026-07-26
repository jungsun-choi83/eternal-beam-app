#!/usr/bin/env bash
# 기계 센서 → S23 Unity (거리 + 마이크 + NFC 배경)
#   터미널 1: bash start_machine_sensors.sh bg
#   터미널 2: bash start_machine_sensors.sh bridge
set -euo pipefail
cd "$(dirname "$0")"

REPO_RAW="https://raw.githubusercontent.com/jungsun-choi83/eternal-beam-app/main/python"
S23_IP="${UDP_HOST:-192.168.0.101}"

pull_latest() {
  for f in eternal_beam_pi.py pi_sensors_to_unity_udp.py voice_to_unity.py pi_sse_server.py \
    pi_display_bg.py bg_theme_map.json hardware_config.yaml \
    hardware/__init__.py hardware/config.py hardware/i2c_bus.py hardware/gpio.py; do
    mkdir -p "$(dirname "$f")"
    curl -fsSL -o "$f" "${REPO_RAW}/${f}" || true
  done
}

install_deps() {
  pip install -q -r requirements-pi.txt 2>/dev/null || pip install -q \
    adafruit-circuitpython-vl53l0x adafruit-circuitpython-pn532 smbus2 gpiod pyyaml pyaudio numpy
}

mode="${1:-bridge}"

case "$mode" in
  bg)
    pull_latest
    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-/home/pi/.Xauthority}"
    sudo fuser -k 9999/udp 2>/dev/null || true
    pkill -f pi_display_bg.py 2>/dev/null || true
    pkill -f film_display_simple.py 2>/dev/null || true
    echo "[bg] Pi 터치스크린 배경 (pi_display_bg.py) UDP :9999"
    exec python3 -u pi_display_bg.py --videos-dir ./backgrounds --wait-nfc
    ;;
  bridge)
    pull_latest
    install_deps
    pkill -f eternal_beam_pi.py 2>/dev/null || true
    export UDP_HOST="$S23_IP"
    export UDP_PORT="${UDP_PORT:-5005}"
    export BG_DISPLAY_HOST="${BG_DISPLAY_HOST:-127.0.0.1}"
    export BG_DISPLAY_PORT="${BG_DISPLAY_PORT:-9999}"
    export NFC_FALLBACK_THEME="${NFC_FALLBACK_THEME:-forest}"
    export ACTION_MOCK="${ACTION_MOCK:-run}"
    echo "[bridge] S23 Unity → udp://${UDP_HOST}:${UDP_PORT}"
    echo "[bridge] Pi 배경   → udp://${BG_DISPLAY_HOST}:${BG_DISPLAY_PORT}"
    echo "[bridge] ACTION_MOCK=${ACTION_MOCK} (approach=달려오기 목업)"
    echo "[bridge] 거리센서 + 마이크 + NFC (옵션 끄지 마세요)"
    exec python3 -u eternal_beam_pi.py --sse-port 0
    ;;
  test-voice)
    pull_latest
    install_deps
    echo "[test] INMP441 장치 목록:"
    python3 voice_to_unity.py --list-devices
    echo "[test] 10초 소리 테스트 (큰 소리 / 고야야) → voice UDP 전송"
    UDP_HOST="$S23_IP" python3 voice_to_unity.py --device "${VOICE_DEVICE_INDEX:-0}"
    ;;
  test-distance)
    pull_latest
    install_deps
    echo "[test] 거리센서 — 손을 5~12cm 에 대보세요 (Ctrl+C 종료)"
    UDP_HOST="$S23_IP" python3 -c "
from pi_sensors_to_unity_udp import _init_vl53l0x, _distance_loop, _udp_sender
import os
send = _udp_sender(os.environ['UDP_HOST'], int(os.environ.get('UDP_PORT', '5005')))
_distance_loop(send, _init_vl53l0x())
"
    ;;
  *)
    echo "사용법:"
    echo "  bash start_machine_sensors.sh bg       # 터미널1 배경"
    echo "  bash start_machine_sensors.sh bridge   # 터미널2 센서→S23"
    echo "  bash start_machine_sensors.sh test-voice"
    echo "  bash start_machine_sensors.sh test-distance"
    exit 1
    ;;
esac
