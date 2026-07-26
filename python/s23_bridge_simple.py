#!/usr/bin/env python3
"""
S23 Unity 브리지 (단일 파일) — 터치 + 음성
  python3 s23_bridge_simple.py 172.30.1.54

I2C/오디오 카드 등 보드별 값은 하드코딩하지 않고 hardware_config.yaml 을 통해
얻는다(공유 초기화 함수는 pi_sensors_to_unity_udp.py / voice_to_unity.py 에 있음).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from hardware import load_hardware_config  # noqa: E402

_HW = load_hardware_config()

UDP_PORT = int(os.getenv("UDP_PORT", str(_HW.get("network", "udp_port", default=5005))))
TOUCH_MIN_MM = int(os.getenv("TOUCH_MIN_MM", _HW.get("distance", "touch_min_mm", default=28)))
TOUCH_MAX_MM = int(os.getenv("TOUCH_MAX_MM", "120"))
TOUCH_COOLDOWN = float(os.getenv("TOUCH_COOLDOWN_SEC", "2"))
VOICE_DEVICE = int(os.getenv("VOICE_DEVICE_INDEX", "0"))
VOICE_RMS = float(os.getenv("VOICE_RMS_THRESHOLD", _HW.get("voice", "rms_threshold", default=500)))
VOICE_HOLD_MS = int(os.getenv("VOICE_HOLD_MS", _HW.get("voice", "hold_ms", default=350)))
VOICE_COOLDOWN = float(os.getenv("VOICE_COOLDOWN_SEC", "4"))
ACTION_RESET_SEC = float(os.getenv("ACTION_RESET_SEC", "10"))
ACTION_MOCK = os.getenv("ACTION_MOCK", "run").strip().lower()
VOICE_DELAY_SEC = float(os.getenv("VOICE_DELAY_SEC", "5"))
PI_SSE_PORT = int(os.getenv("PI_SSE_PORT", str(_HW.get("network", "sse_port", default=8787))))
NO_TOF = os.getenv("NO_TOF", "").strip().lower() in ("1", "true", "yes")
BG_DISPLAY_HOST = os.getenv("BG_DISPLAY_HOST", _HW.get("network", "bg_display_host", default="127.0.0.1"))
BG_DISPLAY_PORT = int(os.getenv("BG_DISPLAY_PORT", str(_HW.get("network", "bg_display_port", default=9999))))
NFC_FALLBACK_THEME = os.getenv("NFC_FALLBACK_THEME", "forest").strip()
NFC_DEBOUNCE_SEC = float(os.getenv("NFC_DEBOUNCE_SEC", _HW.get("nfc", "debounce_sec", default=1.5)))
NFC_POLL_SEC = float(os.getenv("NFC_POLL_SEC", _HW.get("nfc", "poll_sec", default=0.15)))
NFC_THEME_MAP_PATH = os.getenv(
    "NFC_THEME_MAP",
    os.path.join(os.path.dirname(__file__), "nfc_theme_map.json"),
)


def _pet_sensor_payload(payload: dict) -> dict:
    event = str(payload.get("event", "")).lower()
    if ACTION_MOCK == "off":
        return payload
    if event == "touch":
        payload = {**payload, "event": "approach"}
        event = "approach"
    if ACTION_MOCK == "run" and event == "approach":
        return {**payload, "event": "approach", "action_id": "RUN", "mock": True}
    return payload


    return 20 < mm < 2000 and mm != 8191


def _broadcast_sse(payload: dict) -> None:
    try:
        from pi_sse_server import broadcast_event

        broadcast_event(payload)
    except Exception:
        pass


def _load_nfc_theme_map() -> dict[str, str]:
    try:
        with open(NFC_THEME_MAP_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {
            str(k).upper(): str(v)
            for k, v in raw.items()
            if not str(k).startswith("_")
        }
    except Exception as e:
        print(f"[NFC] theme map 없음/오류: {e}", flush=True)
        return {}


def _resolve_nfc_theme(uid_hex: str, theme_map: dict[str, str]) -> str | None:
    theme = theme_map.get(uid_hex)
    if theme:
        return theme
    if NFC_FALLBACK_THEME:
        print(
            f"[NFC] 미등록 UID={uid_hex} → fallback={NFC_FALLBACK_THEME!r}",
            flush=True,
        )
        return NFC_FALLBACK_THEME
    return None


def main() -> None:
    host = (sys.argv[1].strip() if len(sys.argv) >= 2 else "").strip()
    if not host:
        host = os.getenv("S23_IP", os.getenv("UDP_HOST", "")).strip()
    if not host:
        print("사용법: python3 s23_bridge_simple.py <S23_IP>")
        print("  또는 환경변수 S23_IP=172.30.1.54")
        sys.exit(1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bg_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock = threading.Lock()
    reset_timer: threading.Timer | None = None

    def send_raw(payload: dict) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        with lock:
            sock.sendto(msg.encode("utf-8"), (host, UDP_PORT))
        print(f"[UDP -> {host}:{UDP_PORT}] {msg}", flush=True)
        _broadcast_sse(payload)

    def send_udp(payload: dict) -> None:
        nonlocal reset_timer
        body = _pet_sensor_payload(payload)
        send_raw(body)

        event = str(body.get("event", "")).lower()
        if event in ("touch", "approach", "voice", "action"):

            def _idle() -> None:
                send_raw({"event": "nfc_match", "source": "pi_reset"})
                send_raw({"event": "idle", "source": "pi_reset"})

            if reset_timer is not None:
                reset_timer.cancel()
            reset_timer = threading.Timer(ACTION_RESET_SEC, _idle)
            reset_timer.daemon = True
            reset_timer.start()

    def udp_route(payload: dict) -> None:
        event = str(payload.get("event", "")).lower()
        if event == "nfc_tagged":
            msg = json.dumps(payload, separators=(",", ":"))
            with lock:
                bg_sock.sendto(msg.encode("utf-8"), (BG_DISPLAY_HOST, BG_DISPLAY_PORT))
            print(
                f"[UDP -> Pi배경 {BG_DISPLAY_HOST}:{BG_DISPLAY_PORT}] {msg}",
                flush=True,
            )
            theme_id = str(payload.get("theme_id", "")).strip()
            send_raw(
                {
                    "event": "nfc_match",
                    "theme_id": theme_id,
                    "uid": payload.get("uid"),
                    "source": "pi_nfc",
                }
            )
            _broadcast_sse(payload)
            return
        send_udp(payload)

    def send_nfc_bg(payload: dict) -> None:
        """NFC → Pi 배경 + S23 nfc_match + 웹 SSE."""
        udp_route(payload)

    if PI_SSE_PORT > 0:
        try:
            from pi_sse_server import register_udp_forward, start_sse_server

            register_udp_forward(udp_route)
            start_sse_server(port=PI_SSE_PORT)
            print(
                f"[HTTP/SSE] 포레스트 데모 대기 http://0.0.0.0:{PI_SSE_PORT}/demo/forest",
                flush=True,
            )
        except Exception as e:
            print(f"[HTTP/SSE] 시작 실패: {e}", flush=True)

    def run_nfc(theme_map: dict[str, str]) -> None:
        if os.getenv("NO_NFC", "").strip().lower() in ("1", "true", "yes"):
            print("[NFC] 비활성 (NO_NFC=1)", flush=True)
            return
        try:
            from pi_sensors_to_unity_udp import _init_pn532

            pn532 = _init_pn532()
        except Exception as e:
            print(f"[NFC] 비활성 (init 실패): {e}", flush=True)
            return

        print("[NFC] 준비 — 카드 대면 Pi 숲 배경 + S23 웹앱 연동", flush=True)
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
                theme = _resolve_nfc_theme(uid_hex, theme_map)
                if not theme:
                    time.sleep(NFC_POLL_SEC)
                    continue
                print(f"[NFC] 카드 UID={uid_hex} theme={theme}", flush=True)
                send_nfc_bg(
                    {"event": "nfc_tagged", "theme_id": theme, "uid": uid_hex},
                )
                last_uid = uid_hex
                last_sent = now
            except Exception as e:
                print(f"[NFC] 읽기 오류: {e}", flush=True)
                time.sleep(0.5)

    print(f"S23 브리지 → {host}:{UDP_PORT}", flush=True)
    print(f"Pi 배경   → {BG_DISPLAY_HOST}:{BG_DISPLAY_PORT} (pi_display_bg 필요)", flush=True)
    print("PetVFX 앱 켜 둔 상태에서 테스트", flush=True)

    nfc_map = _load_nfc_theme_map()
    threading.Thread(target=run_nfc, args=(nfc_map,), daemon=True).start()

    def send_unity(payload: dict) -> None:
        """PetVFX APK는 approach/voice 만 수신 — touch → approach."""
        if str(payload.get("event", "")).lower() == "touch":
            payload = {**payload, "event": "approach"}
        send_udp(payload)

    sensor = None
    if not NO_TOF:
        try:
            from pi_sensors_to_unity_udp import _init_vl53l0x, _distance_loop

            sensor = _init_vl53l0x()
            print(
                f"[VL53L0X] OK touch {TOUCH_MIN_MM}~{TOUCH_MAX_MM}mm → approach",
                flush=True,
            )
            threading.Thread(
                target=_distance_loop,
                args=(send_unity, sensor),
                kwargs={"touch_min_mm": TOUCH_MIN_MM, "touch_max_mm": TOUCH_MAX_MM},
                daemon=True,
            ).start()
        except Exception as e:
            print(f"[VL53L0X] 비활성: {e}", flush=True)
            print(f"  → i2cdetect -y {_HW.i2c_bus} / NO_TOF=1 / sudo reboot", flush=True)
    else:
        print("[VL53L0X] 비활성 (NO_TOF=1)", flush=True)

    def run_voice() -> None:
        try:
            from voice_to_unity import run_voice_loop
        except Exception as e:
            print(f"[INMP441] import 실패: {e}", flush=True)
            return
        try:
            run_voice_loop(send_unity)
        except Exception as e:
            print(f"[INMP441] 비활성: {e}", flush=True)
            print("  → python3 voice_to_unity.py --list-devices", flush=True)
            print("  → VOICE_DEVICE_INDEX=1 VOICE_CHANNELS=1 로 재시도", flush=True)

    if sensor is not None:
        print(f"[INMP441] {VOICE_DELAY_SEC}s 후 시작 (I2C 안정화)", flush=True)
        time.sleep(VOICE_DELAY_SEC)
    threading.Thread(target=run_voice, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n종료", flush=True)


if __name__ == "__main__":
    main()
