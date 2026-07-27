#!/usr/bin/env bash
# Pi에서 S23 IP 자동 갱신 (Wi-Fi 재연결 후)
#   bash update_s23_ip.sh
#   bash update_s23_ip.sh 192.168.0.109
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="./systemd/s23-bridge.env"
NEW_IP="${1:-}"

if [[ -z "$NEW_IP" ]]; then
  echo "[*] ARP 테이블에서 192.168.0.x / 192.168.43.x 후보:"
  ip neigh show 2>/dev/null | awk '/REACHABLE|STALE|DELAY/ {print $1}' | sort -u || true
  echo ""
  echo "S23 설정 → Wi-Fi → IP 주소 확인 후:"
  echo "  bash update_s23_ip.sh 172.30.1.54"
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp ./systemd/s23-bridge.env.example "$ENV_FILE"
fi

if grep -q '^S23_IP=' "$ENV_FILE"; then
  sed -i "s/^S23_IP=.*/S23_IP=${NEW_IP}/" "$ENV_FILE"
else
  echo "S23_IP=${NEW_IP}" >>"$ENV_FILE"
fi

echo "[+] ${ENV_FILE} → S23_IP=${NEW_IP}"
sudo systemctl restart s23-bridge.service 2>/dev/null || {
  echo "[*] systemd 없음 — 수동 실행:"
  echo "    bash start_pi_forest.sh ${NEW_IP}"
}
