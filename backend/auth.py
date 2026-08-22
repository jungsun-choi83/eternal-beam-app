"""
검증된 신원 — 프로덕션 프리미엄 경로 전용.

지금까지 백엔드에는 인증이 **하나도 없었다**. user_id 가 경로/바디 파라미터로
들어왔고, 프론트는 그것을 localStorage 에서 만들어 냈다(`user_${Date.now()}`).
그 상태로 과금을 붙이면:
  * 남의 user_id 로 조회하면 남의 서명 URL 이 나온다
  * 남의 user_id 로 생성하면 남의 크레딧이 나간다
  * localStorage 를 지우면 STARTER_CREDITS 를 무한히 받는다

여기서는 Supabase JWT 를 검증해 `sub` 를 user_id 로 쓴다. 토큰이 없거나 서명이
틀리면 401 이다 — 폴백은 없다.

── 서명 알고리즘: 토큰이 정한다 ────────────────────────────────────────────
예전에는 무조건 HS256 + SUPABASE_JWT_SECRET 으로 검증했다. Supabase 가 비대칭
키로 옮기면서 실제 액세스 토큰이 **ES256** 이 됐고, 그 순간 모든 인증 요청이
InvalidAlgorithmError → 401 이 됐다. 무료 경로는 인증을 타지 않아 멀쩡해 보였고,
그래서 결제·주문 같은 인증 경로만 조용히 죽어 있었다.

이제는 토큰 헤더의 alg 를 읽어 경로를 가른다:

    ES256/RS256  → JWKS 공개키 (프로젝트의 /auth/v1/.well-known/jwks.json)
    HS256        → SUPABASE_JWT_SECRET (헤더가 HS256 일 때만)
    그 외        → 401 (특히 'none' 은 서명 없는 토큰이다)

**ES256 토큰에 대칭 비밀을 시도하지 않는다.** 알고리즘 혼동은 그 자체로 알려진
취약점 부류이고, 여기서는 헤더가 말한 알고리즘의 검증 경로로만 보낸다.

⚠️ 레거시 경로(4코인 /generate-with-credit, /generate-pet-video, dev_premium)는
**건드리지 않는다.** 이 모듈은 새 프리미엄 라우터에서만 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException

#: 레거시 대칭 서명(HS256) 토큰 전용 시크릿.
#: ⚠️ 이제 **필수가 아니다.** 현재 Supabase 액세스 토큰은 ES256(비대칭)이라
#: JWKS 공개키로 검증한다. 이 값은 옛 HS256 토큰이 아직 살아 있을 때만 쓰인다.
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


def _project_url() -> str:
    """Supabase 프로젝트 URL. SUPABASE_URL 이 정본이고, 프론트 변수는 폴백이다."""
    for k in ("SUPABASE_URL", "VITE_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"):
        v = (os.getenv(k) or "").strip().rstrip("/")
        if v:
            return v
    return ""


def _expected_issuer() -> str:
    """
    이 토큰이 **우리 프로젝트에서** 나왔는가를 가르는 값.

    없으면 다른 Supabase 프로젝트가 발급한, 서명이 완벽히 유효한 토큰도 통과한다 —
    그쪽 프로젝트는 누구나 무료로 만들 수 있으므로 사실상 인증이 없는 것과 같다.
    """
    explicit = (os.getenv("SUPABASE_JWT_ISSUER") or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = _project_url()
    return f"{base}/auth/v1" if base else ""


#: JWKS 클라이언트는 **재사용해야 한다.** 매 요청 새로 만들면 요청마다 키를 다시
#: 받아 오고(추가 왕복), Supabase 쪽 레이트리밋에도 걸린다. PyJWKClient 는 내부
#: 캐시를 갖고 있으므로 프로세스 수명 동안 하나만 둔다.
_jwks_client = None
_jwks_client_url = ""


def _jwks_signing_key(token: str):
    """
    토큰 헤더의 kid 에 맞는 **공개키**. 비밀이 아니라 공개키이므로 유출 위험이 없다.

    키 회전은 Supabase 가 한다. kid 가 캐시에 없으면 PyJWKClient 가 알아서 다시
    받아 오므로, 회전 시에도 배포 없이 따라간다.
    """
    global _jwks_client, _jwks_client_url

    base = _project_url()
    if not base:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_NOT_CONFIGURED",
                "message": "SUPABASE_URL 이 설정되지 않아 JWKS 를 조회할 수 없습니다.",
            },
        )

    url = (os.getenv("SUPABASE_JWKS_URL") or "").strip() or f"{base}/auth/v1/.well-known/jwks.json"
    if _jwks_client is None or _jwks_client_url != url:
        from jwt import PyJWKClient

        # lifespan_kwargs 없이 기본 캐시 사용 — 키는 자주 바뀌지 않는다.
        _jwks_client = PyJWKClient(url, cache_keys=True)
        _jwks_client_url = url

    return _jwks_client.get_signing_key_from_jwt(token).key


def __reset_jwks_cache_for_tests() -> None:
    global _jwks_client, _jwks_client_url
    _jwks_client = None
    _jwks_client_url = ""


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

    try:
        import jwt
    except ImportError as e:  # pragma: no cover - 배포 의존성 누락
        raise HTTPException(
            status_code=503,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "PyJWT 가 설치되지 않았습니다."},
        ) from e

    # ── 어떤 알고리즘으로 서명됐는가 ──────────────────────────────────────────
    # **토큰 헤더가 정한다.** 우리가 고르지 않는다. 예전에는 무조건 HS256 으로
    # 검증했는데, Supabase 가 비대칭 키(ES256)로 옮기면서 모든 요청이
    # InvalidAlgorithmError → 401 이 됐다.
    #
    # 헤더를 읽는 것 자체는 **검증이 아니다**(서명 확인 전이다). 그래서 여기서는
    # "어느 검증 경로로 보낼지"만 정하고, 실제 신뢰는 아래 decode 가 만든다.
    try:
        header = jwt.get_unverified_header(raw)
    except Exception as e:
        raise _unauthorized(f"토큰 헤더를 읽지 못했습니다: {type(e).__name__}") from e

    alg = str(header.get("alg") or "").strip()
    audience = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    common = {
        "audience": audience,
        "issuer": _expected_issuer(),
        "options": {"require": ["sub", "exp"]},
    }
    # issuer 가 확정되지 않은 환경(로컬 등)에서는 iss 검사를 건너뛴다 —
    # 여기서 빈 문자열을 기대값으로 넘기면 **모든 토큰이 거절된다.**
    if not common["issuer"]:
        common.pop("issuer")

    try:
        if alg.upper().startswith("ES") or alg.upper().startswith("RS"):
            # 비대칭: JWKS 의 공개키로 검증한다. 비밀은 필요 없다.
            key = _jwks_signing_key(raw)
            claims = jwt.decode(raw, key, algorithms=[alg], **common)
        elif alg.upper() == "HS256":
            # 레거시 대칭 토큰. **헤더가 HS256 이라고 말할 때만** 이 경로로 온다 —
            # ES256 토큰에 대칭 비밀을 들이대지 않는다.
            secret = (os.getenv(_SECRET_ENV) or "").strip()
            if not secret:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "AUTH_NOT_CONFIGURED",
                        "message": (
                            f"HS256 토큰을 받았지만 {_SECRET_ENV} 가 설정되지 않았습니다."
                        ),
                    },
                )
            claims = jwt.decode(raw, secret, algorithms=["HS256"], **common)
        else:
            # 모르는 알고리즘은 통과시키지 않는다. 특히 'none' 은 서명 없는 토큰이다.
            raise _unauthorized(f"지원하지 않는 서명 알고리즘입니다: {alg or '(없음)'}")
    except HTTPException:
        raise
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
