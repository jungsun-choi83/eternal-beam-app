"""
Phase 3A — 구독 신원 정합성과 웹훅 인가.

Phase 2 검증 중 실제로 밟은 버그가 출발점이다: 구독은 앱이 보낸 raw user_id 로
저장되고, 프리미엄 인가는 토큰이 확정한 정규 신원으로 조회했다. 둘이 어긋나면
**결제한 사용자가 "구독 없음"으로 읽힌다.** 예외도 로그도 없이 조용히 틀린다.

여기서 고정하는 것:

  1) 저장 신원 == 인가 신원. 목업 웹훅은 바디 user_id 를 **무시하고** 토큰에서
     신원을 확정한다. 대소문자가 다른 이메일도 같은 신원으로 수렴한다.
  2) 웹훅은 아무나 부를 수 없다. 예전에는 이 한 줄로 남의 구독을 켤 수 있었다:
       {"store_type":"apple","notification_type":"INITIAL_BUY","user_id":"victim"}
  3) 구독 상태는 본인 것만 읽는다.
  4) 시크릿 미설정은 **열림이 아니라 닫힘**(503)이다.
"""

from __future__ import annotations

import datetime
import os

import jwt
import pytest
from fastapi import FastAPI

from backend.routers import premium_v1, subscription_v1
from backend.services import (
    identity_service,
    premium_entitlement,
    premium_purchase,
    subscription_store_service as sub_store,
    wallet_service,
)

from .conftest import ASGITestClient

