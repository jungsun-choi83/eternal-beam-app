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
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from hardware import load_hardware_config  # noqa: E402

_HW = load_hardware_config()

BG_DISPLAY_PORT = int(os.getenv("BG_DISPLAY_PORT", str(_HW.get("network", "bg_display_port", default=9999))))
BG_VIDEOS_DIR = Path(os.getenv("BG_VIDEOS_DIR", str(BASE_DIR / "backgrounds")))
BG_THEME_MAP_PATH = Path(os.getenv("BG_THEME_MAP", str(BASE_DIR / "bg_theme_map.json")))

_player_lock = threading.Lock()
_player_proc: subprocess.Popen | None = None
_current_theme: str | None = None


def load_bg_map() -> dict[str, str]:
    if not BG_THEME_MAP_PATH.exists():
        print(f"[pi_display_bg] theme map 없음: {BG_THEME_MAP_PATH}", flush=True)
        return {"_default": "fresh_forest.mp4"}
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


def _player_env() -> dict[str, str]:
    """SSH에서 실행해도 터치스크린에 mpv가 뜨도록 — DISPLAY/XAUTHORITY 기본값은
    hardware_config.yaml 의 display.env (보드별 홈 디렉터리가 다를 수 있음)."""
    board_env = _HW.display_env
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = os.getenv("BG_DISPLAY", board_env.get("DISPLAY", ":0"))
    if not env.get("WAYLAND_DISPLAY"):
        wayland = os.getenv("BG_WAYLAND_DISPLAY", "wayland-0").strip()
        if wayland:
            env["WAYLAND_DISPLAY"] = wayland
    xauth = Path(env.get("XAUTHORITY", board_env.get("XAUTHORITY", "/home/pi/.Xauthority")))
    if xauth.exists():
        env["XAUTHORITY"] = str(xauth)
    print(
        f"[pi_display_bg] player env DISPLAY={env.get('DISPLAY')!r} "
        f"WAYLAND={env.get('WAYLAND_DISPLAY', '(없음)')!r} "
        f"XAUTHORITY={env.get('XAUTHORITY', '(없음)')!r}",
        flush=True,
    )
    return env


def _build_cmd(player: list[str], video: Path) -> list[str]:
    extra = os.getenv("BG_MPV_EXTRA", "").strip().split()
    if player[0] == "mpv":
        # 기본 simple — 수동 테스트와 동일 (촬영에서 가장 잘 뜸)
        fill = os.getenv("BG_MPV_FILL", "simple").strip().lower()
        fill_args: list[str]
        if fill in ("stretch", "noaspect", "distort"):
            fill_args = ["--keepaspect=no"]
        elif fill == "panscan":
            fill_args = [
                "--panscan=1.0",
                "--keepaspect-window=no",
                "--video-align-y=1",
            ]
        elif fill == "wayland":
            fill_args = ["--gpu-context=wayland"]
        else:
            fill_args = ["--ontop"]

        return [
            "mpv",
            "--fs",
            "--loop=inf",
            "--no-audio",
            "--no-terminal",
            *fill_args,
            *extra,
            str(video),
        ]
    return ["omxplayer", "--loop", "--no-osd", str(video)]


def _log_player_exit(proc: subprocess.Popen) -> None:
    """mpv가 바로 죽었는지 확인 (DISPLAY 없을 때 흔함)."""
    import time

    time.sleep(0.8)
    code = proc.poll()
    if code is None:
        print(f"[pi_display_bg] 플레이어 실행 중 PID={proc.pid}", flush=True)
        return
    print(
        f"[pi_display_bg] 플레이어 즉시 종료 exit={code}",
        flush=True,
    )
    print(
        "[pi_display_bg] 힌트: Pi 터치스크린 앞에서 실행하거나 "
        "DISPLAY=:0 python3 pi_display_bg.py … 를 시도하세요.",
        flush=True,
    )


def stop_player() -> None:
    global _player_proc
    subprocess.run(["pkill", "-9", "mpv"], check=False)
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

    print(
        f"[pi_display_bg] play_background 요청 theme_id={theme_id!r} "
        f"(현재={_current_theme!r}, player={'실행중' if _player_proc and _player_proc.poll() is None else '없음'})",
        flush=True,
    )

    video = resolve_video(theme_id, bg_map)
    if video is None:
        print(f"[pi_display_bg] 재생 중단: 영상 경로를 찾지 못함 theme_id={theme_id!r}", flush=True)
        return

    tid = theme_id or "_default"
    if tid == _current_theme and _player_proc is not None and _player_proc.poll() is None:
        print(f"[pi_display_bg] 재생 스킵: 이미 같은 테마 재생 중 theme={tid!r}", flush=True)
        return

    try:
        player = _pick_player()
    except RuntimeError as e:
        print(f"[pi_display_bg] 재생 중단: {e}", flush=True)
        return

    cmd = _build_cmd(player, video)

    with _player_lock:
        stop_player()
        print(f"[pi_display_bg] 재생 시작 theme={tid!r} cmd={' '.join(cmd)}", flush=True)
        try:
            _player_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/mpv-bg.err", "a", encoding="utf-8"),
                env=_player_env(),
            )
        except Exception as e:
            print(f"[pi_display_bg] mpv/omxplayer 실행 실패: {e}", flush=True)
            _player_proc = None
            return
        _current_theme = tid
        _log_player_exit(_player_proc)


