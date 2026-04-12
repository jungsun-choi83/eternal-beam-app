#!/usr/bin/env python3
"""
Eternal Beam — Pi NFC → Unity Bridge

When NFC tag is scanned, sends slot/content_id to Unity (on PC) via:
  - Socket (TCP): Pi connects to Unity server (default)
  - Serial (USB): Pi writes to serial; Unity reads from COM port

Usage:
  python pi_nfc_to_unity.py --mode socket --host 192.168.1.100 --port 9999
  python pi_nfc_to_unity.py --mode serial --port /dev/ttyUSB0
  python pi_nfc_to_unity.py --mode mock  # Simulate NFC scans (keys 1-4)

JSON format sent: {"slot": 1, "content_id": "optional-uuid", "theme": "Celestial"}
"""

import argparse
import json
import socket
import sys
import time
from pathlib import Path

# Optional: pyserial for serial mode
try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

# Optional: RPi.GPIO + SPI for RC522 NFC (when hardware present)
try:
    import RPi.GPIO as GPIO
    HAS_RPI = True
except ImportError:
    HAS_RPI = False

BASE_DIR = Path(__file__).resolve().parent
SLOT_MAP_PATH = BASE_DIR / "slot_map.json"


def load_slot_map():
    """Load slot mapping for theme names."""
    if not SLOT_MAP_PATH.exists():
        return {}
    with open(SLOT_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def read_nfc_mock():
    """
    Mock NFC: returns (slot, content_id) or None.
    In mock mode, this is called by key input in main loop.
    """
    return None  # Handled by input() in main


def read_nfc_rc522():
    """
    Real NFC: read from RC522/PN532 (requires mfrc522 or similar).
    Returns (slot, content_id) or None.
    """
    # Placeholder for actual RC522/PN532 integration
    # Example with mfrc522: uid = reader.read_id() -> map to slot
    return None


def send_via_socket(host: str, port: int, data: dict) -> bool:
    """Send JSON to Unity TCP server."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect((host, port))
        msg = json.dumps(data) + "\n"
        sock.sendall(msg.encode("utf-8"))
        sock.close()
        return True
    except Exception as e:
        print(f"[Socket] Error: {e}")
        return False


def send_via_serial(port: str, baud: int, data: dict) -> bool:
    """Send JSON to serial port (PC reads via COM)."""
    if not HAS_SERIAL:
        print("[Serial] pyserial not installed. pip install pyserial")
        return False
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
        msg = json.dumps(data) + "\n"
        ser.write(msg.encode("utf-8"))
        ser.close()
        return True
    except Exception as e:
        print(f"[Serial] Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Pi NFC → Unity bridge")
    parser.add_argument(
        "--mode",
        choices=["socket", "serial", "mock"],
        default="socket",
        help="socket=TCP to Unity, serial=USB serial, mock=keyboard sim",
    )
    parser.add_argument("--host", default="192.168.1.100", help="Unity PC IP (socket mode)")
    parser.add_argument("--port", type=int, default=9999, help="Unity TCP port (socket mode) or device (serial)")
    parser.add_argument("--serial-port", default="/dev/ttyUSB0", help="Serial device (serial mode)")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--debounce", type=float, default=2.0, help="Seconds between same-tag scans")
    args = parser.parse_args()

    if args.mode == "serial" and args.port != 9999:
        args.serial_port = args.port if isinstance(args.port, str) else args.serial_port

    slot_map = load_slot_map()
    last_slot = None
    last_sent = 0.0

    def send_to_unity(slot: int, content_id: str = ""):
        nonlocal last_slot, last_sent
        now = time.time()
        if slot == last_slot and (now - last_sent) < args.debounce:
            return
        last_slot = slot
        last_sent = now

        theme = slot_map.get(str(slot), {}).get("theme", "") if slot_map else ""
        data = {"slot": slot, "content_id": content_id, "theme": theme}

        if args.mode in ("socket", "mock"):
            ok = send_via_socket(args.host, args.port, data)
        elif args.mode == "serial":
            ok = send_via_serial(args.serial_port, args.baud, data)
        else:
            ok = False

        status = "OK" if ok else "FAIL"
        print(f"[{status}] NFC → Unity: slot={slot} content_id={content_id or '(none)'} theme={theme or '(none)'}")

    if args.mode == "mock":
        print("Mock mode: Press 1-4 to simulate NFC scan, q to quit")
        print(f"  Target: {args.host}:{args.port} (TCP)")
        while True:
            try:
                line = input(">>> ").strip().lower()
                if line == "q":
                    break
                slot = int(line) if line.isdigit() else 0
                if 1 <= slot <= 4:
                    send_to_unity(slot)
                else:
                    print("  Use 1-4 for slot, q to quit")
            except (KeyboardInterrupt, EOFError):
                break
        return

    # Production: poll NFC hardware (placeholder loop)
    print(f"[{args.mode}] Waiting for NFC scan... (Ctrl+C to exit)")
    if args.mode == "socket":
        print(f"  Unity server: {args.host}:{args.port}")
    else:
        print(f"  Serial: {args.serial_port} @ {args.baud} baud")

    while True:
        slot, content_id = read_nfc_rc522() or (None, "")
        if slot is not None:
            send_to_unity(slot, content_id)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
    sys.exit(0)
