"""
테마 일회성 결제 — **구독한 적 없는 사용자도 살 수 있다.**

예전에는 저장된 카드(billing_key)가 **필수**였고, 그건 멤버십 체크아웃에서만
만들어졌다. 결과적으로 "테마를 사려면 먼저 구독 흐름을 타야 한다"가 되어,
일회성 구매라는 성격과 어긋났고 두 축의 분리도 반쪽이었다.

이 파일이 고정하는 것:
  * **한 번도 구독한 적 없는 사용자** → 체크아웃 → 승인 → OWNED.
  * 저장된 카드는 단축키일 뿐 전제가 아니다.
  * 금액은 **서버가 보관한 주문**이 정본이다 — URL 을 고쳐도 바뀌지 않는다.
  * 남의 주문을 확인할 수 없다.
  * 멱등: 같은 주문을 다시 확인해도 재승인되지 않는다.
  * 성공하면 만들어지는 것은 **테마 소유권 한 줄뿐** — 구독도 크레딧도 아니다.
"""

from __future__ import annotations

import functools

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import theme_store_v1
from backend.services import (
    theme_catalog,
    theme_entitlement,
    theme_order,
    theme_purchase,
    toss_billing,
)

from .conftest import ASGITestClient

#: 구독도 카드도 **한 번도** 가진 적 없는 사용자.
NEWCOMER = "newcomer@example.com"
OTHER = "other@example.com"
PAID = "aurora"
PRICE = 4900


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    monkeypatch.setenv("TOSS_MOCK", "1")
    monkeypatch.setenv(f"THEME_PRICE_{PAID.upper()}_KRW", str(PRICE))
    monkeypatch.delenv("THEME_PAID_KEYS", raising=False)
    monkeypatch.delenv("THEME_ENTITLEMENT_TTL_DAYS", raising=False)
    theme_entitlement.__reset_for_tests()
    theme_order.__reset_for_tests()
    yield
    theme_entitlement.__reset_for_tests()
    theme_order.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(theme_store_v1.router, prefix="/api")
    return ASGITestClient(app)


@pytest.fixture(autouse=True)
def _no_saved_card(monkeypatch: pytest.MonkeyPatch):
    """
    저장된 카드가 **전혀 없는** 상태. 구독 이력이 없으므로 billing_store 도 비어 있다.

    이것이 이 파일의 전제다 — 이 상태에서 구매가 끝까지 되어야 한다.
    """
    from backend.services import billing_store

    async def _none(_uid, _provider):
        return None

    monkeypatch.setattr(billing_store, "get_subscription", _none)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _checkout(client: ASGITestClient, user: str = NEWCOMER, theme: str = PAID):
    return client.post(
        "/api/v1/themes/checkout", json={"theme_key": theme}, headers=_auth(user)
    )


def _confirm(client: ASGITestClient, order_id: str, *, user: str = NEWCOMER,
             payment_key: str = "pk_test", amount: int | None = None):
    body: dict = {"payment_key": payment_key, "order_id": order_id}
    if amount is not None:
        body["amount"] = amount
    return client.post("/api/v1/themes/confirm", json=body, headers=_auth(user))


def _catalog(client: ASGITestClient, user: str = NEWCOMER) -> dict[str, dict]:
    r = client.get("/api/v1/themes/catalog", headers=_auth(user))
    assert r.status_code == 200, r.text
    return {t["theme_key"]: t for t in r.json()["themes"]}


# ── 핵심: 구독한 적 없음 → 구매 → OWNED ─────────────────────────────────────


def test_never_subscribed_user_can_buy_a_theme(client: ASGITestClient):
    """
    **핵심 회귀**: 구독한 적도, 카드를 등록한 적도 없는 사용자가 테마를 산다.

    NOT OWNED → 체크아웃 → 결제창 승인 → confirm → OWNED.
    """
    assert _catalog(client)[PAID]["owned"] is False

    co = _checkout(client)
    assert co.status_code == 200, co.text
    order = co.json()
    assert order["amount"] == PRICE
    assert order["order_id"]
    # 아직 아무 돈도 움직이지 않았다.
    assert _catalog(client)[PAID]["owned"] is False
    assert _sync(theme_entitlement.list_entitlements, NEWCOMER) == []

    done = _confirm(client, order["order_id"], amount=PRICE)
    assert done.status_code == 200, done.text
    b = done.json()
    assert b["status"] == "owned"
    assert b["charged"] == PRICE
    assert b["already_owned"] is False

    assert _catalog(client)[PAID]["owned"] is True