def handle_payload(payload: dict[str, Any], bg_map: dict[str, str]) -> None:
    print(f"[pi_display_bg] handle_payload: {payload!r}", flush=True)

    if not isinstance(payload, dict):
        print(f"[pi_display_bg] 무시: payload 가 dict 가 아님 type={type(payload).__name__}", flush=True)
        return

    event = str(payload.get("event", "")).strip().lower()
    if event != "nfc_tagged":
        print(
            f"[pi_display_bg] 무시: event={event!r} (nfc_tagged 만 처리, keys={list(payload.keys())})",
            flush=True,
        )
        return

    theme_id = str(payload.get("theme_id", "")).strip() or None
    if not theme_id:
        print(
            f"[pi_display_bg] 경고: theme_id 없음 — _default 배경 시도 (uid={payload.get('uid')!r})",
            flush=True,
        )
    else:
        print(f"[pi_display_bg] nfc_tagged 수신 theme_id={theme_id!r} uid={payload.get('uid')!r}", flush=True)

    play_background(theme_id, bg_map)


def run_listener(
    bind_host: str,
    bind_port: int,
    bg_map: dict[str, str],
    *,
    wait_nfc: bool = False,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, bind_port))
    except OSError as e:
        print(
            f"[pi_display_bg] bind 실패 {bind_host}:{bind_port} — "
            f"다른 프로세스가 포트를 쓰는지 확인: {e}",
            flush=True,
        )
        raise

    print(
        f"[pi_display_bg] UDP listen {bind_host}:{bind_port} "
        f"(pid={os.getpid()}, videos_dir={BG_VIDEOS_DIR}, map_keys={list(bg_map.keys())})",
        flush=True,
    )
    print(
        "[pi_display_bg] 테스트: echo '{\"event\":\"nfc_tagged\",\"theme_id\":\"forest\"}' "
        f"| nc -u -w1 {bind_host if bind_host != '0.0.0.0' else '127.0.0.1'} {bind_port}",
        flush=True,
    )

    if wait_nfc:
        print(
            "[pi_display_bg] --wait-nfc: 배경 대기 (화면 검정/바탕) — NFC 신호 시 forest 재생",
            flush=True,
        )
    else:
        print("[pi_display_bg] 부팅 기본 배경 재생 시도…", flush=True)
        play_background(None, bg_map)

    while True:
        try:
            print("[pi_display_bg] UDP 대기 중 (recvfrom)…", flush=True)
            data, addr = sock.recvfrom(4096)
            print(
                f"[pi_display_bg] 패킷 수신 from {addr[0]}:{addr[1]} "
                f"bytes={len(data)} raw={data!r}",
                flush=True,
            )

            raw = data.decode("utf-8", errors="replace").strip()
            if not raw:
                print("[pi_display_bg] 무시: 빈 payload (decode 후 길이 0)", flush=True)
                continue

            print(f"[pi_display_bg ← {addr[0]}:{addr[1]}] {raw}", flush=True)

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"[pi_display_bg] JSON 파싱 실패: {raw!r} err={e}", flush=True)
                continue

            print(f"[pi_display_bg] JSON 파싱 OK type={type(payload).__name__}", flush=True)

            if isinstance(payload, dict):
                handle_payload(payload, bg_map)
            else:
                print(
                    f"[pi_display_bg] 무시: 최상위 JSON 이 dict 가 아님 type={type(payload).__name__}",
                    flush=True,
                )
        except OSError as e:
            print(f"[pi_display_bg] socket error: {e}", flush=True)


def main() -> None:
    global BG_VIDEOS_DIR

    import argparse

    ap = argparse.ArgumentParser(description="Pi 터치스크린 배경 (UDP :9999)")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=BG_DISPLAY_PORT)
    ap.add_argument("--videos-dir", default=str(BG_VIDEOS_DIR))
    ap.add_argument(
        "--wait-nfc",
        action="store_true",
        help="촬영용: 시작 시 배경 없음, nfc_tagged 수신 시에만 재생",
    )
    ap.add_argument(
        "--test-forest",
        action="store_true",
        help="UDP 없이 forest 배경만 바로 재생 (터치스크린 테스트)",
    )
    args = ap.parse_args()

    BG_VIDEOS_DIR = Path(args.videos_dir)
    BG_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    bg_map = load_bg_map()
    print(f"[pi_display_bg] bg_theme_map={BG_THEME_MAP_PATH} entries={bg_map}", flush=True)
    print(
        f"[pi_display_bg] mpv={shutil.which('mpv')!r} omxplayer={shutil.which('omxplayer')!r}",
        flush=True,
    )

    def _shutdown(*_args: object) -> None:
        stop_player()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if args.test_forest:
        print("[pi_display_bg] --test-forest: forest 배경만 재생 후 종료", flush=True)
        play_background("forest", bg_map)
        import time

        time.sleep(3600)
        return

    try:
        run_listener(args.bind, args.port, bg_map, wait_nfc=args.wait_nfc)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
