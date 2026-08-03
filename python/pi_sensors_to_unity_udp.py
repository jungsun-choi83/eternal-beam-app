#!/usr/bin/env python3
"""
VL53L0X + PN532 → Unity UDP (default port 5005)

보드 하드코딩(I2C 버스/주소) 없이 python/hardware_config.yaml + python/hardware
(Linux 표준 I2C — smbus2) 를 사용. RPi5든 RK3566이든 hardware_config.yaml 의
active_board / i2c.bus 값만 바꾸면 이 파일은 그대로 동작한다.

Galaxy S21 등 Android 디스플레이: Unity가 돌아가는 폰 Wi‑Fi IP로 보냄.
  python pi_sensors_to_unity_udp.py --host 192.168.0.25
  UDP_HOST=192.168.0.25 python pi_sensors_to_unity_udp.py

Sends compact JSON lines, e.g.:
  {"event":"approach","distance_mm":240}
  {"event":"nfc_tagged","uid":"A1B2C3D4"}

Unity UDPReceiver maps "approach" → near, plain "near" also works.

Deps:
  pip install -r requirements-pi.txt
  (adafruit-circuitpython-vl53l0x, adafruit-circuitpython-pn532, smbus2)
"""

from __future__ import annotations

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

from hardware import get_i2c_bus, load_hardware_config, open_line  # noqa: E402

_HW = load_hardware_config()

# --- UDP target (Galaxy/Android = phone Wi‑Fi IP; PC dev = 127.0.0.1) ---
UDP_HOST = _HW.get("network", "udp_host", default="127.0.0.1")
UDP_PORT = int(_HW.get("network", "udp_port", default=5005))

# --- VL53L0X ---
DISTANCE_THRESHOLD_MM = int(_HW.get("distance", "approach_threshold_mm", default=300))
# VL53L0X 최소 측정 ~30mm — 2cm(20mm)는 0으로 나와 터치 불가
TOUCH_MIN_MM = int(os.getenv("TOUCH_MIN_MM", _HW.get("distance", "touch_min_mm", default=28)))
TOUCH_MAX_MM = int(os.getenv("TOUCH_MAX_MM", _HW.get("distance", "touch_max_mm", default=40)))
DISTANCE_POLL_SEC = float(_HW.get("distance", "poll_sec", default=0.05))
APPROACH_COOLDOWN_SEC = float(_HW.get("distance", "approach_cooldown_sec", default=2.0))  # debounce while user stays close

# --- PN532 ---
NFC_POLL_SEC = float(_HW.get("nfc", "poll_sec", default=0.15))
NFC_DEBOUNCE_SEC = float(_HW.get("nfc", "debounce_sec", default=1.5))


def _udp_sender(host: str, port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, separators=(",", ":"))
        sock.sendto(msg.encode("utf-8"), (host, port))
        print(f"[UDP → {host}:{port}] {msg}")

    return send


def _get_i2c():
    """VL53L0X + PN532 가 같은 I2C 버스를 공유 — hardware_config.yaml 의 i2c.bus 사용.

    busio.I2C(Blinka, RPi 전용) 대신 smbus2 기반 LinuxI2CBus(hardware/i2c_bus.py)를
    쓰므로 어떤 Linux 보드(RK3566 포함)에서도 동일하게 동작한다.
    """
    return get_i2c_bus(_HW)


def _patch_busio_i2c() -> None:
    """Adafruit Blinka가 board.SCL/SDA(GPIO)로 I2C를 열지 않게 /dev/i2c-N만 쓴다."""
    linux_bus = _get_i2c()
    try:
        import busio
    except ImportError:
        return

    class _LinuxI2C:
        """busio.I2C 대체 — 생성자 호출 시 LinuxI2CBus 싱글톤 반환."""

        def __new__(cls, *_args, **_kwargs):
            return linux_bus

        @classmethod
        def __subclasshook__(cls, subclass):
            from hardware.i2c_bus import LinuxI2CBus

            return issubclass(subclass, LinuxI2CBus)

    busio.I2C = _LinuxI2C  # type: ignore[misc, assignment]


