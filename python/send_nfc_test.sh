#!/usr/bin/env bash
# UDP 테스트 (터미널 2에서 실행)
printf '%s' '{"event":"nfc_tagged","theme_id":"forest"}' | nc -u -w2 127.0.0.1 9999
echo ""
echo "[send_nfc_test] UDP 전송 완료 → 터미널1에 패킷 수신 로그가 떠야 합니다."
