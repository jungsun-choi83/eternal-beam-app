"""
검증된 신원 — 프로덕션 프리미엄 경로 전용.

지금까지 백엔드에는 인증이 **하나도 없었다**. user_id 가 경로/바디 파라미터로
들어왔고, 프론트는 그것을 localStorage 에서 만들어 냈다(`user_${Date.now()}`).
그 상태로 과금을 붙이면:
  * 남의 user_id 로 조회하면 남의 서명 URL 이 나온다
  * 남의 user_id 로 생성하면 남의 크레딧이 나간다
  * localStorage 를 지우면 STARTER_CREDITS 를 무한히 받는다

여기서는 Supabase JWT(HS256) 를 검증해 `sub` 를 user_id 로 쓴다. 토큰이 없거나
서명이 틀리면 401 이다 — 폴백은 없다.

⚠️ 레거시 경로(4코인 /generate-with-credit, /generate-pet-video, dev_premium)는
**건드리지 않는다.** 이 모듈은 새 프리미엄 라우터에서만 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException

#: Supabase 프로젝트의 JWT 시크릿. 없으면 프로덕션에서 인증이 성립하지 않는다.
_SECRET_ENV = "SUPABASE_JWT_SECRET"

#: 테스트/로컬 전용 우회. **프로덕션에서는 절대 켜지 않는다.**
#: 켜져 있으면 Authorization: Bearer test:<user_id> 를 그대로 신뢰한다.
_DEV_BYPASS_ENV = "ALLOW_INSECURE_TEST_AUTH"


@dataclass(frozen=True)
class AuthPrincipal:
    """JWT 에서 **그대로** 읽은 값. 아직 Eternal Beam 신원이 아니다."""

    subject: str
    email: str | None = None
    email_verified: bool = False


@dataclass(frozen=True)
class AuthedUser:
    """
    확정된 호출자.

    user_id 는 **Eternal Beam 안정 신원**이지 Supabase sub 가 아니다. 둘을 같게
    두면 기존 데이터(지갑·생성 자산·구매 원장)가 전부 고아가 된다 —
    services/identity_service.py 참고.
    """

    user_id: str
    subject: str = ""
    email: str | None = None


def _dev_bypass_enabled() -> bool:
    return os.getenv(_DEV_BYPASS_ENV, "0").strip().lower() in ("1", "true", "yes")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": detail})


def verify_bearer_token(token: str) -> AuthPrincipal:
    """
    Bearer 토큰 → **검증된 클레임**. 실패하면 예외를 던진다(절대 None 을 돌려주지 않는다).

    여기서는 신원 연결을 하지 않는다 — 순수 검증만 한다. Eternal Beam 신원으로의
    변환은 require_user 가 identity_service 를 통해 한다.
    """
    raw = (token or "").strip()
    if not raw:
        raise _unauthorized("인증 토큰이 없습니다.")

    if _dev_bypass_enabled() and raw.startswith("test:"):
        uid = raw[len("test:") :].strip()
        if not uid:
            raise _unauthorized("테스트 토큰에 user_id 가 없습니다.")
        # 우회 토큰은 신원 연결을 거치지 않는다 — 주어진 값이 곧 신원이다.
        return AuthPrincipal(subject=uid, email=None, email_verified=False)

    secret = (os.getenv(_SECRET_ENV) or "").strip()
    if not secret:
        # 시크릿이 없으면 **아무도 통과시키지 않는다**. 예전 코드가 그랬듯 조용히
        # 폴백하면, 설정 누락이 곧 인증 없는 프로덕션이 된다.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_NOT_CONFIGURED",
                "message": f"{_SECRET_ENV} 가 설정되지 않아 인증을 수행할 수 없습니다.",
            },
        )

    try:
        import jwt
    except ImportError as e:  # pragma: no cover - 배포 의존성 누락
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "PyJWT 가 설치되지 않았습니다."},
        ) from e

    try:
        claims = jwt.decode(
            raw,
            secret,
            algorithms=["HS256"],
            # Supabase 액세스 토큰의 aud 는 'authenticated'.
            audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
            options={"require": ["sub", "exp"]},
        )
    except Exception as e:
        raise _unauthorized(f"토큰 검증 실패: {type(e).__name__}") from e

    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise _unauthorized("토큰에 sub 클레임이 없습니다.")

    # Supabase 는 이메일 검증 여부를 user_metadata 에 넣는다(프로젝트 설정에 따라
    # 최상위에도 온다). 둘 다 본다 — 검증된 이메일만 기존 신원 승계의 근거다.
    meta = claims.get("user_metadata") or {}
    verified = bool(
        claims.get("email_verified")
        or (isinstance(meta, dict) and meta.get("email_verified"))
    )
    return AuthPrincipal(
        subject=sub,
        email=str(claims.get("email") or "").strip() or None,
        email_verified=verified,
    )


async def require_user(authorization: str = Header(default="")) -> AuthedUser:
    """
    FastAPI 의존성. `Authorization: Bearer <jwt>` 만 받는다.

    토큰 검증 → **Eternal Beam 신원 확정**까지가 한 단위다. 라우터는 이미 확정된
    user_id 만 보고, 경로/바디/쿼리의 값은 어디서도 쓰지 않는다.
    """
    value = (authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise _unauthorized("Authorization: Bearer <token> 헤더가 필요합니다.")

    principal = verify_bearer_token(value[len("bearer ") :])

    # 개발 우회 토큰은 그대로 신원이 된다 (연결 테이블을 타지 않는다).
    if _dev_bypass_enabled() and value[len("bearer ") :].strip().startswith("test:"):
        return AuthedUser(user_id=principal.subject, subject=principal.subject)

    from .services.identity_service import IdentityUnavailableError, resolve_identity

    try:
        resolved = await resolve_identity(
            subject=principal.subject,
            email=principal.email,
            email_verified=principal.email_verified,
        )
    except IdentityUnavailableError as e:
        # 신원을 확정하지 못하면 **닫는다.** 여기서 sub 로 폴백하면 같은 사용자가
        # 상황에 따라 다른 신원을 갖게 되어 지갑·자산이 갈라진다.
        raise HTTPException(
            status_code=503,
            detail={"code": "IDENTITY_UNAVAILABLE", "message": str(e)},
        ) from e

    return AuthedUser(
        user_id=resolved.user_id, subject=resolved.subject, email=resolved.email
    )
