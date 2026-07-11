#!/usr/bin/env python3
"""흰색 NFC 카드 UID 읽기 — nfc_theme_map.json / nfc_uid_slot.json 등록용."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    try:
        from pi_sensors_to_unity_udp import _init_pn532  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"PN532 초기화 실패: {e}", file=sys.stderr)
        sys.exit(1)

    pn532 = _init_pn532()
    theme_map = BASE_DIR / "nfc_theme_map.json"
    slot_map = BASE_DIR / "nfc_uid_slot.json"
    print("NFC 카드를 리더에 대세요… (Ctrl+C 종료)", flush=True)

    last: str | None = None
    while True:
        try:
            uid = pn532.read_passive_target(timeout=0.5)
            if uid is None:
                last = None
                continue
            uid_hex = uid.hex().upper()
            if uid_hex == last:
                continue
            last = uid_hex
            print(f"\nUID = {uid_hex}", flush=True)
            print(f'  nfc_theme_map.json: "{uid_hex}": "forest"', flush=True)
            print(f'  nfc_uid_slot.json:  "{uid_hex}": 1', flush=True)

            if theme_map.exists():
                with open(theme_map, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get(uid_hex):
                    print(f"  (이미 theme={raw.get(uid_hex)!r})", flush=True)
        except KeyboardInterrupt:
            print("\n종료", flush=True)
            break
        except Exception as e:  # noqa: BLE001
            print(f"읽기 오류: {e}", flush=True)
            time.sleep(0.3)


if __name__ == "__main__":
    main()
