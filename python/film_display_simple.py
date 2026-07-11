#!/usr/bin/env python3
"""촬영용 — UDP :9999 받으면 mpv로 forest 재생 (단순 버전)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
VIDEO = BASE / "backgrounds" / "fresh_forest.mp4"
PORT = int(os.getenv("BG_DISPLAY_PORT", "9999"))

MPV_EXTRA = os.getenv(
    "BG_MPV_EXTRA",
    "--panscan=0 --background=color --background-color=#142814",
).split()


def env() -> dict[str, str]:
    e = os.environ.copy()
    e.setdefault("DISPLAY", ":0")
    e.setdefault("XAUTHORITY", "/home/pi/.Xauthority")
    return e


def stop_mpv() -> None:
    subprocess.run(["pkill", "mpv"], check=False)


def play_forest() -> None:
    if not VIDEO.exists():
        print(f"[!] 영상 없음: {VIDEO}", flush=True)
        return
    stop_mpv()
    cmd = [
        "mpv",
        "--fs",
        "--loop=inf",
        "--no-audio",
        "--no-terminal",
        *MPV_EXTRA,
        str(VIDEO),
    ]
    print(f"[*] 재생: {' '.join(cmd)}", flush=True)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env())
    print("[*] mpv 시작함", flush=True)


def main() -> None:
    if not VIDEO.exists():
        print(f"[!] {VIDEO} 없음", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))
    print(f"[*] UDP 대기 :{PORT} (신호 오면 forest 재생)", flush=True)
    print("[*] --wait-nfc 모드: 시작 시 화면 검정", flush=True)

    while True:
        data, addr = sock.recvfrom(4096)
        print(f"[*] 수신 {addr}: {data!r}", flush=True)
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "nfc_tagged":
            play_forest()


if __name__ == "__main__":
    main()
