#!/usr/bin/env bash
# Eternal Beam — Pi 센서 브리지 systemd 자동 실행 설치
#   sudo bash systemd/install.sh
# 전원만 켜면(모니터 없이) eternal_beam_pi.py 가 자동 시작/복구된다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_NAME="eternal-beam-pi.service"
DEST="/etc/systemd/system/${SERVICE_NAME}"
ENV_SRC="${SCRIPT_DIR}/eternal-beam-pi.env"
ENV_EXAMPLE="${SCRIPT_DIR}/eternal-beam-pi.env.example"

if [[ "${EUID}" -ne 0 ]]; then
  echo "sudo 로 실행하세요:  sudo bash systemd/install.sh" >&2
  exit 1
fi

# 1) 환경설정 파일 준비
if [[ ! -f "${ENV_SRC}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_SRC}"
  echo "[!] ${ENV_SRC} 생성됨 — UDP_HOST(폰 IP)를 실제 값으로 수정하세요."
fi

# 2) 유닛 파일 경로 치환 후 설치
sed "s#__APP_DIR__#${APP_DIR}#g" "${SCRIPT_DIR}/${SERVICE_NAME}" > "${DEST}"
echo "[+] ${DEST} 설치됨 (APP_DIR=${APP_DIR})"

# 3) 활성화
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "완료. 상태/로그 확인:"
echo "  systemctl status ${SERVICE_NAME}"
echo "  journalctl -u ${SERVICE_NAME} -f"
