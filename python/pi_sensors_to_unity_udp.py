#!/usr/bin/env python3
"""
Raspberry Pi 5 — VL53L0X + PN532 → Unity UDP (default port 5005)

Galaxy S21 등 Android 디스플레이: Unity가 돌아가는 폰 Wi‑Fi IP로 보냄.
  python pi_sensors_to_unity_udp.py --host 192.168.0.25
  UDP_HOST=192.168.0.25 python pi_sensors_to_unity_udp.py

Sends compact JSON lines, e.g.:
  {"event":"approach","distance_mm":240}
  {"event":"nfc_tagged","uid":"A1B2C3D4"}

Unity UDPReceiver maps "approach" → near, plain "near" also works.

Deps (Pi):
  pip install adafruit-circuitpython-vl53l0x adafruit-circuitpython-pn532
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

# --- UDP target (Galaxy/Android = phone Wi‑Fi IP; PC dev = 127.0.0.1) ---
UDP_HOST = "127.0.0.1"
UDP_PORT = 5005

# --- VL53L0X ---
DISTANCE_THRESHOLD_MM = 300
DISTANCE_POLL_SEC = 0.05
APPROACH_COOLDOWN_SEC = 2.0  # debounce while user stays close

# --- PN532 ---
NFC_POLL_SEC = 0.15
NFC_DEBOUNCE_SEC = 1.5


def _udp_sender(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        sock.sendto(msg.encode("utf-8"), (host, port))
        print(f"[UDP → {host}:{port}] {msg}")

    return send


def _init_vl53l0x():
    import board
    import busio
    import adafruit_vl53l0x

    i2c = busio.I2C(board.SCL, board.SDA)
    return adafruit_vl53l0x.VL53L0X(i2c)


def _init_pn532():
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C

    i2c = busio.I2C(board.SCL, board.SDA)
    pn532 = PN532_I2C(i2c, debug=False)
    pn532.SAM_configuration()
    return pn532


def _distance_loop(send, sensor) -> None:
    last_sent = 0.0
    while True:
        try:
            mm = int(sensor.range)
            if 0 < mm < DISTANCE_THRESHOLD_MM and (time.monotonic() - last_sent) >= APPROACH_COOLDOWN_SEC:
                send({"event": "approach", "distance_mm": mm})
                last_sent = time.monotonic()
        except Exception as e:
            print(f"[VL53L0X] {e}")
        time.sleep(DISTANCE_POLL_SEC)


def _nfc_loop(send, pn532) -> None:
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

            send({"event": "nfc_tagged", "uid": uid_hex})
            last_uid = uid_hex
            last_sent = now
        except Exception as e:
            print(f"[PN532] {e}")
        time.sleep(NFC_POLL_SEC)


def main() -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="VL53L0X + PN532 → Unity UDP")
    ap.add_argument(
        "--host",
        default=os.getenv("UDP_HOST", UDP_HOST),
        help="Unity 수신 IP (Galaxy/Android: 폰 Wi‑Fi IP, 예: 192.168.0.25)",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("UDP_PORT", str(UDP_PORT))),
        help="Unity UDP 포트 (기본 5005)",
    )
    args = ap.parse_args()

    send = _udp_sender(args.host, args.port)
    print(f"Sensor bridge → udp://{args.host}:{args.port}")

    vl53 = _init_vl53l0x()
    pn532 = _init_pn532()

    threading.Thread(target=_distance_loop, args=(send, vl53), daemon=True).start()
    threading.Thread(target=_nfc_loop, args=(send, pn532), daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("종료")


if __name__ == "__main__":
    main()
