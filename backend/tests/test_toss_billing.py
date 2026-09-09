"""
Phase 8 — Toss 웹 정기결제.

    Start Membership → Toss 결제 → 검증된 백엔드 결제 → 기존 자격 ACTIVE

핵심 계약:
  * 제공자는 자격을 **직접 쓰지 않는다** — 정규화된 이벤트만 보낸다.
  * 첫 결제는 멱등하다 (성공 페이지 새로고침이 두 번 청구하지 않는다).
  * 갱신 성공은 기간을 **한 번만** 연장한다.
  * 갱신 실패는 **연장하지 않는다** (자격이 거짓으로 살아 있지 않다).
  * 해지는 기간 끝까지 ACTIVE, 그 뒤 EXPIRED.
  * 만료가 READY 자산·선호를 지우지 않는다.
  * 구매/갱신이 생성을 일으키지 않는다.
  * 크레딧을 건드리지 않는다.
"""

from __future__ import annotations

import datetime

import jwt
import pytest
from fastapi import FastAPI

from backend.routers import billing_v1, premium_v1
from backend.services import (
    behavior_preferences as prefs_svc,
    billing_service,
    billing_store,
    identity_service,
    premium_entitlement,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    toss_billing,
    wallet_service,
)

from .conftest import ASGITestClient

