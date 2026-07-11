#!/usr/bin/env python3
"""
Eternal Beam — Pi 터치스크린 배경 전용 (mpv 루프)

NFC 신호는 eternal_beam_pi.py 가 UDP :9999 로 보냄:
  {"event":"nfc_tagged","theme_id":"forest","uid":"..."}

실행:
  python pi_display_bg.py
  python pi_display_bg.py --videos-dir ./backgrounds

환경변수:
  BG_DISPLAY_PORT (기본 9999)
  BG_VIDEOS_DIR   (기본 ./backgrounds)
  BG_THEME_MAP    (기본 bg_theme_map.json)
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

BG_DISPLAY_PORT = int(os.getenv("BG_DISPLAY_PORT", "9999"))
BG_VIDEOS_DIR = Path(os.getenv("BG_VIDEOS_DIR", str(BASE_DIR / "backgrounds")))
BG_THEME_MAP_PATH = Path(os.getenv("BG_THEME_MAP", str(BASE_DIR / "bg_theme_map.json")))

_player_lock = threading.Lock()
_player_proc: subprocess.Popen | None = None
_current_theme: str | None = None


def load_bg_map() -> dict[str, str]:
    if not BG_THEME_MAP_PATH.exists():
        print(f"[pi_display_bg] theme map 없음: {BG_THEME_MAP_PATH}", flush=True)
        return {"_default": "idle.mp4"}
    with open(BG_THEME_MAP_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): str(v) for k, v in raw.items() if not str(k).startswith("_") or k == "_default"}


def resolve_video(theme_id: str | None, bg_map: dict[str, str]) -> Path | None:
    key = (theme_id or "").strip() or "_default"
    rel = bg_map.get(key) or bg_map.get("_default")
    if not rel:
        print(f"[pi_display_bg] 매핑 없음: theme_id={theme_id!r}", flush=True)
        return None

    path = Path(rel)
    if not path.is_absolute():
        path = BG_VIDEOS_DIR / path

    if not path.exists():
        print(f"[pi_display_bg] 파일 없음: {path}", flush=True)
        return None

    return path


def _pick_player() -> list[str]:
    forced = os.getenv("BG_PLAYER", "").strip().lower()
    if forced == "mpv" and shutil.which("mpv"):
        return ["mpv"]
    if forced in ("omxplayer", "omx") and shutil.which("omxplayer"):
        return ["omxplayer"]
    if shutil.which("mpv"):
        return ["mpv"]
    if shutil.which("omxplayer"):
        return ["omxplayer"]
    raise RuntimeError("mpv 또는 omxplayer 가 PATH에 없습니다. apt install mpv")


def _build_cmd(player: list[str], video: Path) -> list[str]:
    if player[0] == "mpv":
        return ["mpv", "--fs", "--loop=inf", "--no-audio", "--really-quiet", str(video)]
    return ["omxplayer", "--loop", "--no-osd", str(video)]


def stop_player() -> None:
    global _player_proc
    with _player_lock:
        if _player_proc is None:
            return
        try:
            _player_proc.terminate()
            _player_proc.wait(timeout=3)
        except Exception:
            try:
                _player_proc.kill()
            except Exception:
                pass
        _player_proc = None


def play_background(theme_id: str | None, bg_map: dict[str, str]) -> None:
    global _player_proc, _current_theme

    video = resolve_video(theme_id, bg_map)
    if video is None:
        return

    tid = theme_id or "_default"
    if tid == _current_theme and _player_proc is not None and _player_proc.poll() is None:
        return

    cmd = _build_cmd(_pick_player(), video)

    with _player_lock:
        stop_player()
        print(f"[pi_display_bg] 재생 theme={tid!r} → {video}", flush=True)
        _player_proc = subprocess.Popen(cmd)
        _current_theme = tid


def handle_payload(payload: dict[str, Any], bg_map: dict[str, str]) -> None:
    event = str(payload.get("event", "")).strip().lower()
    if event != "nfc_tagged":
        return
    theme_id = str(payload.get("theme_id", "")).strip() or None
    play_background(theme_id, bg_map)


def run_listener(bind_host: str, bind_port: int, bg_map: dict[str, str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_host, bind_port))
    print(f"[pi_display_bg] UDP listen {bind_host}:{bind_port}", flush=True)

    play_background(None, bg_map)

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            raw = data.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            print(f"[pi_display_bg ← {addr[0]}:{addr[1]}] {raw}", flush=True)
            payload = json.loads(raw)
            if isinstance(payload, dict):
                handle_payload(payload, bg_map)
        except json.JSONDecodeError:
            print(f"[pi_display_bg] JSON 파싱 실패: {raw!r}", flush=True)
        except OSError as e:
            print(f"[pi_display_bg] socket error: {e}", flush=True)


def main() -> None:
    global BG_VIDEOS_DIR

    import argparse

    ap = argparse.ArgumentParser(description="Pi 터치스크린 배경 (UDP :9999)")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=BG_DISPLAY_PORT)
    ap.add_argument("--videos-dir", default=str(BG_VIDEOS_DIR))
    args = ap.parse_args()

    BG_VIDEOS_DIR = Path(args.videos_dir)
    BG_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    bg_map = load_bg_map()

    def _shutdown(*_args: object) -> None:
        stop_player()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        run_listener(args.bind, args.port, bg_map)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
