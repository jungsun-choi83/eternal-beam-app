#!/usr/bin/env bash
# Eternal Beam — Pi 배경(pi_display_bg) + 센서(eternal_beam_pi) 자동 시작
#   sudo bash systemd/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_SRC="${SCRIPT_DIR}/eternal-beam-pi.env"
ENV_EXAMPLE="${SCRIPT_DIR}/eternal-beam-pi.env.example"

if [[ "${EUID}" -ne 0 ]]; then
  echo "sudo 로 실행하세요:  sudo bash systemd/install.sh" >&2
  exit 1
fi

install_service() {
  local name="$1"
  sed "s#__APP_DIR__#${APP_DIR}#g" "${SCRIPT_DIR}/${name}" > "/etc/systemd/system/${name}"
  echo "[+] /etc/systemd/system/${name}"
}

if [[ ! -f "${ENV_SRC}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_SRC}"
  echo "[!] ${ENV_SRC} 생성 — UDP_HOST(폰 IP) 확인하세요."
fi

if [[ ! -f "${APP_DIR}/python/backgrounds/fresh_forest.mp4" ]]; then
  echo "[!] ${APP_DIR}/python/backgrounds/fresh_forest.mp4 없음 — forest.mp4 복사 필요" >&2
fi

install_service "pi-display-bg.service"
install_service "eternal-beam-pi.service"

systemctl daemon-reload
systemctl enable pi-display-bg.service eternal-beam-pi.service
systemctl restart pi-display-bg.service
systemctl restart eternal-beam-pi.service

echo
echo "2디스플레이 시작됨:"
echo "  journalctl -u pi-display-bg.service -f    # Pi 터치스크린 배경"
echo "  journalctl -u eternal-beam-pi.service -f  # NFC→배경, 거리/음성→Unity"
echo "  python3 nfc_scan_uid.py                   # 흰 카드 UID 등록"
