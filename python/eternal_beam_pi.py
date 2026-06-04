#!/usr/bin/env python3
"""
Eternal Beam — Raspberry Pi 5 통합 센서 브리지 (production main)

문서 "이터널빔 임베디드" 기준 '커서 담당' 산출물.
ToF(VL53L0X) + NFC(PN532) + 마이크(INMP441) 3개 센서를 하나의 프로세스에서
동시에 감시하고, 같은 Wi-Fi의 Unity(기계 속 폰)로 UDP JSON 패킷을 쏜다.

전송 이벤트 (Unity UDPReceiver 계약):
  {"event":"approach","distance_mm":240}            # 사람 접근  → near
  {"event":"nfc_tagged","theme_id":"snow_forest","uid":"A1B2C3D4"}  # NFC → 배경 전환
  {"event":"voice","source":"inmp441","rms":1800}   # 음성     → action 영상

사용:
  # 폰(Unity) Wi-Fi IP로 전송, 3센서 전부
  UDP_HOST=192.168.0.25 python eternal_beam_pi.py

  # 특정 센서만 끄기 (하드웨어 일부만 연결된 경우)
  python eternal_beam_pi.py --host 192.168.0.25 --no-voice

  # PC에서 배선 없이 동작 확인 (전부 시뮬레이션)
  python eternal_beam_pi.py --simulate

설치(Pi):
  pip install -r requirements-pi.txt
자동 실행(systemd):
  sudo bash systemd/install.sh   # 전원 켜면 자동 시작

환경변수:
  UDP_HOST (기본 127.0.0.1)   UDP_PORT (기본 5005)
  NFC_THEME_MAP (기본 nfc_theme_map.json)
  voice_to_unity.py 의 VOICE_* 변수도 그대로 적용됨
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

UDP_HOST = os.getenv("UDP_HOST", "127.0.0.1")
UDP_PORT = int(os.getenv("UDP_PORT", "5005"))
NFC_THEME_MAP_PATH = Path(os.getenv("NFC_THEME_MAP", str(BASE_DIR / "nfc_theme_map.json")))

NFC_POLL_SEC = 0.15
NFC_DEBOUNCE_SEC = 1.5


def make_sender(host: str, port: int):
    """스레드 안전한 UDP 송신 함수를 반환한다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock = threading.Lock()

    def send(payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        with lock:
            sock.sendto(msg.encode("utf-8"), (host, port))
        print(f"[UDP -> {host}:{port}] {msg}", flush=True)

    return send


def load_theme_map() -> dict[str, str]:
    """NFC UID(hex, 대문자) → Unity theme_id 매핑. 주석 키(_)는 무시."""
    if not NFC_THEME_MAP_PATH.exists():
        print(f"[NFC] theme map 없음: {NFC_THEME_MAP_PATH} (uid만 전송)", flush=True)
        return {}
    try:
        with open(NFC_THEME_MAP_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {
            str(k).upper(): str(v)
            for k, v in raw.items()
            if not str(k).startswith("_")
        }
    except Exception as e:  # noqa: BLE001
        print(f"[NFC] theme map 읽기 실패: {e}", flush=True)
        return {}


# --------------------------- 센서 스레드 ---------------------------

def run_distance(send, *, simulate: bool) -> None:
    """ToF(VL53L0X) 접근 감지 → approach. simulate면 8초마다 가짜 접근."""
    if simulate:
        while True:
            time.sleep(8)
            send({"event": "approach", "distance_mm": 220})
        return

    try:
        from pi_sensors_to_unity_udp import (  # type: ignore
            _init_vl53l0x,
            _distance_loop,
        )

        sensor = _init_vl53l0x()
    except Exception as e:  # noqa: BLE001
        print(f"[VL53L0X] 비활성화 (init 실패): {e}", flush=True)
        return
    _distance_loop(send, sensor)


def run_nfc(send, theme_map: dict[str, str], *, simulate: bool) -> None:
    """NFC(PN532) 태깅 → theme_id 매핑 후 nfc_tagged 전송."""
    if simulate:
        demo = list(theme_map.items()) or [("A1B2C3D4", "snow_forest")]
        i = 0
        while True:
            time.sleep(12)
            uid, theme = demo[i % len(demo)]
            send({"event": "nfc_tagged", "theme_id": theme, "uid": uid})
            i += 1
        return

    try:
        from pi_sensors_to_unity_udp import _init_pn532  # type: ignore

        pn532 = _init_pn532()
    except Exception as e:  # noqa: BLE001
        print(f"[PN532] 비활성화 (init 실패): {e}", flush=True)
        return

    last_uid: str | None = None
    last_sent = 0.0
    while True:
        try:
            uid = pn532.read_passive_target(timeout=0.1)
            if uid is None:
                last_uid = None
                time.sleep(NFC_POLL_SEC)
                continue

            uid_hex = uid.hex().upper()
            now = time.monotonic()
            if uid_hex == last_uid and (now - last_sent) < NFC_DEBOUNCE_SEC:
                time.sleep(NFC_POLL_SEC)
                continue

            payload: dict[str, Any] = {"event": "nfc_tagged", "uid": uid_hex}
            theme = theme_map.get(uid_hex)
            if theme:
                payload["theme_id"] = theme
            else:
                print(f"[NFC] 미매핑 UID={uid_hex} (nfc_theme_map.json에 추가 필요)", flush=True)
            send(payload)
            last_uid = uid_hex
            last_sent = now
        except Exception as e:  # noqa: BLE001
            print(f"[PN532] {e}", flush=True)
        time.sleep(NFC_POLL_SEC)


def run_voice(send, *, simulate: bool) -> None:
    """INMP441 마이크 음성 감지 → voice."""
    try:
        from voice_to_unity import run_voice_loop  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[INMP441] 모듈 로드 실패: {e}", flush=True)
        return
    try:
        run_voice_loop(send, simulate=simulate)
    except Exception as e:  # noqa: BLE001
        print(f"[INMP441] 비활성화: {e}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Eternal Beam Pi 통합 센서 브리지")
    ap.add_argument("--host", default=UDP_HOST, help="Unity(폰) Wi-Fi IP")
    ap.add_argument("--port", type=int, default=UDP_PORT, help="Unity UDP 포트 (기본 5005)")
    ap.add_argument("--simulate", action="store_true", help="배선 없이 전체 시뮬레이션(PC 테스트)")
    ap.add_argument("--no-tof", action="store_true", help="거리 센서 끄기")
    ap.add_argument("--no-nfc", action="store_true", help="NFC 끄기")
    ap.add_argument("--no-voice", action="store_true", help="마이크 끄기")
    args = ap.parse_args()

    send = make_sender(args.host, args.port)
    theme_map = load_theme_map()

    print(
        f"Eternal Beam Pi bridge -> udp://{args.host}:{args.port} "
        f"(simulate={args.simulate})",
        flush=True,
    )

    threads: list[threading.Thread] = []
    if not args.no_tof:
        threads.append(threading.Thread(target=run_distance, args=(send,), kwargs={"simulate": args.simulate}, daemon=True))
    if not args.no_nfc:
        threads.append(threading.Thread(target=run_nfc, args=(send, theme_map), kwargs={"simulate": args.simulate}, daemon=True))
    if not args.no_voice:
        threads.append(threading.Thread(target=run_voice, args=(send,), kwargs={"simulate": args.simulate}, daemon=True))

    if not threads:
        print("활성화된 센서가 없습니다. --no-* 옵션을 확인하세요.", flush=True)
        return

    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n종료", flush=True)


if __name__ == "__main__":
    main()
