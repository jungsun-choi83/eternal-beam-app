#!/usr/bin/env bash
# S23 PetVFX 액션 수동 트리거 (센서 없이)
#   bash send_s23_action.sh 172.30.1.54 approach
#   bash send_s23_action.sh 172.30.1.54 voice
set -euo pipefail

S23_IP="${1:-172.30.1.54}"
ACTION="${2:-approach}"
PORT="${UDP_PORT:-5005}"

case "$ACTION" in
  approach|touch|run|action)
    PAYLOAD='{"event":"approach","distance_mm":85,"action_id":"RUN","mock":true}'
    ;;
  voice|speak)
    PAYLOAD='{"event":"voice","source":"manual"}'
    ;;
  *)
    echo "사용법: bash send_s23_action.sh <S23_IP> [approach|voice]" >&2
    exit 1
    ;;
esac

python3 - <<PY
import json, socket
ip, port = "$S23_IP", int("$PORT")
payload = json.loads("""$PAYLOAD""")
msg = json.dumps(payload, separators=(",", ":")).encode()
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(msg, (ip, port))
print(f"[UDP → {ip}:{port}] {msg.decode()}")
PY

echo "PetVFX 앱이 켜져 있어야 반응합니다."
