"""PN532 NFC → 슬롯 번호 (raspi_nfc_playback 용)."""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NFC_UID_SLOT_PATH = Path(os.getenv("NFC_UID_SLOT_MAP", str(BASE_DIR / "nfc_uid_slot.json")))

_pn532 = None


def load_uid_slot_map() -> tuple[dict[str, int], int | None]:
    default_slot: int | None = None
    mapping: dict[str, int] = {}
    if not NFC_UID_SLOT_PATH.exists():
        return mapping, 1
    with open(NFC_UID_SLOT_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    if "_default_slot" in raw:
        try:
            default_slot = int(raw["_default_slot"])
        except (TypeError, ValueError):
            default_slot = None
    for k, v in raw.items():
        if str(k).startswith("_"):
            continue
        try:
            mapping[str(k).upper()] = int(v)
        except (TypeError, ValueError):
            continue
    return mapping, default_slot


def init_pn532():
    global _pn532
    if _pn532 is not None:
        return _pn532
    from pi_sensors_to_unity_udp import _init_pn532 as _init  # type: ignore

    _pn532 = _init()
    return _pn532


def read_nfc_slot_from_pn532() -> int | None:
    """카드 UID → slot_map 슬롯 번호. 미등록이면 _default_slot."""
    uid_map, default_slot = load_uid_slot_map()
    try:
        pn532 = init_pn532()
        uid = pn532.read_passive_target(timeout=0.1)
    except Exception:
        return None
    if uid is None:
        return None
    uid_hex = uid.hex().upper()
    return uid_map.get(uid_hex, default_slot)
