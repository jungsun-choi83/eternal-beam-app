"""
9:16 비디오 앵커 (Phase 6) — 승인 키프레임을 I2V 프로바이더 기하 계약에 맞춘다.

── 왜 필요한가 (라이브 검증) ───────────────────────────────────────────────
Seedance 2.5 I2V 는 aspect_ratio 가 항상 "auto" 다: 출력 기하를 **입력 이미지**가
결정한다. Kling V3 I2V 에도 aspect_ratio 파라미터가 없다. 그래서 요청 종횡비
(9:16)는 파라미터가 아니라 시작/끝 이미지 자체로 강제한다 — 1:1 키프레임을
그대로 보내면 1:1 영상이 나온다 (BREATHING V2 의 1440×1440 사고).

── 계약 ────────────────────────────────────────────────────────────────────
  * 승인된 펫 픽셀을 **그대로** 보존한다 — 리샘플링/재생성 없음, 패딩만.
  * 배치는 결정론적이다: 중앙 정렬, 키프레임 테두리 중앙값 색으로 패딩
    (키프레임의 중립 회색 배경과 이어진다 — 사용자 테마 없음).
  * 신원을 바꾸지 않는다. GENERATED 가 아니라 DERIVED 로 대장에 기록되며,
    근거(diagnostics)에 원본 키프레임 id/버전/후보를 남긴다.
  * 시작 앵커와 끝 앵커 모두 같은 함수 하나로 만든다.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
from types import SimpleNamespace
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

VIDEO_ANCHOR_VERSION = "video-anchor-v1"
DERIVED_KIND_VIDEO_ANCHOR = "video_anchor"

#: 이 비율 이내면 이미 요청 종횡비다 — 앵커 불필요.
_ASPECT_TOLERANCE = 0.02


def anchor_enabled() -> bool:
    return os.getenv("PHASE6_VIDEO_ANCHOR", "1").strip().lower() not in ("0", "false", "no")


def _parse_ratio(aspect_ratio: str) -> Optional[tuple[int, int]]:
    try:
        w, h = (aspect_ratio or "").split(":")
        aw, ah = int(w), int(h)
        return (aw, ah) if aw > 0 and ah > 0 else None
    except (ValueError, AttributeError):
        return None


def needs_anchor(width: int, height: int, aspect_ratio: str) -> bool:
    ratio = _parse_ratio(aspect_ratio)
    if not ratio or not width or not height:
        return False
    return abs((width / height) / (ratio[0] / ratio[1]) - 1.0) > _ASPECT_TOLERANCE


def _border_median_rgb(rgb: np.ndarray) -> tuple[int, int, int]:
    """1px 테두리의 채널별 중앙값 — 키프레임 자체의 배경색 (결정론)."""
    edges = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]])
    med = np.median(edges, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def build_anchor_image(image_bytes: bytes, *, aspect_ratio: str = "9:16") -> tuple[bytes, dict[str, Any]]:
    """
    결정론적 9:16 앵커: 원본 픽셀 무변형 페이스트 + 테두리색 패딩.

    캔버스는 (2·aw·k, 2·ah·k) — 양변이 짝수이고 종횡비가 정확히 aw:ah 인
    가장 작은 캔버스에 원본이 통째로 들어간다 (k 는 결정론적).
    """
    from PIL import Image

    ratio = _parse_ratio(aspect_ratio)
    if not ratio:
        raise ValueError(f"invalid aspect_ratio {aspect_ratio!r}")
    aw, ah = ratio

    with Image.open(io.BytesIO(image_bytes)) as im:
        src_w, src_h = im.size
        rgb_arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
        bg = _border_median_rgb(rgb_arr)
        k = max(math.ceil(src_w / (2 * aw)), math.ceil(src_h / (2 * ah)), 1)
        canvas_w, canvas_h = 2 * aw * k, 2 * ah * k
        canvas = Image.new("RGB", (canvas_w, canvas_h), bg)
        ox, oy = (canvas_w - src_w) // 2, (canvas_h - src_h) // 2
        if im.mode in ("RGBA", "LA", "P"):
            src = im.convert("RGBA")
            canvas.paste(src, (ox, oy), src)  # 누끼 — 알파 합성
        else:
            canvas.paste(im.convert("RGB"), (ox, oy))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")

    return buf.getvalue(), {
        "anchor_version": VIDEO_ANCHOR_VERSION,
        "aspect_ratio": aspect_ratio,
        "source_size": [src_w, src_h],
        "canvas_size": [canvas_w, canvas_h],
        "offset": [ox, oy],
        "background_rgb": list(bg),
        "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def _anchor_path(source_object_path: str, aspect_ratio: str) -> str:
    stem = source_object_path.rsplit(".", 1)[0]
    return f"{stem}_anchor{aspect_ratio.replace(':', 'x')}.png"


async def ensure_video_anchor(
    *,
    user_id: str,
    content_id: str,
    keyframe: dict[str, Any],
    image_bytes: bytes,
    aspect_ratio: str,
) -> Optional[Any]:
    """
    키프레임이 요청 종횡비가 아니면 앵커를 만들고 업로드/대장 기록 후
    SimpleNamespace(bytes, url, object_path, meta) 를 돌려준다.
    이미 맞는 종횡비면 None (앵커 불필요 — 키프레임을 그대로 쓴다).
    """
    from PIL import Image

    from . import pet_reference_service, supabase_assets

    with Image.open(io.BytesIO(image_bytes)) as im:
        src_w, src_h = im.size
    if not needs_anchor(src_w, src_h, aspect_ratio):
        return None

    anchor_bytes, meta = build_anchor_image(image_bytes, aspect_ratio=aspect_ratio)
    source_path = ((keyframe.get("raw") or {}).get("object_path")) or ""
    if not source_path:
        raise ValueError("keyframe payload has no raw object_path")
    object_path = _anchor_path(source_path, aspect_ratio)

    url = await supabase_assets.upload_asset_to_storage(object_path, anchor_bytes, "image/png")
    # DERIVED 대장 기록은 필수다 (근거 없는 앵커로 생성하지 않는다) — 실패는 전파.
    await pet_reference_service.record_derived(
        user_id=user_id,
        content_id=content_id,
        object_path=object_path,
        derived_kind=DERIVED_KIND_VIDEO_ANCHOR,
        mime_type="image/png",
        diagnostics={
            "video_anchor": meta,
            "source_keyframe_id": keyframe.get("keyframe_id"),
            "source_keyframe_version": keyframe.get("version"),
            "source_candidate_id": keyframe.get("candidate_id"),
            "source_object_path": source_path,
        },
    )

    return SimpleNamespace(bytes=anchor_bytes, url=url, object_path=object_path, meta=meta)
