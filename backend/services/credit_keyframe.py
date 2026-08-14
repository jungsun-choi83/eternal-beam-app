"""
System B 전용: 투명 RGBA 누끼 → **순정 검정 플레이트** 키프레임.

왜 필요한가
-----------
System B 는 지금까지 `/api/matting/cutout` 이 만든 RGBA PNG URL 을 그대로 Luma 에
넘겼다. 그런데 그 PNG 의 RGB 채널은 알파 뒤에 **원본 사진 배경이 그대로** 남아
있다(vitmatte_service 는 `np.dstack([rgb, alpha])` 로 알파만 덧붙인다). 모델이
알파를 무시하고 평탄화하면 사용자의 거실이 배경으로 되살아난다.

기기 재생 구조상 펫 클립은 **검정 배경 위 펫만** 이어야 한다 — S23 셰이더가
휘도로 키잉해서 Pi 가 트는 배경 영상 위에 얹기 때문이다. 그래서 제출 전에
검정으로 평탄화한다.

System A 는 `resolve_keyframe_bg_rgb()` 로 검정/흰색을 코트 밝기에 따라 고르지만,
System B 는 **항상 검정**이다. 흰색 플레이트를 렌더링할 수 있는 소비자가 없다
(S23 셰이더에는 반전 경로가 없다).

실패하면 KeyframePreparationError 를 던진다 — **원본 RGBA URL 로 폴백하지 않는다.**
폴백하면 알파 뒤에 남아 있는 원본 사진 배경이 그대로 영상에 살아나고, 그 픽셀이
S23 의 검정 키를 통과해 Pi 배경 위에 사용자의 거실이 겹쳐 보인다. 그런 결과물에
크레딧 4개를 태우느니 한 건도 제출하지 않고 환불받는 편이 낫다.
"""

from __future__ import annotations

import logging
from typing import Optional

from .luma_keyframe import BG_BLACK, flatten_rgba_to_jpeg_bytes

logger = logging.getLogger(__name__)

KEYFRAME_CONTENT_TYPE = "image/jpeg"


class KeyframePreparationError(RuntimeError):
    """
    검정 플레이트 준비 실패. 호출자는 **Luma 를 한 건도 제출하지 말고** 환불해야 한다.

    generate_with_credit 의 기존 `except Exception: refund; raise` 경로가 그대로
    받아 준다 — 차감은 되돌아가고 제출은 0건이 된다.
    """

    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        msg = f"black-plate keyframe preparation failed at {stage}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


def keyframe_object_path(session_id: str) -> str:
    """세션별 검정 플레이트 키프레임 저장 경로 (모션 저장 경로와 겹치지 않는다)."""
    return f"creditkf/{session_id}/keyframe_black.jpg"


def is_remote_asset_url(url: str) -> bool:
    """
    키프레임 원본으로 쓸 수 있는 URL 인가 — http(s) 만 허용한다.

    `data:` URL 을 그대로 넘기면 httpx 가 실패해 stage="download" 로 떨어지는데,
    그건 "네트워크/스토리지 문제"처럼 보여서 원인(누끼가 애초에 업로드되지 않음)을
    가린다. 실제로 이 혼동으로 한 번 디버깅을 헛돌았다.
    """
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


async def _fetch_bytes(url: str) -> Optional[bytes]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            return res.content
    except Exception:
        logger.warning("credit keyframe: 원본 누끼 다운로드 실패 (url=%.80s)", url, exc_info=True)
        return None


async def prepare_black_plate_keyframe(pet_image_url: str, session_id: str) -> str:
    """
    RGBA 누끼 URL → 검정 평탄화 JPEG 을 업로드하고 그 공개 URL 을 돌려준다.

    Raises:
        KeyframePreparationError: 어느 단계든 실패하면. 원본 RGBA URL 로
            폴백하지 않는다 — 잘못된 배경으로 유료 생성을 태우지 않기 위함.
    """
    # 스킴 검사를 먼저 한다 — data:/빈 값을 download 실패로 뭉개면 원인이 가려진다.
    if not is_remote_asset_url(pet_image_url):
        scheme = (pet_image_url or "").strip().split(":", 1)[0][:16] or "(empty)"
        raise KeyframePreparationError(
            "invalid_url",
            f"pet_image_url must be an http(s) URL, got '{scheme}:' — "
            "누끼가 스토리지에 업로드되지 않았다",
        )

    raw = await _fetch_bytes(pet_image_url)
    if not raw:
        raise KeyframePreparationError("download", "cutout image could not be fetched")

    try:
        jpeg = flatten_rgba_to_jpeg_bytes(raw, bg_rgb=BG_BLACK)
    except Exception as e:
        logger.warning("credit keyframe: 검정 평탄화 실패", exc_info=True)
        raise KeyframePreparationError("flatten", f"{type(e).__name__}: {e}") from e

    try:
        from . import supabase_assets

        url = await supabase_assets.upload_asset_to_storage(
            keyframe_object_path(session_id), jpeg, KEYFRAME_CONTENT_TYPE
        )
    except Exception as e:
        logger.warning("credit keyframe: 업로드 실패", exc_info=True)
        raise KeyframePreparationError("upload", f"{type(e).__name__}: {e}") from e

    if not url:
        raise KeyframePreparationError("upload", "storage returned an empty URL")
    logger.info(
        "credit keyframe: black plate ready (session=%s, %d bytes)", session_id, len(jpeg)
    )
    return url
