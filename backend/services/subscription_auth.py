"""
구독 웹훅 인가 — **누가 구독 상태를 바꿀 수 있는가**.

Phase 2 까지 이 엔드포인트는 인증이 전혀 없었다. 그리고 파서는 명시적
notification_type + user_id 를 그대로 받아들인다. 둘을 합치면 프로덕션에서
이 한 줄로 아무나 남의(또는 자기) 구독을 활성화할 수 있었다:

    POST /api/v1/subscription/webhook
    {"store_type":"apple","notification_type":"INITIAL_BUY","user_id":"victim", ...}

Phase 2 에서 이 엔드포인트가 **프로바이더 생성 비용의 관문**이 되면서(구독=생성
권한) 이 구멍의 폭발 반경이 커졌다. 여기서 닫는다.

경로는 두 개뿐이고 요구 조건이 다르다:

    실제 스토어(apple/google)  공유 시크릿 헤더가 있어야 한다.
                               Apple/Google 은 사용자 JWT 를 보낼 수 없으므로
                               사용자 인증을 요구할 수 없다.

    목업(mock)                 SUBSCRIPTION_MOCK=1 **그리고** 유효한 사용자 토큰.
                               user_id 는 바디에서 읽지 않고 **토큰에서 확정한다** —
                               목업으로 남의 구독을 건드릴 수 없고, 저장되는 신원이
                               프리미엄 인가가 조회하는 신원과 반드시 같아진다.

설정 누락은 **열리는 게 아니라 닫힌다**: 시크릿이 없으면 실제 웹훅은 503 이다.
auth.py 의 SUPABASE_JWT_SECRET 처리와 같은 규칙이다.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

#: 실제 스토어 웹훅이 제시해야 하는 공유 시크릿.
_SECRET_ENV = "SUBSCRIPTION_WEBHOOK_SECRET"

#: 시크릿을 싣는 헤더. Apple/Google 콘솔에서 커스텀 헤더로 설정한다.
WEBHOOK_SECRET_HEADER = "x-subscription-webhook-secret"


def mock_enabled() -> bool:
    return os.getenv("SUBSCRIPTION_MOCK", "0").strip().lower() in ("1", "true", "yes")


def _configured_secret() -> str:
    return (os.getenv(_SECRET_ENV) or "").strip()


def _forbidden(code: str, message: str, *, status: int = 403) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def is_mock_payload(body: dict[str, Any]) -> bool:
    """
    이 바디가 목업 이벤트인가.

    store_type 을 명시하지 않으면 파서가 "mock" 으로 떨어뜨린다(기본값). 그래서
    **미지정도 목업으로 본다** — 그러지 않으면 store_type 을 빼는 것만으로 실제
    스토어 경로의 시크릿 검사를 건너뛸 수 있다.
    """
    raw = body.get("raw") if isinstance(body.get("raw"), dict) else body
    store = (body.get("store_type") or raw.get("store_type") or "").strip().lower()
    return store in ("", "mock")


def assert_store_webhook_authorized(secret_header: Optional[str]) -> None:
    """
    실제 스토어 웹훅(apple/google) 인가. 실패하면 던진다.

    hmac.compare_digest 로 비교한다 — 문자열 == 는 앞에서부터 다르면 즉시 빠져
    나오므로 시크릿 길이·접두사가 타이밍으로 새어 나간다.
    """
    expected = _configured_secret()
    if not expected:
        # 설정 누락을 "검사 통과"로 해석하지 않는다. 이 엔드포인트는 구독 상태를
        # 바꾸고, 구독 상태는 생성 비용을 연다.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "WEBHOOK_NOT_CONFIGURED",
                "message": f"{_SECRET_ENV} 가 설정되지 않아 스토어 웹훅을 처리할 수 없습니다.",
            },
        )

    provided = (secret_header or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        logger.warning("구독 웹훅 시크릿 불일치 — 거절")
        raise _forbidden(
            "WEBHOOK_FORBIDDEN",
            "웹훅 시크릿이 올바르지 않습니다.",
            status=401,
        )


def assert_mock_webhook_allowed() -> None:
    """목업 이벤트는 SUBSCRIPTION_MOCK=1 에서만 받는다."""
    if not mock_enabled():
        raise _forbidden(
            "MOCK_DISABLED",
            "목업 구독 웹훅은 SUBSCRIPTION_MOCK=1 에서만 사용할 수 있습니다.",
        )
