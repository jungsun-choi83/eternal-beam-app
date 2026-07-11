#!/usr/bin/env python3
"""
Eternal Beam — Raspberry Pi 5 통합 센서 브리지 (2디스플레이)

  Pi 터치스크린  → 배경 mp4 만 (pi_display_bg.py, UDP :9999)
  기계 안 폰(S23) → 피사체(강아지) 만 (Unity APK, UDP :5005)

이중 UDP 라우팅:
  NFC nfc_tagged          → Pi 디스플레이 :9999  (배경 전환, Unity 로 안 감)
  touch / approach / voice → S21/S23 Unity :5005  (idle / action)

  {"event":"nfc_tagged","theme_id":"forest","uid":"..."}  → Pi 터치스크린
  {"event":"touch","distance_mm":85}                      → Unity (피사체)
  {"event":"voice",...}                                   → Unity (피사체)

사용:
  UDP_HOST=192.168.219.187 python eternal_beam_pi.py   # 폰 Wi-Fi IP
  python eternal_beam_pi.py --simulate

  # Pi 배경 플레이어는 별도 터미널(또는 systemd):
  python pi_display_bg.py --videos-dir ./backgrounds

환경변수:
  UDP_HOST / UDP_PORT           — Unity 폰 (기본 5005)
  BG_DISPLAY_HOST / BG_DISPLAY_PORT — Pi 배경 (기본 127.0.0.1:9999)
  NFC_THEME_MAP, NFC_FALLBACK_THEME (기본 forest)
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
BG_DISPLAY_HOST = os.getenv("BG_DISPLAY_HOST", "127.0.0.1")
BG_DISPLAY_PORT = int(os.getenv("BG_DISPLAY_PORT", "9999"))
NFC_THEME_MAP_PATH = Path(os.getenv("NFC_THEME_MAP", str(BASE_DIR / "nfc_theme_map.json")))
NFC_FALLBACK_THEME = os.getenv("NFC_FALLBACK_THEME", "forest").strip()

NFC_POLL_SEC = 0.15
NFC_DEBOUNCE_SEC = 1.5


def make_sender(
    unity_host: str,
    unity_port: int,
    bg_host: str = BG_DISPLAY_HOST,
    bg_port: int = BG_DISPLAY_PORT,
):
    """NFC → Pi 배경(:9999), 나머지 센서 → Unity 폰(:5005)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock = threading.Lock()

    def send(payload: dict[str, Any]) -> None:
        event = str(payload.get("event", "")).strip().lower()
        msg = json.dumps(payload, separators=(",", ":"))

        if event == "nfc_tagged":
            target = (bg_host, bg_port)
            label = f"Pi 터치스크린(배경) {bg_host}:{bg_port}"
        else:
            target = (unity_host, unity_port)
            label = f"Unity 폰(피사체) {unity_host}:{unity_port}"

        with lock:
            sock.sendto(msg.encode("utf-8"), target)
        print(f"[UDP -> {label}] {msg}", flush=True)

        try:
            from pi_sse_server import broadcast_event  # type: ignore

            broadcast_event(payload)
        except Exception:
            pass

    return send


def load_theme_map() -> dict[str, str]:
    if not NFC_THEME_MAP_PATH.exists():
        print(f"[NFC] theme map 없음: {NFC_THEME_MAP_PATH}", flush=True)
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


def resolve_nfc_theme(uid_hex: str, theme_map: dict[str, str]) -> str | None:
    theme = theme_map.get(uid_hex)
    if theme:
        return theme
    if NFC_FALLBACK_THEME:
        print(
            f"[NFC] 미등록 UID={uid_hex} → fallback theme={NFC_FALLBACK_THEME!r}",
            flush=True,
        )
        return NFC_FALLBACK_THEME
    print(f"[NFC] 미등록 UID={uid_hex} — nfc_theme_map.json 또는 NFC_FALLBACK_THEME", flush=True)
    return None


