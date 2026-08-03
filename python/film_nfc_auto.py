#!/usr/bin/env python3
"""
촬영용 — NFC 카드 대면 forest 재생 (터미널 명령 불필요)

  python3 film_nfc_auto.py

NFC(PN532) 실패 시: 같은 터미널에서 Enter 키 = forest (카메라 밖에서 누르기)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from hardware import load_hardware_config  # noqa: E402

_HW = load_hardware_config()

VIDEO = BASE / "backgrounds" / "fresh_forest.mp4"
NFC_DEBOUNCE_SEC = 1.5

MPV_EXTRA = os.getenv(
    "BG_MPV_EXTRA",
    "--panscan=0 --background=color --background-color=#142814",
).split()


def env() -> dict[str, str]:
    """DISPLAY/XAUTHORITY 기본값은 hardware_config.yaml 의 display.env (보드별 홈 디렉터리 다름)."""
    board_env = _HW.display_env
    e = os.environ.copy()
    e.setdefault("DISPLAY", board_env.get("DISPLAY", ":0"))
    e.setdefault("XAUTHORITY", board_env.get("XAUTHORITY", "/home/pi/.Xauthority"))
    return e


def stop_mpv() -> None:
    subprocess.run(["pkill", "mpv"], check=False)


def play_forest() -> None:
    if not VIDEO.exists():
        print(f"[!] 영상 없음: {VIDEO}", flush=True)
        return
    stop_mpv()
    time.sleep(0.15)
    cmd = [
        "mpv",
        "--fs",
        "--loop=inf",
        "--no-audio",
        "--no-terminal",
        *MPV_EXTRA,
        str(VIDEO),
    ]
    print(f"[*] forest 재생 시작", flush=True)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env())


def nfc_loop() -> None:
    try:
        from pi_sensors_to_unity_udp import _init_pn532  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[!] NFC 모듈 로드 실패: {e}", flush=True)
        return

    for attempt in range(3):
        try:
            print(f"[*] PN532 연결 시도 ({attempt + 1}/3)…", flush=True)
            pn532 = _init_pn532()
            print("[*] NFC 준비 완료 — 카드를 리더에 대세요", flush=True)
            break
        except Exception as e:  # noqa: BLE001
            print(f"[!] PN532 실패: {e}", flush=True)
            time.sleep(1.0)
    else:
        print("[!] NFC 자동 인식 불가 — Enter 키로 대체 (아래 안내)", flush=True)
        return

    last_uid: str | None = None
    last_sent = 0.0
    while True:
        try:
            uid = pn532.read_passive_target(timeout=0.1)
            if uid is None:
                last_uid = None
                time.sleep(0.1)
                continue
            uid_hex = uid.hex().upper()
            now = time.monotonic()
            if uid_hex == last_uid and (now - last_sent) < NFC_DEBOUNCE_SEC:
                time.sleep(0.1)
                continue
            print(f"[*] NFC 카드 감지 UID={uid_hex}", flush=True)
            play_forest()
            last_uid = uid_hex
            last_sent = now
        except Exception as e:  # noqa: BLE001
            print(f"[!] NFC 읽기 오류: {e}", flush=True)
            time.sleep(0.5)


def enter_key_loop() -> None:
    print("[*] 대체: 이 터미널에서 Enter 치면 forest (모델이 카드 대는 순간 누르기)", flush=True)
    while True:
        try:
            input()
            print("[*] Enter → forest", flush=True)
            play_forest()
        except EOFError:
            time.sleep(1)


def main() -> None:
    if not VIDEO.exists():
        print(f"[!] {VIDEO} 없음", file=sys.stderr)
        sys.exit(1)

    print("[*] 촬영 모드 — 시작 시 화면 검정, 신호 시 forest", flush=True)
    threading.Thread(target=nfc_loop, daemon=True).start()
    threading.Thread(target=enter_key_loop, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
