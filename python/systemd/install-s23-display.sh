#!/usr/bin/env bash
# Pi 2디스플레이: 터치스크린 배경 + S23 센서 브리지
#   sudo bash systemd/install-s23-display.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_SRC="${SCRIPT_DIR}/s23-bridge.env"
ENV_EXAMPLE="${SCRIPT_DIR}/s23-bridge.env.example"

if [[ "${EUID}" -ne 0 ]]; then
  echo "sudo 로 실행하세요:  sudo bash systemd/install-s23-display.sh" >&2
  exit 1
fi

install_service() {
  local name="$1"
  sed "s#__APP_DIR__#${APP_DIR}#g" "${SCRIPT_DIR}/${name}" > "/etc/systemd/system/${name}"
  echo "[+] /etc/systemd/system/${name}"
}

if [[ ! -f "${ENV_SRC}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_SRC}"
  echo "[!] ${ENV_SRC} 생성 — S23_IP 확인하세요."
fi

if [[ ! -f "${APP_DIR}/python/backgrounds/fresh_forest.mp4" ]]; then
  echo "[!] ${APP_DIR}/python/backgrounds/fresh_forest.mp4 없음" >&2
  echo "    bash install_forest_background.sh 실행 후 재시도" >&2
fi

install_service "pi-display-bg.service"
install_service "s23-bridge.service"

systemctl daemon-reload
systemctl enable pi-display-bg.service s23-bridge.service
systemctl restart pi-display-bg.service
systemctl restart s23-bridge.service

echo
echo "2디스플레이 시작됨:"
echo "  journalctl -u pi-display-bg.service -f   # Pi 숲 배경"
echo "  journalctl -u s23-bridge.service -f      # NFC·터치·음성"
echo "  bash send_nfc_test.sh                    # 배경만 테스트"
