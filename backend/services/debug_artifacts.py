"""
누끼 디버그 아티팩트 저장 — 개발 전용.

`CUTOUT_DEBUG_ENABLED=1` 일 때만 동작한다. 큰 base64 이미지를 여러 장 JSON 에
싣지 않고, `outputs/_debug/<content_id>/` 에 PNG 로 떨어뜨린 뒤 정적 마운트
(`/outputs`) 기준 상대 URL 목록만 돌려준다.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

_OUTPUTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "outputs")
)
DEBUG_SUBDIR = "_debug"


def debug_enabled() -> bool:
    return os.getenv("CUTOUT_DEBUG_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _safe(name: str) -> str:
    return _SAFE.sub("_", name)[:80] or "unnamed"


def store_debug_artifacts(content_id: str, artifacts: dict[str, bytes]) -> dict[str, str]:
    """
    아티팩트를 파일로 저장하고 {이름: URL 경로} 를 반환.
    저장에 실패해도 요청 전체를 실패시키지 않는다 (디버그 편의 기능일 뿐).
    """
    if not artifacts or not debug_enabled():
        return {}

    cid = _safe(content_id)
    target = os.path.join(_OUTPUTS_DIR, DEBUG_SUBDIR, cid)
    urls: dict[str, str] = {}
    try:
        os.makedirs(target, exist_ok=True)
    except Exception:
        logger.exception("debug artifacts: mkdir failed (%s)", target)
        return {}

    for name, data in artifacts.items():
        safe_name = _safe(name)
        path = os.path.join(target, safe_name)
        try:
            with open(path, "wb") as f:
                f.write(data)
            urls[name] = f"/outputs/{DEBUG_SUBDIR}/{cid}/{safe_name}"
        except Exception:
            logger.exception("debug artifacts: write failed (%s)", path)
    return urls
