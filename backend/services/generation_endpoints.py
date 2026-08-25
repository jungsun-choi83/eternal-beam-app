"""
유료 생성 엔드포인트의 개폐 플래그.

**의존성이 없는 모듈이다.** routers/generate.py 는 생성 파이프라인 전체(cv2·torch
등)를 끌고 오므로, 그 안에 플래그를 두면 "이 경로가 닫혀 있는가"를 확인하는 것만으로도
무거운 스택 전체가 필요해진다. 개폐 판정은 배포 안전장치라 어디서든 가볍게 읽히고
검사될 수 있어야 한다.
"""

from __future__ import annotations

import os


def _truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes")


def idle_variant_enabled() -> bool:
    """
    `/api/generate-idle-variant` 를 열 것인가. **기본은 닫힘.**

    ── 왜 닫아 두는가 ──────────────────────────────────────────────────────
    이 경로는 아직 scene_generation_jobs 예약을 쓰지 않는다. provider_job_id 를
    남기지 않으므로 **이미 제출한 유료 작업을 되찾을 방법이 없고**, 동기식이라
    클라이언트 타임아웃·새로고침·502 재시도가 각각 새 유료 작업이 된다.
    `/generate-pet-video` 가 고치기 전에 갖고 있던 노출과 같은 것이다.

    ⚠️ `ENABLE_GENERATE_API` 로는 대신할 수 없다. 그 플래그는 라우터 **전체**를
    끄는데, 보호가 끝난 `/generate-pet-video`(BREATHING)가 같은 라우터에 있어
    함께 꺼진다. 그래서 별도 플래그가 필요하다.

    ── 언제 이 함수를 지우는가 ─────────────────────────────────────────────
    이 경로에 예약 → provider_job_id 기록 → 복구 폴링이 붙는 날. 그전에 켜는 것은
    개발/스테이징에서 비용을 감수하고 실험할 때뿐이다.
    """
    return _truthy("ENABLE_IDLE_VARIANT_API")


#: 닫혀 있을 때 돌려줄 응답 본문. 라우터가 그대로 HTTPException 에 싣는다.
DISABLED_DETAIL = {
    "code": "GENERATION_ENDPOINT_DISABLED",
    "message": (
        "이 생성 경로는 현재 사용할 수 없습니다. "
        "중복 제출 보호가 적용될 때까지 비활성화되어 있습니다."
    ),
    "endpoint": "/api/generate-idle-variant",
}
DISABLED_STATUS = 503
