#!/usr/bin/env bash
# 촬영용 — 카드 태그 타이밍에 이 스크립트 실행 (또는 Enter)
printf '%s' '{"event":"nfc_tagged","theme_id":"forest"}' | nc -u -w2 127.0.0.1 9999
echo "[film] forest 배경 트리거 전송"