def test_checkout_does_not_require_a_saved_card(client: ASGITestClient):
    """체크아웃은 카드 유무를 **묻지도 않는다**."""
    assert _checkout(client).status_code == 200


def test_saved_card_path_reports_fallback_not_failure(client: ASGITestClient):
    """
    카드가 없을 때 /purchase 는 **안내**를 준다 — "살 수 없다"가 아니다.

    프론트는 이 코드를 보고 결제창 경로로 넘어간다.
    """
    r = client.post(
        "/api/v1/themes/purchase", json={"theme_key": PAID}, headers=_auth(NEWCOMER)
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYMENT_METHOD_UNAVAILABLE"


def test_purchase_grants_only_theme_entitlement(client: ASGITestClient):
    """
    **핵심 계약**: 성공한 결제가 만드는 것은 테마 소유권 한 줄뿐이다.

    구독도 크레딧도 생기거나 바뀌지 않는다.
    """
    from backend.services import subscription_store_service as sub_store
    from backend.services import wallet_service

    subs_before = dict(sub_store._MOCK_SUBS)
    wallets_before = dict(wallet_service._MOCK_WALLETS)

    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)

    assert sub_store._MOCK_SUBS == subs_before, "구독 상태가 바뀌었다"
    assert wallet_service._MOCK_WALLETS == wallets_before, "지갑이 바뀌었다"

    ents = _sync(theme_entitlement.list_entitlements, NEWCOMER)
    assert len(ents) == 1
    assert ents[0].theme_key == PAID
    assert ents[0].status == "owned"


def test_never_subscribed_buyer_is_still_not_a_member(client: ASGITestClient):
    """테마를 샀다고 회원이 되지 않는다 — 다른 축이다."""
    from backend.services import premium_entitlement

    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)

    ent = _sync(premium_entitlement.get_entitlement, NEWCOMER)
    assert ent.entitled is False
    assert ent.status is None


# ── 금액 위조 방어 ───────────────────────────────────────────────────────────


def test_amount_from_redirect_cannot_lower_the_price(client: ASGITestClient):
    """
    **핵심 회귀**: URL 의 amount 를 고쳐도 싸게 살 수 없다.

    리다이렉트 파라미터는 주소창에 있다. 그것을 승인 기준으로 쓰면 1원 결제로
    유료 테마를 살 수 있다.
    """
    order = _checkout(client).json()

    r = _confirm(client, order["order_id"], amount=1)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "THEME_AMOUNT_MISMATCH"
    assert _catalog(client)[PAID]["owned"] is False


def test_mismatch_does_not_brick_the_order(client: ASGITestClient):
    """
    금액 불일치는 주문을 **죽이지 않는다.**

    실측으로 드러난 결함: 불일치 시 주문을 failed 로 표시했더니, 그 뒤의 **정당한**
    확인이 THEME_ORDER_NOT_PENDING 으로 영구히 막혔다. 죽여서 얻는 보안은 없다 —
    틀린 금액으로는 어차피 승인되지 않는다 — 반면 정당한 재시도는 막힌다.
    """
    order = _checkout(client).json()
    assert _confirm(client, order["order_id"], amount=1).status_code == 400

    ok = _confirm(client, order["order_id"], amount=PRICE)
    assert ok.status_code == 200, ok.text
    assert ok.json()["charged"] == PRICE
    assert _catalog(client)[PAID]["owned"] is True


def test_confirm_asks_toss_with_the_stored_amount(client: ASGITestClient, monkeypatch):
    """Toss 에 묻는 금액은 **저장된 주문 금액**이다 (클라이언트 값이 아니다)."""
    seen: list[int] = []
    real = toss_billing.confirm_payment

    async def _spy(**kw):
        seen.append(kw["amount"])
        return await real(**kw)

    monkeypatch.setattr(toss_billing, "confirm_payment", _spy)

    order = _checkout(client).json()
    _confirm(client, order["order_id"])  # amount 를 아예 안 보낸다
    assert seen == [PRICE]


