"""
요청에 실려 온 **정본 장면**을 생성 입력으로 바꾼다.

라우터마다 같은 파싱·검증·다운로드를 반복하지 않기 위한 얇은 층이다. 행동별로
배경 처리를 나누지 않는다는 요구는 이 파일이 하나뿐이라는 사실로도 지켜진다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import NamedTuple, Optional

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


class SceneRequest(NamedTuple):
    """
    요청이 **장면을 요구했는가**(requested) 와 **그 장면을 실제로 쓸 수 있는가**
    (scene) 는 서로 다른 질문이다.

    ── 왜 굳이 나누는가 ────────────────────────────────────────────────────
    예전에는 둘 다 `None` 하나로 표현했다. 그래서 이 두 상황이 호출부에서
    구분되지 않았다:

      (a) 클라이언트가 장면을 보내지 않았다 (레거시 흐름)
      (b) 클라이언트가 장면을 보냈는데 **우리가 그것을 가져오지 못했다**

    (a) 는 정상이다. (b) 는 고객이 고르고 승인한 배경이 사라졌다는 뜻이고,
    그대로 진행하면 고객이 본 적 없는 그림으로 유료 생성이 돌아간다. 실제로
    그렇게 동작했고, 게다가 그 경로는 멱등성 예약 블록(`if baked:`) 밖이라
    타임아웃 재시도가 **두 번째 유료 작업**이 됐다.

    이제 (b) 는 requested=True, scene=None 으로 나타나고 호출부가 멈출 수 있다.
    """

    #: 요청이 background_baked=true 를 실었는가. **의도**에 대한 사실이다.
    requested: bool
    #: 실제로 생성 입력으로 쓸 수 있는 장면. requested=True 인데 None 이면 실패다.
    scene: Optional["SceneInput"]


#: 레거시 요청 — 장면을 요구하지 않았다. 지금까지처럼 진행한다.
LEGACY = SceneRequest(requested=False, scene=None)


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes")


def parse(
    *,
    scene_id: Optional[str],
    background_type: Optional[str],
    background_id: Optional[str],
    scene_keyframe_url: Optional[str],
    background_baked: Optional[str],
) -> SceneRequest:
    """
    폼 값 → SceneRequest.

    `background_baked` 가 참이 아니면 **레거시**다 — 장면을 요구하지 않은
    요청이고, 지금까지처럼 조용히 진행한다. 구버전 클라이언트가 생성 자체를
    못 하게 되면 안 되므로 여기서 400 을 던지지 않는다.

    참인데 나머지 필드가 어긋나면 requested=True, scene=None 이다. 조용히
    레거시로 떨어뜨리지 **않는다** — 고객이 배경을 골랐다고 말한 요청이고,
    그 배경 없이 만든 영상은 고객이 승인한 적 없는 그림이다.
    """
    if not _truthy(background_baked):
        return LEGACY

    sid = (scene_id or "").strip()
    url = (scene_keyframe_url or "").strip()
    if not sid or not url:
        logger.warning(
            "장면을 요구했으나 필드가 비었다 (scene_id=%r keyframe=%r)", sid, bool(url)
        )
        return SceneRequest(requested=True, scene=None)

    btype = (background_type or "").strip().lower()
    if btype not in BACKGROUND_TYPES:
        logger.warning("알 수 없는 background_type=%r — 장면을 쓸 수 없다", btype)
        return SceneRequest(requested=True, scene=None)

    return SceneRequest(
        requested=True,
        scene=SceneInput(
            scene_id=sid,
            background_type=btype,
            background_id=(background_id or "").strip(),
            scene_keyframe_url=url,
        ),
    )


async def fetch_bytes(scene: SceneInput) -> SceneInput:
    """
    장면 이미지를 내려받는다. 실패하면 scene_bytes 가 비어 있는 채로 돌려준다 —
    호출부는 `usable` 로 판단한다.

    **여기서 판단하지 않는다.** 예전 주석은 "생성을 막지 않는다"고 적혀 있었고
    실제로 그랬지만, 그것이 곧 고객이 고른 배경 없이 유료 생성이 도는 경로였다.
    막을지 말지는 이제 `resolve` 의 requested 값을 보는 호출부가 정한다.
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
) -> SceneRequest:
    """
    파싱 + 다운로드.

    돌려주는 것은 **두 사실**이다: 장면을 요구했는가, 그리고 쓸 수 있는가.
    호출부는 `requested and scene is None` 을 보고 멈춰야 한다 —
    그 조합이 "고객이 고른 배경을 우리가 잃었다"는 뜻이다.
    """
    req = parse(
        scene_id=scene_id,
        background_type=background_type,
        background_id=background_id,
        scene_keyframe_url=scene_keyframe_url,
        background_baked=background_baked,
    )
    if not req.requested or req.scene is None:
        return req
    fetched = await fetch_bytes(req.scene)
    return SceneRequest(
        requested=True, scene=fetched if fetched.usable else None
    )
