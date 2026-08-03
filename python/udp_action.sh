#!/usr/bin/env bash
# S23 액션 — UDP만 (센서/터치 없음)
#   bash udp_action.sh           # 달려오기
#   bash udp_action.sh voice     # 음성 반응
#   bash udp_action.sh 172.30.1.54 approach
set -euo pipefail

S23_IP="${1:-172.30.1.54}"
ACTION="${2:-approach}"

if [[ "$1" == "voice" || "$1" == "approach" || "$1" == "touch" ]]; then
  ACTION="$1"
  S23_IP="${2:-172.30.1.54}"
fi

case "$ACTION" in
  approach|touch|run|action)
    PAYLOAD='{"event":"approach","distance_mm":85}'
    ;;
  voice)
    PAYLOAD='{"event":"voice","source":"manual"}'
    ;;
  *)
    echo "사용법: bash udp_action.sh [S23_IP] [approach|voice]" >&2
    exit 1
    ;;
esac

python3 -c "
import json, socket
ip, payload = '$S23_IP', json.loads('''$PAYLOAD''')
msg = json.dumps(payload, separators=(',', ':')).encode()
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(msg, (ip, 5005))
print(f'[UDP → {ip}:5005] {msg.decode()}')
"