def test_amount_may_be_omitted(client: ASGITestClient):
    """대조값이 없어도 승인된다 — 기준은 어차피 서버 주문이다."""
    order = _checkout(client).json()
    assert _confirm(client, order["order_id"]).status_code == 200


# ── 주문 소유권 ──────────────────────────────────────────────────────────────


def test_cannot_confirm_someone_elses_order(client: ASGITestClient):
    """
    **핵심 회귀**: 주문 id 는 리다이렉트 URL 에 노출된다.

    소유자 검사가 없으면 남의 결제로 내 소유권을 만들 수 있다.
    """
    order = _checkout(client, user=NEWCOMER).json()

    r = _confirm(client, order["order_id"], user=OTHER, amount=PRICE)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "THEME_ORDER_NOT_FOUND"
    assert _sync(theme_entitlement.list_entitlements, OTHER) == []
    # 원래 주문자는 여전히 확인할 수 있다.
    assert _confirm(client, order["order_id"], amount=PRICE).status_code == 200


def test_unknown_order_is_rejected(client: ASGITestClient):
    r = _confirm(client, "eb_theme_doesnotexist")
    assert r.status_code == 404


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_confirming_twice_does_not_charge_twice(client: ASGITestClient):
    """새로고침·뒤로가기로 confirm 이 다시 와도 재승인하지 않는다."""
    order = _checkout(client).json()
    first = _confirm(client, order["order_id"], amount=PRICE).json()
    assert first["charged"] == PRICE

    for _ in range(3):
        again = _confirm(client, order["order_id"], amount=PRICE).json()
        assert again["charged"] == 0
        assert again["already_owned"] is True

    assert len(_sync(theme_entitlement.list_entitlements, NEWCOMER)) == 1


def test_repeat_confirm_never_calls_the_provider(client: ASGITestClient, monkeypatch):
    """두 번째 확인은 결제사를 **호출조차 하지 않는다**."""
    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)

    calls: list[str] = []

    async def _tracked(**kw):
        calls.append(kw["order_id"])
        raise AssertionError("이미 확정된 주문에 재승인을 시도했다")

    monkeypatch.setattr(toss_billing, "confirm_payment", _tracked)
    assert _confirm(client, order["order_id"], amount=PRICE).json()["charged"] == 0
    assert calls == []


def test_checkout_twice_reuses_the_pending_order(client: ASGITestClient):
    """
    체크아웃을 두 번 눌러도 주문이 쌓이지 않는다.

    쌓이면 예전 탭의 결제창을 승인했을 때 어느 주문이 유효한지 모호해진다.
    """
    a = _checkout(client).json()["order_id"]
    b = _checkout(client).json()["order_id"]
    assert a == b