SECRET = "test-jwt-secret-value-long-enough-32b"
CRON_SECRET = "cron-secret-value"
EMAIL = "buyer@example.com"
SUB = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
OTHER_SUB = "ffffffff-ffff-ffff-ffff-ffffffffffff"
PET = "buyer_pet"
PRICE = 9900


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv("TOSS_CLIENT_KEY", "test_ck_dummy")
    monkeypatch.setenv("TOSS_SECRET_KEY", "test_sk_dummy")
    monkeypatch.setenv("BILLING_CRON_SECRET", CRON_SECRET)
    monkeypatch.delenv("SUBSCRIPTION_MOCK", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_TEST_AUTH", raising=False)
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    for m in (billing_store, identity_service, premium_purchase, prefs_svc):
        getattr(m, "__reset_for_tests", lambda: None)()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    yield
    for m in (billing_store, identity_service, premium_purchase, prefs_svc):
        getattr(m, "__reset_for_tests", lambda: None)()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> ASGITestClient:
    submitted: list[str] = []

    async def spy(*, action_id, **kw):
        submitted.append(action_id)
        raise premium_generation.PremiumSubmitError("no provider in tests", stage="test")

    monkeypatch.setattr(premium_generation, "submit_premium_action", spy)

    app = FastAPI()
    app.include_router(billing_v1.router, prefix="/api")
    app.include_router(premium_v1.router, prefix="/api")
    c = ASGITestClient(app)
    c.submitted = submitted  # type: ignore[attr-defined]
    return c


def _auth(sub: str = SUB, email: str = EMAIL) -> dict[str, str]:
    tok = jwt.encode(
        {"sub": sub, "email": email, "email_verified": True, "aud": "authenticated",
         "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def _checkout(c: ASGITestClient, headers=None) -> dict:
    r = c.post("/api/v1/billing/checkout", headers=headers or _auth())
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(c: ASGITestClient, co: dict, headers=None, auth_key="authkey_1"):
    return c.post(
        "/api/v1/billing/confirm",
        json={"auth_key": auth_key, "customer_key": co["customer_key"],
              "order_id": co["order_id"], "plan_id": co["plan_id"]},
        headers=headers or _auth(),
    )


def _entitled(c: ASGITestClient, headers=None) -> bool:
    r = c.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=headers or _auth())
    assert r.status_code == 200, r.text
    return r.json()["entitled"]


def _buy(c: ASGITestClient, headers=None):
    co = _checkout(c, headers)
    r = _confirm(c, co, headers)
    assert r.status_code == 200, r.text
    return co, r.json()


# ── 설정 · 시크릿 노출 ───────────────────────────────────────────────────────


def test_config_exposes_public_key_only(client):
    r = client.get("/api/v1/billing/config")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["client_key"] == "test_ck_dummy"
    assert "test_sk_dummy" not in r.text, "시크릿 키가 응답에 실렸다"


def test_secret_key_never_appears_in_any_response(client):
    co, out = _buy(client)
    for r in (
        client.post("/api/v1/billing/checkout", headers=_auth()),
        client.get("/api/v1/billing/status", headers=_auth()),
        client.get("/api/v1/billing/config"),
    ):
        assert "test_sk_dummy" not in r.text, "시크릿이 노출됐다"
        assert "billing_key" not in r.text or "mock_bk_" not in r.text, "billingKey 가 노출됐다"


def test_test_mode_is_reported(client):
    assert client.get("/api/v1/billing/config").json()["test_mode"] is True


# ── 인증 · 신원 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/checkout", "/confirm", "/cancel", "/resume"])
def test_billing_mutations_require_authentication(client, path):
    r = client.post(f"/api/v1/billing{path}", json={})
    assert r.status_code == 401


def test_status_requires_authentication(client):
    assert client.get("/api/v1/billing/status").status_code == 401


def test_cannot_confirm_with_another_users_customer_key(client):
    co = _checkout(client)  # 사용자 A 가 만든 주문
    other = _auth(OTHER_SUB, "other@example.com")
    r = _confirm(client, co, headers=other)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CUSTOMER_KEY_MISMATCH"


def test_customer_key_does_not_leak_user_identity(client):
    co = _checkout(client)
    assert EMAIL not in co["customer_key"], "결제창 URL 에 사용자 이메일이 실린다"
    assert co["customer_key"].startswith("eb_")


# ── 첫 결제 → ACTIVE ─────────────────────────────────────────────────────────


def test_start_membership_activates_entitlement(client):
    assert _entitled(client) is False
    _, out = _buy(client)
    assert out["entitled"] is True
    assert out["subscription_status"] == "active"
    assert _entitled(client) is True, "결제했는데 자격이 살아나지 않았다"


def test_checkout_alone_does_not_grant_entitlement(client):
    """결제창을 열기만 해서는 자격이 생기지 않는다."""
    _checkout(client)
    assert _entitled(client) is False


def test_checkout_amount_matches_plan_price(client):
    co = _checkout(client)
    assert co["amount"] == PRICE


def test_confirm_is_idempotent(client):
    co = _checkout(client)
    first = _confirm(client, co)
    second = _confirm(client, co)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["already_processed"] is False
    assert second.json()["already_processed"] is True, "새로고침이 두 번 청구했다"
    assert _entitled(client) is True


def test_failed_first_payment_does_not_activate(client, monkeypatch: pytest.MonkeyPatch):
    async def failing(**kw):
        return toss_billing.ChargeResult(
            ok=False, payment_key=None, order_id=kw["order_id"], amount=kw["amount"],
            raw={}, failure_code="REJECT_CARD", failure_message="카드 거절",
        )

    monkeypatch.setattr(toss_billing, "charge", failing)
    co = _checkout(client)
    r = _confirm(client, co)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "PAYMENT_FAILED"
    assert _entitled(client) is False, "실패한 결제로 자격이 생겼다"


# ── 갱신 ─────────────────────────────────────────────────────────────────────


def _force_due(user_id: str):
    sub = billing_store._MOCK_SUBS[(user_id, "toss")]
    sub.current_period_end = billing_store.now_utc() - datetime.timedelta(seconds=1)


def _renew(client, secret=CRON_SECRET):
    return client.post(
        "/api/v1/billing/renew-due", headers={"X-Billing-Cron-Secret": secret}
    )


def test_renewal_extends_the_period_once(client):
    _buy(client)
    _force_due(EMAIL)
    before = billing_store._MOCK_SUBS[(EMAIL, "toss")].current_period_end

    r = _renew(client)
    assert r.status_code == 200
    assert r.json()["results"][0]["outcome"] == "renewed"
    after = billing_store._MOCK_SUBS[(EMAIL, "toss")].current_period_end
    assert after > before
    assert _entitled(client) is True

    # 두 번째 배치는 아직 기간이 남아 대상이 아니다 — 이중 연장 방지.
    assert _renew(client).json()["processed"] == 0, "기간이 남았는데 또 청구했다"


def test_failed_renewal_does_not_extend_entitlement(client, monkeypatch: pytest.MonkeyPatch):
    _buy(client)
    assert _entitled(client) is True
    _force_due(EMAIL)
    before = billing_store._MOCK_SUBS[(EMAIL, "toss")].current_period_end

    async def failing(**kw):
        return toss_billing.ChargeResult(
            ok=False, payment_key=None, order_id=kw["order_id"], amount=kw["amount"],
            raw={}, failure_code="INSUFFICIENT_FUNDS", failure_message="잔액 부족",
        )

    monkeypatch.setattr(toss_billing, "charge", failing)
    r = _renew(client)

    assert r.json()["results"][0]["outcome"] == "failed"
    after = billing_store._MOCK_SUBS[(EMAIL, "toss")].current_period_end
    assert after == before, "실패했는데 기간이 늘었다"
    assert _entitled(client) is False, "실패한 갱신으로 자격이 유지됐다"


def test_renew_requires_cron_secret(client):
    assert client.post("/api/v1/billing/renew-due").status_code == 401
    assert _renew(client, secret="wrong").status_code == 401


def test_renew_closes_when_cron_secret_unset(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BILLING_CRON_SECRET", raising=False)
    r = _renew(client, secret="anything")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "CRON_NOT_CONFIGURED"


# ── 해지 → 기간 끝까지 ACTIVE → EXPIRED ──────────────────────────────────────


def test_cancel_keeps_entitlement_until_period_end(client):
    _buy(client)
    r = client.post("/api/v1/billing/cancel", headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["billing"]["cancel_at_period_end"] is True
    assert r.json()["entitled"] is True, "해지 즉시 끊겼다 — 낸 기간은 써야 한다"
    assert _entitled(client) is True


def test_cancel_then_period_end_expires(client):
    _buy(client)
    client.post("/api/v1/billing/cancel", headers=_auth())
    _force_due(EMAIL)

    r = _renew(client)
    assert r.json()["results"][0]["outcome"] == "expired_after_cancel"
    assert _entitled(client) is False, "기간이 끝났는데 자격이 남아 있다"


def test_canceled_subscription_is_not_charged_again(client, monkeypatch: pytest.MonkeyPatch):
    _buy(client)
    client.post("/api/v1/billing/cancel", headers=_auth())
    _force_due(EMAIL)

    charged: list[str] = []
    real = toss_billing.charge

    async def spy(**kw):
        charged.append(kw["order_id"])
        return await real(**kw)

    monkeypatch.setattr(toss_billing, "charge", spy)
    _renew(client)
    assert charged == [], "해지 예약분에 청구했다"


def test_resume_before_period_end_restores_active(client):
    _buy(client)
    client.post("/api/v1/billing/cancel", headers=_auth())
    r = client.post("/api/v1/billing/resume", headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["billing"]["cancel_at_period_end"] is False
    assert _entitled(client) is True


def test_cancel_without_subscription_is_rejected(client):
    r = client.post("/api/v1/billing/cancel", headers=_auth())
    assert r.status_code == 404


# ── 복원 / 상태 ──────────────────────────────────────────────────────────────


def test_status_restores_on_another_device(client):
    """웹에는 스토어식 복원이 없다 — 같은 계정으로 로그인하면 그대로 돌아온다."""
    _buy(client)
    r = client.get("/api/v1/billing/status", headers=_auth())
    assert r.status_code == 200
    assert r.json()["billing"]["status"] == "active"
    assert r.json()["billing"]["has_payment_method"] is True
    assert "billing_key" not in str(r.json()["billing"])


def test_status_is_empty_for_a_different_user(client):
    _buy(client)
    other = _auth(OTHER_SUB, "other@example.com")
    assert client.get("/api/v1/billing/status", headers=other).json()["billing"] is None
    assert _entitled(client, headers=other) is False


# ── 자산·선호·크레딧 불변 ────────────────────────────────────────────────────


def test_purchase_never_triggers_generation(client):
    _buy(client)
    _force_due(EMAIL)
    _renew(client)
    assert client.submitted == [], "결제/갱신이 생성을 제출했다"


def test_expiry_preserves_preferences(client):
    _buy(client)
    client.put(
        "/api/v1/pet/premium/preference",
        json={"pet_id": PET, "action_id": "BLINKING", "enabled": False},
        headers=_auth(),
    )
    client.post("/api/v1/billing/cancel", headers=_auth())
    _force_due(EMAIL)
    _renew(client)

    assert _entitled(client) is False
    body = client.get(f"/api/v1/pet/premium/assets?pet_id={PET}", headers=_auth()).json()
    assert body["preferences"]["BLINKING"] is False, "만료가 선호를 지웠다"


def test_billing_never_touches_the_credit_wallet(client):
    _buy(client)
    _force_due(EMAIL)
    _renew(client)
    w = wallet_service._MOCK_WALLETS.get(EMAIL)
    # Toss 경로는 지갑을 만들지도, 크레딧을 주지도 않는다.
    assert w is None or w.current_credits == 0, "웹 결제가 소비자 크레딧을 발행했다"


def test_billing_service_cannot_reach_generation():
    """구조적 보장 — 생성 모듈을 import 하지 않는다."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(billing_service))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    for forbidden in ("premium_generation", "generation_queue", "video_generation",
                      "premium_purchase", "wallet_service"):
        assert forbidden not in imported, f"청구 서비스가 {forbidden} 에 닿는다"


# ── 제공자 중립성 ────────────────────────────────────────────────────────────


def test_entitlement_core_is_provider_agnostic():
    """Toss 이벤트가 Apple/Google 과 **같은 코어**를 탄다."""
    from backend.services import billing_events
    from backend.services.subscription_webhook_parser import (
        is_cancel_event, is_expiration_event, is_renewal_event,
    )

    assert is_renewal_event("INITIAL_BUY") and is_renewal_event("RENEWAL")
    assert is_cancel_event("CANCEL")
    assert is_expiration_event("EXPIRATION")
    # 실패한 갱신이 만료 계열이어야 "실패했는데 연장" 이 불가능하다.
    assert is_expiration_event("DID_FAIL_TO_RENEW")
    assert not is_renewal_event("DID_FAIL_TO_RENEW"), "갱신 실패가 갱신으로 분류된다"
    assert set(billing_events.NormalizedSubscriptionEvent.__annotations__) >= {
        "provider", "event_type", "user_id", "plan_id", "transaction_id",
    }


def test_provider_is_recorded_but_not_used_for_authorization():
    """자격 판정은 제공자를 보지 않는다 — Apple 이 붙어도 판정이 갈라지지 않는다."""
    import ast
    import inspect

    src = inspect.getsource(premium_entitlement)
    assert "toss" not in src.lower()
    assert "provider" not in src.lower(), "자격 판정이 제공자를 안다"
    _ = ast.parse(src)


def test_toss_events_reach_the_same_subscription_table(client):
    _buy(client)
    assert EMAIL in sub_store._MOCK_SUBS, "Toss 결제가 기존 자격 테이블에 반영되지 않았다"
    assert sub_store._MOCK_SUBS[EMAIL].status == "active"