def _vl53_xshut_wake() -> None:
    """VL53L0X XSHUT: LOW → HIGH 후 2ms+ 대기. 미연결 시 XSHUT→3.3V 점퍼 필요."""
    env_pin = os.getenv("VL53_XSHUT_GPIO", "").strip()
    line = None
    if env_pin.isdigit():
        from hardware.config import GpioLineConfig

        line_cfg = GpioLineConfig(name="vl53_xshut", enabled=True, offset=int(env_pin), direction="out")
        try:
            from hardware.gpio import GpioLine

            line = GpioLine(_HW.gpio_chip, line_cfg)
        except Exception as e:  # noqa: BLE001
            print(f"[ToF] XSHUT GPIO{env_pin} open fail: {e}", flush=True)
    else:
        line = open_line("vl53_xshut", _HW)

    if line is None:
        print(
            "[ToF] XSHUT 미제어 — VL53L0X 모듈 XSHUT 핀을 3.3V에 점퍼로 연결하세요",
            flush=True,
        )
        return

    try:
        line.set_value(False)
        time.sleep(0.01)
        line.set_value(True)
        time.sleep(0.05)
        print("[ToF] XSHUT wake (LOW→HIGH)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ToF] XSHUT wake fail: {e}", flush=True)
    finally:
        try:
            line.close()
        except Exception:
            pass


def _vl53_soft_reset(bus_number: int, address: int) -> None:
    """VL53L0X soft reset (reg 0x0083 ← 0x00) — 부팅 후 ID/read 정상화."""
    try:
        import smbus2

        bus = smbus2.SMBus(bus_number)
        try:
            w = smbus2.i2c_msg.write(address, [0x00, 0x83, 0x00])
            bus.i2c_rdwr(w)
            time.sleep(0.05)
        finally:
            bus.close()
    except Exception as e:  # noqa: BLE001
        print(f"[ToF] soft reset skip: {e}", flush=True)


def _probe_vl53_model_id(bus_number: int, address: int = 0x29) -> int | None:
    """VL53L0X 모델 ID (0xEEAA) — smbus2 16-bit 레지스터 0x00C0."""
    try:
        import smbus2

        bus = smbus2.SMBus(bus_number)
        try:
            w = smbus2.i2c_msg.write(address, [0x00, 0xC0])
            r = smbus2.i2c_msg.read(address, 2)
            bus.i2c_rdwr(w, r)
            return int.from_bytes(bytes(r), "big")
        finally:
            bus.close()
    except Exception as e:  # noqa: BLE001
        print(f"[VL53L0X] probe 실패 bus={bus_number} addr={address:#x}: {e}", flush=True)
        return None


class _Vl53L1xRange:
    """adafruit VL53L1X — .distance → .range 호환."""

    def __init__(self, sensor: object) -> None:
        self._sensor = sensor

    @property
    def range(self) -> int:
        return int(getattr(self._sensor, "distance"))


class _PimoroniVl53Range:
    """pimoroni VL53L0X — get_distance() → .range."""

    def __init__(self, sensor: object) -> None:
        self._sensor = sensor

    @property
    def range(self) -> int:
        mm = int(self._sensor.get_distance())
        return mm if mm >= 0 else 8191


def _init_vl53_pimoroni(bus_num: int, addr: int) -> _PimoroniVl53Range | None:
    try:
        import VL53L0X  # pimoroni git package
    except ImportError:
        return None
    tof = VL53L0X.VL53L0X(i2c_bus=bus_num, i2c_address=addr)
    tof.open()
    tof.start_ranging(1)
    mm = tof.get_distance()
    if mm < 0:
        raise RuntimeError(f"pimoroni get_distance={mm}")
    print(f"[ToF] pimoroni VL53L0X OK (sample={mm}mm)", flush=True)
    return _PimoroniVl53Range(tof)


