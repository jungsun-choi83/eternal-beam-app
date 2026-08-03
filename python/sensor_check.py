#!/usr/bin/env python3
"""센서 점검 — I2C / 거리 / 마이크 장치 확인 (hardware_config.yaml 의 보드 설정 사용)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from hardware import load_hardware_config  # noqa: E402


def main() -> int:
    hw = load_hardware_config()
    bus = hw.i2c_bus
    print(f"=== Eternal Beam 센서 점검 (board={hw.board}, i2c bus={bus}) ===\n")

    print(f"[1] i2cdetect -y {bus}")
    try:
        subprocess.run(["i2cdetect", "-y", str(bus)], check=False)
    except FileNotFoundError:
        print("  i2c-tools 없음: sudo apt install -y i2c-tools")
    print(
        f"  기대: {hw.vl53l0x_address:#x}=VL53L0X, {hw.pn532_address:#x}=PN532 "
        "(둘 다 보이면 배선 OK)\n"
    )

    print("[2] VL53L0X 거리 5회")
    try:
        from pi_sensors_to_unity_udp import _init_vl53l0x

        sensor = _init_vl53l0x()
        for i in range(5):
            print(f"  sample {i + 1}: {sensor.range} mm")
            time.sleep(0.3)
        print("  VL53L0X OK\n")
    except Exception as e:  # noqa: BLE001
        print(f"  VL53L0X FAIL: {e}\n")

    print("[3] PyAudio 입력 장치")
    try:
        from voice_to_unity import autodetect_input_device, list_input_devices

        list_input_devices()
        detected = autodetect_input_device()
        print(f"  auto-detect index: {detected}\n")
    except Exception as e:  # noqa: BLE001
        print(f"  INMP441 FAIL: {e}\n")

    print("점검 완료. 문제 없으면:")
    print("  bash bringup_sensors.sh <S23_IP>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
