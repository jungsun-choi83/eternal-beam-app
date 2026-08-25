"""
요청에 실려 온 **정본 장면**을 생성 입력으로 바꾼다.

라우터마다 같은 파싱·검증·다운로드를 반복하지 않기 위한 얇은 층이다. 행동별로
배경 처리를 나누지 않는다는 요구는 이 파일이 하나뿐이라는 사실로도 지켜진다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

BACKGROUND_TYPES = ("original", "theme", "custom")


@dataclass(frozen=True)
class SceneInput:
    scene_id: str
    background_type: str
    background_id: str
    scene_keyframe_url: str
    #: 다운로드된 장면 이미지. 프로바이더 키프레임의 원본이 된다.
    scene_bytes: Optional[bytes] = None

    @property
    def usable(self) -> bool:
        return bool(self.scene_id and self.scene_keyframe_url and self.scene_bytes)


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes")


def parse(
    *,
    scene_id: Optional[str],
    background_type: Optional[str],
    background_id: Optional[str],
    scene_keyframe_url: Optional[str],
    background_baked: Optional[str],
) -> Optional[SceneInput]:
    """
    폼 값 → 장면. 불완전하면 **None**(레거시 경로로 조용히 떨어진다).

    조용히 떨어지는 것이 맞는 이유: 장면은 화질 향상이지 정확성 요건이 아니다.
    여기서 400 을 던지면 구버전 클라이언트가 생성 자체를 못 하게 된다.
    """
    sid = (scene_id or "").strip()
    url = (scene_keyframe_url or "").strip()
    if not sid or not url:
        return None
    if not _truthy(background_baked):
        # 명시적으로 baked 가 아니면 장면으로 취급하지 않는다.
        return None

    btype = (background_type or "").strip().lower()
    if btype not in BACKGROUND_TYPES:
        logger.warning("알 수 없는 background_type=%r — 장면 없이 진행", btype)
        return None

    return SceneInput(
        scene_id=sid,
        background_type=btype,
        background_id=(background_id or "").strip(),
        scene_keyframe_url=url,
    )


async def fetch_bytes(scene: SceneInput) -> SceneInput:
    """
    장면 이미지를 내려받는다. 실패하면 scene_bytes 가 비어 있는 채로 돌려준다 —
    호출부는 `usable` 로 판단해 레거시 키프레임으로 떨어진다.

    **생성을 막지 않는다.** 배경을 잃는 것은 아쉽지만, 결제·크레딧이 걸린 요청을
    이미지 한 장 때문에 실패시키는 것보다 낫다.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(scene.scene_keyframe_url)
            res.raise_for_status()
            data = res.content
        if not data:
            raise ValueError("empty scene image")
        return SceneInput(
            scene_id=scene.scene_id,
            background_type=scene.background_type,
            background_id=scene.background_id,
            scene_keyframe_url=scene.scene_keyframe_url,
            scene_bytes=data,
        )
    except Exception:
        logger.warning(
            "장면 이미지를 내려받지 못했다 (scene=%s) — 레거시 키프레임으로 진행",
            scene.scene_id,
            exc_info=True,
        )
        return scene


async def resolve(
    *,
    scene_id: Optional[str],
    background_type: Optional[str],
    background_id: Optional[str],
    scene_keyframe_url: Optional[str],
    background_baked: Optional[str],
) -> Optional[SceneInput]:
    """파싱 + 다운로드. 쓸 수 없으면 None."""
    parsed = parse(
        scene_id=scene_id,
        background_type=background_type,
        background_id=background_id,
        scene_keyframe_url=scene_keyframe_url,
        background_baked=background_baked,
    )
    if not parsed:
        return None
    fetched = await fetch_bytes(parsed)
    return fetched if fetched.usable else None