def _init_vl53l0x():
    from hardware.i2c_bus import reset_i2c_bus

    reset_i2c_bus()
    bus_num = int(os.getenv("HARDWARE_I2C_BUS", str(_HW.i2c_bus)))
    addr = _HW.vl53l0x_address

    _vl53_xshut_wake()
    _vl53_soft_reset(bus_num, addr)
    model_id = _probe_vl53_model_id(bus_num, addr)
    if model_id is not None:
        print(f"[ToF] probe bus={bus_num} addr={addr:#x} model_id={model_id:#06x}", flush=True)

    _patch_busio_i2c()
    i2c = _get_i2c()

    # --- VL53L0X ---
    try:
        import adafruit_vl53l0x

        sensor = adafruit_vl53l0x.VL53L0X(i2c, address=addr)
        _ = sensor.range
        print("[ToF] VL53L0X init OK", flush=True)
        return sensor
    except Exception as e0:  # noqa: BLE001
        print(f"[ToF] VL53L0X fail: {e0}", flush=True)

    # --- VL53L1X (같은 0x29 주소) ---
    try:
        import adafruit_vl53l1x

        l1 = adafruit_vl53l1x.VL53L1X(i2c, address=addr)
        l1.start_ranging()
        print("[ToF] VL53L1X init OK (L1X 모듈)", flush=True)
        return _Vl53L1xRange(l1)
    except ImportError:
        print(
            "[ToF] VL53L1X 드라이버 없음: pip install adafruit-circuitpython-vl53l1x",
            flush=True,
        )
    except Exception as e1:  # noqa: BLE001
        print(f"[ToF] VL53L1X fail: {e1}", flush=True)

    # --- pimoroni (Pi에서 adafruit 실패 시 — git VL53L0X 패키지) ---
    try:
        p = _init_vl53_pimoroni(bus_num, addr)
        if p is not None:
            return p
    except Exception as e2:  # noqa: BLE001
        print(f"[ToF] pimoroni fail: {e2}", flush=True)

    raise RuntimeError(
        "VL53L0X/L1X init failed — XSHUT·3.3V·모듈 silk(VL53L0/L1) 확인"
    )


def _init_pn532():
    _patch_busio_i2c()
    from adafruit_pn532.i2c import PN532_I2C

    last_err: Exception | None = None
    for attempt in range(8):
        try:
            if attempt:
                time.sleep(0.6 * attempt)
            try:
                pn532 = PN532_I2C(_get_i2c(), debug=False, address=_HW.pn532_address)
            except TypeError:
                # 일부 adafruit_pn532 버전은 address kwarg 를 받지 않음(고정 0x24).
                pn532 = PN532_I2C(_get_i2c(), debug=False)
            pn532.SAM_configuration()
            return pn532
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"PN532 init failed after retries: {last_err}")


def _distance_loop(send, sensor, *, touch_min_mm: int | None = None, touch_max_mm: int | None = None) -> None:
    t_min = touch_min_mm if touch_min_mm is not None else TOUCH_MIN_MM
    t_max = touch_max_mm if touch_max_mm is not None else TOUCH_MAX_MM
    last_touch = 0.0
    last_approach = 0.0
    last_debug = 0.0
    debug = os.getenv("DEBUG_DISTANCE", "").strip() in ("1", "true", "yes")
    while True:
        try:
            mm = int(sensor.range)
            now = time.monotonic()
            if debug and (now - last_debug) >= 0.5:
                print(f"[VL53L0X] distance={mm}mm (touch {t_min}-{t_max}mm)", flush=True)
                last_debug = now
            if t_min <= mm <= t_max and (now - last_touch) >= APPROACH_COOLDOWN_SEC:
                send({"event": "touch", "distance_mm": mm})
                last_touch = now
            elif (
                0 < mm < DISTANCE_THRESHOLD_MM
                and (now - last_approach) >= APPROACH_COOLDOWN_SEC
            ):
                send({"event": "approach", "distance_mm": mm})
                last_approach = now
        except Exception as e:
            print(f"[VL53L0X] {e}", flush=True)
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
