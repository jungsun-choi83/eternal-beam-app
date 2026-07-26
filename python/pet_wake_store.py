"""앱에서 등록한 반려 이름 — 마이크 voice UDP 페이로드용."""

from __future__ import annotations

import json
import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent
WAKE_FILE = Path(os.getenv("PET_WAKE_FILE", str(_BASE / "pet_wake_names.json")))


def save_wake_names(names: list[str], *, pet_name: str | None = None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        part = str(raw).strip()
        if not part or part in seen:
            continue
        seen.add(part)
        cleaned.append(part)
    primary = (pet_name or (cleaned[0] if cleaned else "")).strip()
    payload = {"pet_name": primary, "wake_names": cleaned}
    WAKE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def load_wake_profile() -> dict[str, object]:
    try:
        if WAKE_FILE.is_file():
            data = json.loads(WAKE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                names = data.get("wake_names") or []
                if isinstance(names, list):
                    cleaned = [str(n).strip() for n in names if str(n).strip()]
                    primary = str(data.get("pet_name") or (cleaned[0] if cleaned else "")).strip()
                    return {"pet_name": primary, "wake_names": cleaned}
    except Exception:
        pass
    return {"pet_name": "", "wake_names": []}


def load_wake_names() -> list[str]:
    return list(load_wake_profile().get("wake_names") or [])  # type: ignore[arg-type]


def primary_pet_name() -> str:
    return str(load_wake_profile().get("pet_name") or "")


def voice_payload_extras() -> dict[str, object]:
    profile = load_wake_profile()
    names = profile.get("wake_names") or []
    primary = str(profile.get("pet_name") or "")
    out: dict[str, object] = {}
    if primary:
        out["pet_name"] = primary
    if names:
        out["wake_names"] = names
    return out