def run_distance(send, *, simulate: bool) -> None:
    if simulate:
        while True:
            time.sleep(8)
            send({"event": "approach", "distance_mm": 220})
        return

    try:
        from pi_sensors_to_unity_udp import _init_vl53l0x, _distance_loop  # type: ignore

        sensor = _init_vl53l0x()
        print("[VL53L0X] 시작 — 12cm 이내=touch, 30cm 이내=approach → S23", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[VL53L0X] 비활성화 (init 실패): {e}", flush=True)
        return
    _distance_loop(send, sensor)


def run_nfc(send, theme_map: dict[str, str], *, simulate: bool) -> None:
    """NFC → Pi 터치스크린 배경만 (Unity 로 보내지 않음)."""
    if simulate:
        demo = list(theme_map.items()) or [("A1B2C3D4", NFC_FALLBACK_THEME or "forest")]
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

            theme = resolve_nfc_theme(uid_hex, theme_map)
            if not theme:
                time.sleep(NFC_POLL_SEC)
                continue

            send({"event": "nfc_tagged", "theme_id": theme, "uid": uid_hex})
            last_uid = uid_hex
            last_sent = now
        except Exception as e:  # noqa: BLE001
            print(f"[PN532] {e}", flush=True)
        time.sleep(NFC_POLL_SEC)


def run_voice(send, *, simulate: bool) -> None:
    try:
        from voice_to_unity import run_voice_loop  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[INMP441] 모듈 로드 실패: {e}", flush=True)
        print("[INMP441] Pi에 voice_to_unity.py 있는지 확인하세요.", flush=True)
        return
    try:
        print("[INMP441] 시작 — 말하면 S23 Unity로 voice 이벤트 전송", flush=True)
        run_voice_loop(send, simulate=simulate)
    except Exception as e:  # noqa: BLE001
        print(f"[INMP441] 비활성화: {e}", flush=True)
        print(
            "[INMP441] 힌트: python3 voice_to_unity.py --list-devices  "
            "VOICE_DEVICE_INDEX=1 bash start_machine_sensors.sh bridge",
            flush=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Eternal Beam Pi 센서 (2디스플레이)")
    ap.add_argument("--host", default=UDP_HOST, help="Unity 폰 Wi-Fi IP (피사체만)")
    ap.add_argument("--port", type=int, default=UDP_PORT, help="Unity UDP (기본 5005)")
    ap.add_argument("--bg-host", default=BG_DISPLAY_HOST, help="Pi 배경 수신 IP (기본 127.0.0.1)")
    ap.add_argument("--bg-port", type=int, default=BG_DISPLAY_PORT, help="Pi 배경 UDP (기본 9999)")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--no-tof", action="store_true")
    ap.add_argument("--no-nfc", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument(
        "--sse-port",
        type=int,
        default=int(os.getenv("PI_SSE_PORT", "8787")),
        help="폰 웹앱 SSE 포트 (0=끔, 기본 8787)",
    )
    args = ap.parse_args()

    if args.sse_port > 0:
        try:
            from pi_sse_server import start_sse_server  # type: ignore

            start_sse_server(port=args.sse_port)
        except Exception as e:  # noqa: BLE001
            print(f"[SSE] 시작 실패: {e}", flush=True)

    send = make_sender(args.host, args.port, args.bg_host, args.bg_port)
    theme_map = load_theme_map()

    print(
        f"2디스플레이 브리지\n"
        f"  NFC 배경  → udp://{args.bg_host}:{args.bg_port} (pi_display_bg.py)\n"
        f"  피사체    → udp://{args.host}:{args.port} (Unity APK)\n"
        f"  폰 웹 SSE → http://0.0.0.0:{args.sse_port}/events (sse_port=0 이면 끔)\n"
        f"  simulate={args.simulate}",
        flush=True,
    )

    threads: list[threading.Thread] = []
    if not args.no_tof:
        threads.append(
            threading.Thread(target=run_distance, args=(send,), kwargs={"simulate": args.simulate}, daemon=True)
        )
    if not args.no_nfc:
        threads.append(
            threading.Thread(target=run_nfc, args=(send, theme_map), kwargs={"simulate": args.simulate}, daemon=True)
        )
    if not args.no_voice:
        threads.append(
            threading.Thread(target=run_voice, args=(send,), kwargs={"simulate": args.simulate}, daemon=True)
        )

    if not threads:
        print("활성화된 센서가 없습니다.", flush=True)
        return

    for i, t in enumerate(threads):
        t.start()
        if i < len(threads) - 1:
            time.sleep(0.9)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n종료", flush=True)


if __name__ == "__main__":
    main()
