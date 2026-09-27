"""
프로덕션 프리미엄 라우터 — 인증·라우트 노출 계약.

지키려는 것:
  * 인증 없이는 아무것도 안 된다 (401). 경로/바디/쿼리의 user_id 를 믿지 않는다.
  * 시크릿이 없으면 **열리는 게 아니라 닫힌다** (503).
  * dev 라우트(/v1/pet/dev)는 프로덕션에 존재하지 않는다.
  * GET /assets 는 발견 전용 — 생성도 과금도 하지 않는다.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI, HTTPException

from backend.auth import verify_bearer_token
from backend.routers import premium_v1
from backend.services import premium_purchase, wallet_service

# starlette 의 TestClient 는 이 httpx 버전과 맞지 않는다 — 저장소 공용 래퍼를 쓴다.
from .conftest import ASGITestClient

SECRET = "test-jwt-secret-value"
USER = "auth_user_1"
PET = "auth_pet_1"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    yield
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> ASGITestClient:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    app = FastAPI()
    app.include_router(premium_v1.router, prefix="/api")
    return ASGITestClient(app)


def _token(user_id: str, *, secret: str = SECRET, **overrides) -> str:
    import jwt

    claims = {
        "sub": user_id,
        "aud": "authenticated",
        "exp": 4102444800,  # 2100-01-01
        **overrides,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def _auth(user_id: str, **kw) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, **kw)}"}


# ── 인증 ─────────────────────────────────────────────────────────────────────


def test_unauthenticated_request_is_rejected(client: ASGITestClient):
    for call in (
        lambda: client.get("/api/v1/pet/premium/assets", params={"pet_id": PET}),
        lambda: client.post(
            "/api/v1/pet/premium/purchase", json={"kind": "IDLE_BUNDLE", "pet_id": PET}
        ),
    ):
        r = call()
        assert r.status_code == 401, r.text


def test_non_bearer_scheme_is_rejected(client: ASGITestClient):
    r = client.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": "Basic abc123"},
    )
    assert r.status_code == 401


def test_invalid_signature_is_rejected(client: ASGITestClient):
    r = client.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": f"Bearer {_token(USER, secret='wrong-secret')}"},
    )
    assert r.status_code == 401


def test_expired_token_is_rejected(client: ASGITestClient):
    r = client.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": f"Bearer {_token(USER, exp=1000000000)}"},
    )
    assert r.status_code == 401


def test_garbage_token_is_rejected(client: ASGITestClient):
    r = client.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401


def test_valid_token_is_accepted(client: ASGITestClient):
    r = client.get("/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth(USER))
    assert r.status_code == 200, r.text
    assert r.json()["pet_id"] == PET


def test_missing_secret_fails_closed_not_open(monkeypatch: pytest.MonkeyPatch):
    """
    시크릿 누락이 '인증 없는 프로덕션' 이 되면 안 된다. 열지 말고 닫아야 한다.

    ⚠️ 모델이 바뀌었다: 현재 Supabase 액세스 토큰은 ES256 이라 JWKS 로 검증하고
    SUPABASE_JWT_SECRET 을 쓰지 않는다. 그래서 "시크릿이 없으면 무조건 503" 은
    더 이상 옳지 않다 — 그 규칙이 실제로 ES256 토큰을 전부 막고 있었다.

    변하지 않는 계약은 하나다: **검증할 수 없는 토큰은 절대 통과하지 않는다.**
    """
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    app = FastAPI()
    app.include_router(premium_v1.router, prefix="/api")
    c = ASGITestClient(app)

    # 아무 문자열 → JWT 조차 아니다. 401 로 닫힌다(열리지 않는다).
    r = c.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": "Bearer anything"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"

    # 진짜 HS256 토큰인데 검증할 시크릿이 없다 → 설정 문제이므로 503.
    # 어느 쪽이든 **통과는 없다.**
    r2 = c.get(
        "/api/v1/pet/premium/assets",
        params={"pet_id": PET},
        headers={"Authorization": f"Bearer {_token('someone@example.com')}"},
    )
    assert r2.status_code == 503
    assert r2.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"


def test_es256_token_is_not_verified_with_the_hs256_secret(monkeypatch: pytest.MonkeyPatch):
    """
    **핵심 회귀**: ES256 토큰에 대칭 비밀을 들이대지 않는다.

    예전에는 알고리즘을 우리가 정했고(HS256 고정), 그래서 Supabase 가 ES256 으로
    옮긴 순간 모든 인증 요청이 InvalidAlgorithmError 로 401 이 됐다. 이제는
    토큰 헤더가 경로를 정한다.
    """
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import ec

    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://someproject.supabase.co")

    key = ec.generate_private_key(ec.SECP256R1())
    token = pyjwt.encode(
        {
            "sub": "es256-user",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "iss": "https://someproject.supabase.co/auth/v1",
        },
        key,
        algorithm="ES256",
        headers={"kid": "not-in-our-jwks"},
    )

    # HS256 시크릿으로 조용히 통과시키지 않는다. JWKS 에 없는 kid 라 거절된다.
    with pytest.raises(HTTPException) as e:
        verify_bearer_token(token)
    assert e.value.status_code == 401


def test_alg_none_is_never_accepted(monkeypatch: pytest.MonkeyPatch):
    """서명 없는 토큰(alg=none)은 어떤 설정에서도 통과하지 않는다."""
    import jwt as pyjwt

    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    token = pyjwt.encode(
        {"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 3600},
        None,
        algorithm="none",
    )
    with pytest.raises(HTTPException) as e:
        verify_bearer_token(token)
    assert e.value.status_code == 401


def test_body_user_id_cannot_override_the_token(client: ASGITestClient):
    """
    바디에 남의 user_id 를 넣어도 무시된다 — 신원은 토큰에서만 나온다.
    (요청 모델에 user_id 필드 자체가 없다.)
    """
    assert "user_id" not in premium_v1.PurchaseRequest.model_fields


def test_identity_endpoint_returns_the_linked_eb_identity(client: ASGITestClient):
    """
    라우터가 보는 user_id 는 Supabase sub 가 아니라 **연결된 Eternal Beam 신원**이다.
    검증된 이메일이면 예전 데이터의 키(소문자 이메일)가 그대로 나와야 한다.
    """
    from backend.services import identity_service

    identity_service.__reset_for_tests()
    sub = "33333333-3333-3333-3333-333333333333"
    legacy = "owner@example.com"
    r = client.get(
        "/api/v1/pet/premium/identity",
        headers={
            "Authorization": (
                f"Bearer {_token(sub, email=legacy, email_verified=True)}"
            )
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == legacy, "sub 가 신원으로 새어 나왔다 — 기존 데이터가 고아가 된다"
    assert body["user_id"] != sub
    identity_service.__reset_for_tests()


def test_unverified_email_does_not_inherit_via_the_route(client: ASGITestClient):
    from backend.services import identity_service

    identity_service.__reset_for_tests()
    sub = "44444444-4444-4444-4444-444444444444"
    r = client.get(
        "/api/v1/pet/premium/identity",
        headers={
            "Authorization": (
                f"Bearer {_token(sub, email='victim@example.com', email_verified=False)}"
            )
        },
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == sub, "검증되지 않은 이메일로 신원을 승계했다"
    identity_service.__reset_for_tests()


def test_insecure_test_bypass_is_off_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(Exception):
        verify_bearer_token("test:someone")


# ── 소유권 ───────────────────────────────────────────────────────────────────


def test_another_users_pet_is_rejected(client: ASGITestClient, monkeypatch: pytest.MonkeyPatch):
    # 첫 사용자가 펫을 귀속시킨다.
    r = client.get("/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth(USER))
    assert r.status_code == 200

    r2 = client.get(
        "/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth("intruder")
    )
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "PET_NOT_OWNED"


def test_another_users_pet_cannot_be_purchased(client: ASGITestClient):
    client.get("/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth(USER))
    r = client.post(
        "/api/v1/pet/premium/purchase",
        json={"kind": "IDLE_BUNDLE", "pet_id": PET},
        headers=_auth("intruder"),
    )
    assert r.status_code == 403


# ── 발견은 과금하지 않는다 ───────────────────────────────────────────────────


def test_assets_endpoint_never_charges_or_generates(client: ASGITestClient):
    import anyio

    anyio.run(wallet_service.add_credits, USER, 5)
    for _ in range(5):
        r = client.get(
            "/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth(USER)
        )
        assert r.status_code == 200
    bal = anyio.run(lambda: wallet_service.get_wallet(USER, create_if_missing=True))
    assert bal.current_credits == 5, "발견 호출이 크레딧을 소모했다"


def test_assets_reports_registry_not_a_hardcoded_count(client: ASGITestClient):
    from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS

    r = client.get("/api/v1/pet/premium/assets", params={"pet_id": PET}, headers=_auth(USER))
    body = r.json()
    assert body["idle_events"] == list(IDLE_EVENTS)
    assert body["action_events"] == list(PET_ACTIONS)

    # 가격은 **상품 단위**로 실린다 (Phase 3). 예전에는 카테고리 스칼라 두 개
    # (idle_bundle_credits / action_event_credits)였고, 그 값은 환경변수에서
    # import 시점에 읽혀 응답 모델의 기본값으로 박혀 있었다 — 아이들 넷이 반드시
    # 같은 값이어야 했고, 바꾸려면 재배포가 필요했다.
    prices = body["prices"]
    assert prices["idle:BUNDLE"] == 1
    assert prices["action:COME_CLOSER"] == 1
    # 아이들 이벤트도 **각각** 값을 갖는다 — 서로 다른 값을 매길 수 있다는 뜻이다.
    for event in IDLE_EVENTS:
        assert f"idle:{event}" in prices
    assert "idle_bundle_credits" not in body, "카테고리 전역 가격 필드가 남아 있다"
    assert "action_event_credits" not in body


# ── dev 라우트는 프로덕션에 없다 ─────────────────────────────────────────────


def _include_router_calls() -> dict[str, bool]:
    """
    main.py 의 include_router 호출 → {라우터 이름: 조건문 안에 있는가}.

    앱을 reload 해서 검사할 수는 없다 — main.py 가 load_dotenv(override=True) 로
    개발자의 .env.local 을 다시 읽어 들여 monkeypatch 를 덮어쓰기 때문이다.
    그래서 **등록 구조 자체**를 AST 로 본다. 환경에 의존하지 않는다.
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    gated: dict[str, bool] = {}

    def walk(node: ast.AST, inside_if: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Attribute) and fn.attr == "include_router" and child.args:
                    arg = child.args[0]
                    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                        gated[arg.value.id] = inside_if
            walk(child, inside_if or isinstance(child, ast.If))

    walk(tree, False)
    return gated