def test_owned_theme_cannot_be_checked_out_again(client: ASGITestClient):
    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)

    r = _checkout(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_ALREADY_OWNED"


# ── 실패 경로 ────────────────────────────────────────────────────────────────


def test_failed_confirmation_grants_nothing(client: ASGITestClient, monkeypatch):
    order = _checkout(client).json()

    async def _fail(**kw):
        return toss_billing.ConfirmResult(
            ok=False, payment_key=kw["payment_key"], order_id=kw["order_id"],
            amount=kw["amount"], raw={}, failure_code="PAY_PROCESS_CANCELED",
            failure_message="사용자가 취소했습니다",
        )

    monkeypatch.setattr(toss_billing, "confirm_payment", _fail)

    r = _confirm(client, order["order_id"], amount=PRICE)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "THEME_PAYMENT_FAILED"
    assert _catalog(client)[PAID]["owned"] is False

    # 실패한 주문은 다시 확인할 수 없다 — 새 체크아웃이 필요하다.
    retry = _confirm(client, order["order_id"], amount=PRICE)
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "THEME_ORDER_NOT_PENDING"


def test_free_theme_cannot_be_checked_out(client: ASGITestClient):
    r = _checkout(client, theme="fresh_forest")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_IS_FREE"


def test_unpriced_theme_cannot_be_checked_out(client: ASGITestClient):
    r = _checkout(client, theme="sunset")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "THEME_PRICE_NOT_SET"


def test_checkout_and_confirm_require_auth(client: ASGITestClient):
    assert client.post("/api/v1/themes/checkout", json={"theme_key": PAID}).status_code == 401
    assert client.post(
        "/api/v1/themes/confirm", json={"payment_key": "p", "order_id": "o"}
    ).status_code == 401


# ── 저장된 카드가 있을 때는 단축키가 살아 있다 ──────────────────────────────


def test_saved_card_shortcut_still_works(client: ASGITestClient, monkeypatch):
    """
    카드가 있으면 결제창 없이 즉시 구매된다 — 단축키는 없애지 않았다.

    구독 상태는 여전히 보지 않는다 (아래 status 는 일부러 expired 다).
    """
    from backend.services import billing_store

    class FakeSub:
        billing_key = "bk_test"
        customer_key = "ck_test"
        status = "expired"

    async def _get(_uid, _provider):
        return FakeSub()

    monkeypatch.setattr(billing_store, "get_subscription", _get)

    r = client.post(
        "/api/v1/themes/purchase", json={"theme_key": PAID}, headers=_auth(NEWCOMER)
    )
    assert r.status_code == 200
    assert r.json()["charged"] == PRICE
    assert _catalog(client)[PAID]["owned"] is True


def test_both_paths_produce_the_same_ownership_shape(client: ASGITestClient, monkeypatch):
    """
    두 경로가 **같은 모양의 소유권**을 만든다.

    부여 함수를 공유하지 않으면 한쪽만 provider 가 빠지거나 TTL 규칙이 달라진다.
    """
    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)
    via_checkout = _sync(theme_entitlement.list_entitlements, NEWCOMER)[0]

    from backend.services import billing_store

    class FakeSub:
        billing_key = "bk_test"
        customer_key = "ck_test"

    async def _get(_uid, _provider):
        return FakeSub()

    monkeypatch.setattr(billing_store, "get_subscription", _get)
    client.post("/api/v1/themes/purchase", json={"theme_key": PAID}, headers=_auth(OTHER))
    via_card = _sync(theme_entitlement.list_entitlements, OTHER)[0]

    assert via_checkout.provider == via_card.provider == "toss"
    assert via_checkout.currency == via_card.currency == theme_catalog.CURRENCY
    assert via_checkout.status == via_card.status == "owned"
    assert via_checkout.amount == via_card.amount == PRICE
    assert (via_checkout.expires_at is None) == (via_card.expires_at is None)


# ── 생성 금지 ────────────────────────────────────────────────────────────────


def test_standalone_payment_never_generates(client: ASGITestClient, monkeypatch):
    from backend.services import (
        generation_queue,
        premium_generation,
        premium_purchase,
        wallet_service,
    )

    fired: list[str] = []

    def _explode(name: str):
        async def _boom(*_a, **_k):
            fired.append(name)
            raise AssertionError(f"{name} 호출됨 — 테마 결제는 생성하지 않는다")

        return _boom

    for mod, attr in (
        (premium_purchase, "purchase"),
        (premium_generation, "submit_action"),
        (generation_queue, "enqueue_generation"),
        (wallet_service, "deduct_credits"),
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    order = _checkout(client).json()
    _confirm(client, order["order_id"], amount=PRICE)
    _catalog(client)
    assert fired == []


def test_order_module_is_independent_of_subscription_and_generation():
    """구조로 고정 — 주문 모듈이 구독·생성 모듈을 import 하지 않는다."""
    import ast

    forbidden = {
        "premium_entitlement", "subscription_store_service", "premium_generation",
        "generation_queue", "wallet_service", "credit_generation_service",
        "premium_purchase",
    }
    for path in ("backend/services/theme_order.py", "backend/services/theme_purchase.py"):
        tree = ast.parse(open(path, encoding="utf-8").read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.rsplit(".", 1)[-1])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported.add(a.name)
                if node.module:
                    imported.add(node.module.rsplit(".", 1)[-1])
        assert not (imported & forbidden), f"{path}: {imported & forbidden}"