SECRET = "test-jwt-secret-value-long-enough-32b"
HOOK_SECRET = "store-webhook-shared-secret"
EMAIL = "alice@example.com"
SUB_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SUB_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)
    monkeypatch.delenv("SUBSCRIPTION_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    identity_service.__reset_for_tests()
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    yield
    identity_service.__reset_for_tests()
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(subscription_v1.router, prefix="/api")
    app.include_router(premium_v1.router, prefix="/api")
    return ASGITestClient(app)


def _token(sub: str, email: str | None, *, verified: bool = True) -> str:
    claims = {
        "sub": sub,
        "aud": "authenticated",
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    if email:
        claims["email"] = email
        claims["email_verified"] = verified
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _auth(sub: str = SUB_A, email: str | None = EMAIL) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(sub, email)}"}


def _mock_event(event: str, **over):
    body = {"notification_type": event, "plan_id": "standard_subscription",
            "transaction_id": f"tx_{event}_{over.pop('tx', '1')}"}
    body.update(over)
    return body


# ── 1. 신원 정합성 ────────────────────────────────────────────────────────────


def test_canonical_user_id_lowercases_emails_like_resolve_identity_does():
    """저장 신원과 인가 신원이 같은 규칙을 쓴다."""
    assert identity_service.canonical_user_id("  Alice@Example.COM ") == EMAIL
    assert identity_service.canonical_user_id(EMAIL) == EMAIL


def test_canonical_user_id_preserves_non_email_ids():
    """sub UUID·레거시 익명 id 는 대소문자가 의미를 갖는다 — 건드리지 않는다."""
    assert identity_service.canonical_user_id(SUB_A) == SUB_A
    assert identity_service.canonical_user_id("user_AbC123") == "user_AbC123"
    assert identity_service.canonical_user_id("  ") is None


def test_mock_webhook_ignores_body_user_id_and_uses_the_token(client: ASGITestClient):
    """
    **Phase 2 에서 실제로 밟은 버그.** 바디에 남의 신원을 실어도 무시된다.
    """
    r = client.post(
        "/api/v1/subscription/webhook",
        json=_mock_event("INITIAL_BUY", user_id="somebody-else"),
        headers=_auth(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == EMAIL, "바디의 user_id 가 신원을 덮어썼다"
    assert "somebody-else" not in sub_store._MOCK_SUBS


def test_subscription_and_premium_entitlement_agree_on_identity(client: ASGITestClient):
    """구독을 켠 뒤 프리미엄 자산 조회가 곧바로 entitled 로 보여야 한다."""
    client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth())

    a = client.get("/api/v1/pet/premium/assets?pet_id=p1", headers=_auth())
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["entitled"] is True, "구독을 켰는데 프리미엄이 구독 없음으로 읽힌다"
    assert body["subscription_status"] == "active"


def test_mixed_case_email_token_resolves_to_the_same_subscription(client: ASGITestClient):
    """대문자 이메일로 로그인해도 같은 구독을 본다."""
    client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth())

    mixed = {"Authorization": f"Bearer {_token(SUB_A, 'ALICE@Example.com')}"}
    s = client.get("/api/v1/subscription/status", headers=mixed)
    assert s.status_code == 200, s.text
    assert s.json()["entitled"] is True


def test_store_webhook_user_id_is_canonicalised(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    """스토어가 대문자 이메일을 줘도 정규 신원으로 저장된다."""
    monkeypatch.setenv("SUBSCRIPTION_WEBHOOK_SECRET", HOOK_SECRET)
    r = client.post(
        "/api/v1/subscription/webhook",
        json={
            "store_type": "apple",
            "notification_type": "INITIAL_BUY",
            "user_id": "ALICE@Example.com",
            "plan_id": "standard_subscription",
            "transaction_id": "tx_store_1",
        },
        headers={"X-Subscription-Webhook-Secret": HOOK_SECRET},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == EMAIL

    a = client.get("/api/v1/pet/premium/assets?pet_id=p1", headers=_auth())
    assert a.json()["entitled"] is True, "스토어 결제가 인가로 이어지지 않았다"


# ── 2. 웹훅 인가 ──────────────────────────────────────────────────────────────


def test_unauthenticated_mock_webhook_is_rejected(client: ASGITestClient):
    """예전에는 이게 200 이었다 — 아무나 구독을 켤 수 있었다."""
    r = client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"))
    assert r.status_code == 401
    assert sub_store._MOCK_SUBS == {}, "거절됐는데 구독이 생성됐다"


def test_bad_token_mock_webhook_is_rejected(client: ASGITestClient):
    r = client.post(
        "/api/v1/subscription/webhook",
        json=_mock_event("INITIAL_BUY"),
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401
    assert sub_store._MOCK_SUBS == {}


def test_store_webhook_without_secret_is_rejected(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    """**Phase 2 의 실제 프로덕션 구멍**: store_type=apple 이면 무인증으로 통과했다."""
    monkeypatch.setenv("SUBSCRIPTION_WEBHOOK_SECRET", HOOK_SECRET)
    r = client.post(
        "/api/v1/subscription/webhook",
        json={
            "store_type": "apple",
            "notification_type": "INITIAL_BUY",
            "user_id": "victim@example.com",
            "transaction_id": "tx_attack",
        },
    )
    assert r.status_code == 401
    assert sub_store._MOCK_SUBS == {}, "무인증 요청이 남의 구독을 켰다"


def test_store_webhook_with_wrong_secret_is_rejected(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SUBSCRIPTION_WEBHOOK_SECRET", HOOK_SECRET)
    r = client.post(
        "/api/v1/subscription/webhook",
        json={
            "store_type": "apple",
            "notification_type": "INITIAL_BUY",
            "user_id": "victim@example.com",
            "transaction_id": "tx_attack_2",
        },
        headers={"X-Subscription-Webhook-Secret": "wrong"},
    )
    assert r.status_code == 401
    assert sub_store._MOCK_SUBS == {}


def test_missing_secret_config_closes_instead_of_opening(client: ASGITestClient):
    """SUBSCRIPTION_WEBHOOK_SECRET 미설정 → 503. 설정 누락이 무인증이 되면 안 된다."""
    r = client.post(
        "/api/v1/subscription/webhook",
        json={
            "store_type": "google",
            "notification_type": "INITIAL_BUY",
            "user_id": "victim@example.com",
            "transaction_id": "tx_noconf",
        },
        headers={"X-Subscription-Webhook-Secret": "anything"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "WEBHOOK_NOT_CONFIGURED"
    assert sub_store._MOCK_SUBS == {}


def test_omitting_store_type_cannot_bypass_the_store_secret(client: ASGITestClient):
    """
    store_type 을 빼면 파서가 mock 으로 떨어뜨린다. 그 경로도 인증을 요구하므로
    **시크릿 검사를 우회하는 수단이 되지 않는다.**
    """
    r = client.post(
        "/api/v1/subscription/webhook",
        json={"notification_type": "INITIAL_BUY", "user_id": "victim@example.com",
              "transaction_id": "tx_bypass"},
    )
    assert r.status_code == 401
    assert sub_store._MOCK_SUBS == {}


def test_mock_webhook_rejected_when_mock_disabled(
    client: ASGITestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "0")
    r = client.post(
        "/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth()
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "MOCK_DISABLED"


def test_subscription_mock_still_works_for_local_tests(client: ASGITestClient):
    """SUBSCRIPTION_MOCK=1 + 로그인 → 목업 전 이벤트가 그대로 동작한다."""
    for event, expected in (
        ("INITIAL_BUY", "active"),
        ("RENEWAL", "active"),
        ("CANCEL", "canceled"),
        ("EXPIRATION", "expired"),
    ):
        r = client.post(
            "/api/v1/subscription/webhook",
            json=_mock_event(event, tx=event),
            headers=_auth(),
        )
        assert r.status_code == 200, f"{event}: {r.text}"
        assert r.json()["subscription_status"] == expected


# ── 3. 상태 조회 ──────────────────────────────────────────────────────────────


def test_status_requires_authentication(client: ASGITestClient):
    assert client.get("/api/v1/subscription/status").status_code == 401


def test_legacy_status_path_requires_authentication(client: ASGITestClient):
    """예전에는 인증 없이 남의 구독 상태를 읽을 수 있었다."""
    r = client.get(f"/api/v1/subscription/status/{EMAIL}")
    assert r.status_code == 401


def test_legacy_status_path_rejects_other_users(client: ASGITestClient):
    r = client.get("/api/v1/subscription/status/someone-else@example.com", headers=_auth())
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "IDENTITY_MISMATCH"


def test_legacy_status_path_allows_self_with_any_casing(client: ASGITestClient):
    client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth())
    r = client.get("/api/v1/subscription/status/ALICE@Example.com", headers=_auth())
    assert r.status_code == 200
    assert r.json()["entitled"] is True


def test_plans_stay_public(client: ASGITestClient):
    """가격표에는 사용자 데이터가 없다 — 로그인 전 화면이 읽어야 한다."""
    r = client.get("/api/v1/subscription/plans")
    assert r.status_code == 200
    assert r.json()["plans"]


# ── 4. 신원이 갈린 두 계정 ────────────────────────────────────────────────────


def test_one_users_subscription_does_not_entitle_another(client: ASGITestClient):
    client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth())

    other = _auth(SUB_B, "bob@example.com")
    a = client.get("/api/v1/pet/premium/assets?pet_id=p_bob", headers=other)
    assert a.status_code == 200
    assert a.json()["entitled"] is False, "남의 구독으로 인가를 받았다"


def test_entitlement_reads_the_same_key_the_webhook_wrote(client: ASGITestClient):
    """저장 키와 조회 키가 문자 단위로 같은지 직접 확인한다."""
    import anyio

    client.post("/api/v1/subscription/webhook", json=_mock_event("INITIAL_BUY"), headers=_auth())

    assert list(sub_store._MOCK_SUBS.keys()) == [EMAIL]
    # ASGITestClient 가 이미 anyio.run 을 쓰므로 async 테스트 안에서 섞지 않는다.
    ent = anyio.run(lambda: premium_entitlement.get_entitlement(EMAIL))
    assert ent.entitled is True