def test_dev_router_stays_behind_its_env_gate():
    """
    render.yaml 에 ENABLE_DEV_PREMIUM_TRIGGER 가 없다 = 프로덕션에는 이 경로가
    **존재하지 않는다**. 프리미엄을 프로덕션화한다고 이걸 켜면 안 된다.
    """
    calls = _include_router_calls()
    assert calls.get("dev_premium") is True, "dev 라우터가 조건 없이 등록되고 있다"


def test_production_premium_router_is_registered_unconditionally():
    """인증이 방어선이므로 env 플래그 뒤에 숨지 않는다."""
    calls = _include_router_calls()
    assert "premium_v1" in calls, "프로덕션 프리미엄 라우터가 등록되지 않았다"
    assert calls["premium_v1"] is False, "프로덕션 라우터가 조건문 안에 있다"


def test_dev_gate_still_reads_the_expected_env_var():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'os.getenv("ENABLE_DEV_PREMIUM_TRIGGER"' in src


def test_premium_router_has_no_env_gate():
    """
    프로덕션 라우터가 env 플래그 뒤에 숨으면 안 된다 — 인증이 방어선이다.

    문자열 검색이 아니라 **실제 의존성**을 본다(주석에 플래그 이름이 나오는 것과
    코드가 그 플래그를 읽는 것은 다르다).
    """
    import inspect

    from backend.auth import require_user

    for handler in (premium_v1.get_premium_assets, premium_v1.purchase_premium):
        params = inspect.signature(handler).parameters
        assert "user" in params, f"{handler.__name__} 에 인증 의존성이 없다"
        assert params["user"].default.dependency is require_user, (
            f"{handler.__name__} 이 require_user 를 쓰지 않는다"
        )
