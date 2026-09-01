"""
레거시 테마 결제의 **은퇴와 드레인** (Phase 11).

    은퇴한 것   PayPal 전부 · POST /themes/purchase · POST /themes/checkout
                (= 새 KRW 주문을 만들 수 있는 모든 경로)
    남긴 것     POST /themes/confirm  — 드레인 창구
                purchased_slots / theme_purchase_orders 표 — 과거 결제 증거

── 왜 /confirm 만 남기는가 ──────────────────────────────────────────────────
배포하는 순간 Toss 결제창을 띄워 둔 고객이 있을 수 있다. 그 사람이 [승인] 을
누르면 **돈은 나간다.** 받아 줄 곳이 없으면 결제만 되고 테마는 못 받는다.
새 주문이 만들어지지 않으므로 미결 주문은 시간이 지나면 0 이 되고, 그때 이
경로도 표 동결과 함께 사라진다.

이 파일은 두 가지를 동시에 고정한다:

    1. 드레인 경로가 **여전히 옳게 동작한다** (금액 위조 방어·소유자 검사·멱등)
       — 은퇴시킨다고 반쯤 망가진 채로 남겨 두면 그게 제일 나쁘다.
    2. 은퇴한 경로가 **돌아오지 않는다** (함수·라우트·파일의 부재)

⚠️ 이 파일은 test_theme_standalone_payment.py 를 대체한다. 그 파일은 체크아웃
(주문 생성)을 전제로 쓰여 있었고, 그 전제가 Phase 11 에서 사라졌다. 확인 단계의
계약(금액 정본·소유자 검사·멱등·실패 시 무부여)은 **그대로 옮겨 왔다.**
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI

from backend.routers import theme_store_v1
from backend.services import (
    credit_ledger,
    product_catalog,
    theme_catalog,
    theme_entitlement,
    theme_order,
    theme_purchase,
    toss_billing,
    wallet_service,
)

from .conftest import ASGITestClient

REPO = Path(__file__).resolve().parents[2]

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
    monkeypatch.setenv("STARTER_CREDITS", "0")
    monkeypatch.delenv("THEME_PAID_KEYS", raising=False)
    monkeypatch.delenv("THEME_ENTITLEMENT_TTL_DAYS", raising=False)
    theme_entitlement.__reset_for_tests()
    theme_order.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    product_catalog.__reset_for_tests()
    yield
    theme_entitlement.__reset_for_tests()
    theme_order.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    product_catalog.__reset_for_tests()


@pytest.fixture
def client() -> ASGITestClient:
    app = FastAPI()
    app.include_router(theme_store_v1.router, prefix="/api")
    return ASGITestClient(app)


def _sync(afn, *args, **kwargs):
    return anyio.run(functools.partial(afn, *args, **kwargs))


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer test:{user}"}


def _in_flight(user: str = NEWCOMER, theme: str = PAID, amount: int = PRICE) -> str:
    """
    **배포 직전에 만들어진 미결 주문**을 재현한다.

    프로덕션에는 이제 이런 주문을 만드는 경로가 없다(그것이 은퇴의 내용이다).
    그래서 저장소에 직접 적는다 — 드레인 경로가 상대하는 것이 정확히 이 상태다.
    """
    order = _sync(
        theme_order.create,
        order_id=toss_billing.new_order_id("theme"),
        user_id=user,
        theme_key=theme,
        amount=amount,
        currency=theme_catalog.CURRENCY,
    )
    return order.order_id


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


# ── 드레인: 결제창에 머물러 있던 고객이 테마를 받는다 ───────────────────────


def test_an_in_flight_payment_still_delivers_the_theme(client: ASGITestClient):
    """
    **은퇴의 핵심 조건**: 이미 시작된 결제는 끝까지 간다.

    미결 주문 → 결제창 승인 → confirm → OWNED.
    """
    order_id = _in_flight()
    assert _catalog(client)[PAID]["owned"] is False

    done = _confirm(client, order_id, amount=PRICE)
    assert done.status_code == 200, done.text
    b = done.json()
    assert b["status"] == "owned"
    assert b["charged"] == PRICE
    assert b["already_owned"] is False

    assert _catalog(client)[PAID]["owned"] is True


def test_confirm_grants_only_a_theme_entitlement(client: ASGITestClient):
    """
    **핵심 계약**: 성공한 결제가 만드는 것은 테마 소유권 한 줄뿐이다.

    구독도 크레딧도 생기거나 바뀌지 않는다 — KRW 결제는 지갑과 무관하다.
    """
    from backend.services import subscription_store_service as sub_store

    subs_before = dict(sub_store._MOCK_SUBS)
    wallets_before = dict(wallet_service._MOCK_WALLETS)

    _confirm(client, _in_flight(), amount=PRICE)

    assert sub_store._MOCK_SUBS == subs_before, "구독 상태가 바뀌었다"
    assert wallet_service._MOCK_WALLETS == wallets_before, "지갑이 바뀌었다"

    ents = _sync(theme_entitlement.list_entitlements, NEWCOMER)
    assert len(ents) == 1
    assert ents[0].theme_key == PAID
    assert ents[0].status == "owned"


def test_a_krw_buyer_is_still_not_a_member(client: ASGITestClient):
    """테마를 샀다고 회원이 되지 않는다 — 다른 축이다."""
    from backend.services import premium_entitlement

    _confirm(client, _in_flight(), amount=PRICE)

    ent = _sync(premium_entitlement.get_entitlement, NEWCOMER)
    assert ent.entitled is False
    assert ent.status is None


def test_the_krw_ttl_setting_still_applies_to_this_path(
    client: ASGITestClient, monkeypatch
):
    """
    기간제 설정은 이 부여 경로의 것이다 — 켜져 있으면 만료일이 붙는다.

    (크레딧 구매는 이 설정과 무관하게 영구다 — test_theme_store.py 참고.
    두 경로의 규칙이 다르다는 사실 자체를 양쪽에서 고정해 둔다.)
    """
    monkeypatch.setenv("THEME_ENTITLEMENT_TTL_DAYS", "30")
    _confirm(client, _in_flight(), amount=PRICE)
    ents = _sync(theme_entitlement.list_entitlements, NEWCOMER)
    assert ents[0].expires_at is not None
    assert ents[0].active is True


# ── 금액 위조 방어 ───────────────────────────────────────────────────────────


def test_amount_from_redirect_cannot_lower_the_price(client: ASGITestClient):
    """
    **핵심 회귀**: URL 의 amount 를 고쳐도 싸게 살 수 없다.

    리다이렉트 파라미터는 주소창에 있다. 그것을 승인 기준으로 쓰면 1원 결제로
    유료 테마를 살 수 있다.
    """
    order_id = _in_flight()

    r = _confirm(client, order_id, amount=1)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "THEME_AMOUNT_MISMATCH"
    assert _catalog(client)[PAID]["owned"] is False


def test_mismatch_does_not_brick_the_order(client: ASGITestClient):
    """
    금액 불일치는 주문을 **죽이지 않는다.**

    실측으로 드러난 결함: 불일치 시 주문을 failed 로 표시했더니, 그 뒤의 **정당한**
    확인이 THEME_ORDER_NOT_PENDING 으로 영구히 막혔다. 죽여서 얻는 보안은 없다 —
    틀린 금액으로는 어차피 승인되지 않는다 — 반면 정당한 재시도는 막힌다.

    은퇴 뒤에는 더 중요해졌다: 새 체크아웃으로 다시 시작할 수가 없다.
    """
    order_id = _in_flight()
    assert _confirm(client, order_id, amount=1).status_code == 400

    ok = _confirm(client, order_id, amount=PRICE)
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

    _confirm(client, _in_flight())  # amount 를 아예 안 보낸다
    assert seen == [PRICE]


def test_amount_may_be_omitted(client: ASGITestClient):
    """대조값이 없어도 승인된다 — 기준은 어차피 서버 주문이다."""
    assert _confirm(client, _in_flight()).status_code == 200


# ── 주문 소유권 ──────────────────────────────────────────────────────────────


def test_cannot_confirm_someone_elses_order(client: ASGITestClient):
    """
    **핵심 회귀**: 주문 id 는 리다이렉트 URL 에 노출된다.

    소유자 검사가 없으면 남의 결제로 내 소유권을 만들 수 있다.
    """
    order_id = _in_flight(user=NEWCOMER)

    r = _confirm(client, order_id, user=OTHER, amount=PRICE)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "THEME_ORDER_NOT_FOUND"
    assert _sync(theme_entitlement.list_entitlements, OTHER) == []
    # 원래 주문자는 여전히 확인할 수 있다.
    assert _confirm(client, order_id, amount=PRICE).status_code == 200


def test_unknown_order_is_rejected(client: ASGITestClient):
    r = _confirm(client, "eb_theme_doesnotexist")
    assert r.status_code == 404


def test_confirm_requires_auth(client: ASGITestClient):
    assert client.post(
        "/api/v1/themes/confirm", json={"payment_key": "p", "order_id": "o"}
    ).status_code == 401


# ── 멱등성 ───────────────────────────────────────────────────────────────────


def test_confirming_twice_does_not_charge_twice(client: ASGITestClient):
    """새로고침·뒤로가기로 confirm 이 다시 와도 재승인하지 않는다."""
    order_id = _in_flight()
    first = _confirm(client, order_id, amount=PRICE).json()
    assert first["charged"] == PRICE

    for _ in range(3):
        again = _confirm(client, order_id, amount=PRICE).json()
        assert again["charged"] == 0
        assert again["already_owned"] is True

    assert len(_sync(theme_entitlement.list_entitlements, NEWCOMER)) == 1


def test_repeat_confirm_never_calls_the_provider(client: ASGITestClient, monkeypatch):
    """두 번째 확인은 결제사를 **호출조차 하지 않는다**."""
    order_id = _in_flight()
    _confirm(client, order_id, amount=PRICE)

    calls: list[str] = []

    async def _tracked(**kw):
        calls.append(kw["order_id"])
        raise AssertionError("이미 확정된 주문에 재승인을 시도했다")

    monkeypatch.setattr(toss_billing, "confirm_payment", _tracked)
    assert _confirm(client, order_id, amount=PRICE).json()["charged"] == 0
    assert calls == []


# ── 실패 경로 ────────────────────────────────────────────────────────────────


def test_failed_confirmation_grants_nothing(client: ASGITestClient, monkeypatch):
    order_id = _in_flight()

    async def _fail(**kw):
        return toss_billing.ConfirmResult(
            ok=False, payment_key=kw["payment_key"], order_id=kw["order_id"],
            amount=kw["amount"], raw={}, failure_code="PAY_PROCESS_CANCELED",
            failure_message="사용자가 취소했습니다",
        )

    monkeypatch.setattr(toss_billing, "confirm_payment", _fail)

    r = _confirm(client, order_id, amount=PRICE)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "THEME_PAYMENT_FAILED"
    assert _catalog(client)[PAID]["owned"] is False

    # 실패한 주문은 다시 확인할 수 없다.
    retry = _confirm(client, order_id, amount=PRICE)
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "THEME_ORDER_NOT_PENDING"


def test_the_drain_path_never_generates(client: ASGITestClient, monkeypatch):
    from backend.services import generation_queue, premium_generation, premium_purchase

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
    ):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _explode(f"{mod.__name__}.{attr}"))

    _confirm(client, _in_flight(), amount=PRICE)
    _catalog(client)
    assert fired == []


# ── 두 화폐가 같은 모양의 소유권을 만든다 ───────────────────────────────────


def test_both_currencies_produce_the_same_ownership_shape(client: ASGITestClient):
    """
    레거시 KRW 승인과 크레딧 구매가 **같은 표에 같은 모양**으로 남는다.

    소유권 표가 둘이 되거나 모양이 달라지면 "어느 쪽이 진짜인가"가 생긴다 —
    purchased_slots 가 이미 한 번 만들어 낸 문제다(docs/PAYPAL_LEGACY.md).
    """
    _confirm(client, _in_flight(user=NEWCOMER), amount=PRICE)
    via_krw = _sync(theme_entitlement.list_entitlements, NEWCOMER)[0]

    product_catalog.set_price_for_tests(
        product_catalog.theme_key(PAID), 5, product_catalog.TYPE_THEME
    )
    _sync(
        wallet_service.add_credits, OTHER, 20,
        reason=credit_ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="fund",
    )
    client.post(
        "/api/v1/themes/purchase-with-credits",
        json={"theme_key": PAID},
        headers=_auth(OTHER),
    )
    via_credits = _sync(theme_entitlement.list_entitlements, OTHER)[0]

    assert via_krw.theme_key == via_credits.theme_key == PAID
    assert via_krw.status == via_credits.status == "owned"
    assert via_krw.expires_at is None and via_credits.expires_at is None
    # 화폐는 다르게 **기록된다** — 통합한다고 과거 결제의 사실을 고쳐 쓰지 않는다.
    assert (via_krw.provider, via_krw.currency) == ("toss", theme_catalog.CURRENCY)
    assert (via_credits.provider, via_credits.currency) == ("credits", "CREDIT")


# ── 은퇴한 것이 돌아오지 않는다 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["purchase", "start_checkout", "saved_payment_method", "ThemeCheckout"]
)
def test_the_krw_purchase_starters_are_gone(name: str):
    """
    **주문을 만들 수 있는 코드가 남아 있으면 언젠가 다시 호출된다.**

    라우터에서만 떼어 내는 것으로는 부족하다 — 서비스 함수가 남아 있으면
    "임시로 하나만 열자"가 한 줄로 가능하고, 그러면 KRW·크레딧 두 가격이 동시에
    살아 있는 상태로 돌아간다.
    """
    assert not hasattr(theme_purchase, name), (
        f"theme_purchase.{name} 이 되살아났다 — KRW 직접 구매는 Phase 11 에서 은퇴했다"
    )


def test_no_production_module_creates_a_new_theme_order():
    """
    theme_order.create 는 **드레인 테스트를 위해** 남아 있다.

    프로덕션이 다시 부르는 순간 "새 KRW 주문이 만들어지지 않으므로 미결은 0 이
    된다"는 전제가 무너지고, 표 동결 계획도 함께 무너진다.
    """
    offenders = []
    for p in (REPO / "backend").rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts or "tests" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        # 정의부(theme_order.py 자신)는 `async def create` 라 아래 패턴에 안 걸린다.
        if "theme_order.create(" in text or "theme_order.find_reusable(" in text:
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, f"새 테마 주문을 만드는 프로덕션 코드가 있다: {offenders}"


@pytest.mark.parametrize(
    "path",
    [
        "backend/routers/paypal.py",
        "backend/services/paypal_service.py",
        "backend/models/paypal.py",
        "backend/services/theme_prices.py",
        "src/lib/paypal-api.ts",
        "src/lib/paypal-sdk.ts",
        "src/components/memorial/payment-screen.tsx",
    ],
)
def test_retired_files_stay_retired(path: str):
    """되살아나면 그것은 복원이 아니라 **두 번째 결제 시스템**이다."""
    assert not (REPO / path).exists(), f"{path} 가 돌아왔다 — docs/PAYPAL_LEGACY.md 참고"


def test_the_frontend_no_longer_carries_paypal_copy():
    """
    화면과 SDK 가 사라졌으므로 번역도 사라진다.

    남겨 두면 다음 사람이 "번역이 있으니 화면도 있겠지" 하고 찾는다.
    """
    src = (REPO / "src" / "components" / "memorial" / "memorial-i18n.ts").read_text(
        encoding="utf-8"
    )
    code = "\n".join(line.split("//", 1)[0] for line in src.splitlines())
    for key in ("payWithPaypal", "loadingPaypal", "paypalUnavailable", "securedByPaypal"):
        assert key not in code, f"{key} 번역이 남아 있다"


def test_the_frontend_client_cannot_start_a_krw_theme_purchase():
    """
    프론트에 남은 테마 API 는 카탈로그·크레딧 구매·(드레인) 확인뿐이다.

    체크아웃 호출이 남아 있으면 화면 하나만 되살려도 404 를 치는 결제 흐름이 된다.
    """
    src = (REPO / "src" / "lib" / "theme-store-api.ts").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in src.splitlines())
    assert "/purchase-with-credits" in code
    assert "themes/checkout" not in code
    # `/purchase` 단독 호출이 없다 — `/purchase-with-credits` 는 위에서 이미 봤다.
    assert 'themes/purchase"' not in code and "themes/purchase'" not in code


def test_the_freeze_migration_protects_evidence_without_blocking_the_drain():
    """
    **지시된 두 요구사항이 한 파일에서 만난다.**

        과거 구매 증거는 남긴다        → 표를 drop 하지 않는다
        레거시는 읽기 전용으로 둔다    → 쓰기를 트리거로 막는다

    단, theme_purchase_orders 는 아직 막지 않는다 — /confirm 이 pending → paid 로
    써야 한다. 여기서 잘못 막으면 결제만 되고 테마는 못 받는다.
    """
    sql = (
        REPO / "supabase" / "migrations"
        / "20261009000000_freeze_legacy_purchase_tables.sql"
    ).read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))

    assert "drop table" not in code.lower(), "과거 구매 증거를 지우고 있다"
    assert "create trigger purchased_slots_frozen" in code
    assert "create trigger theme_purchase_orders_frozen" not in code, (
        "드레인이 끝나기 전에 주문 표를 동결하면 결제만 되고 테마는 못 받는다"
    )


def test_the_freeze_migration_tolerates_a_database_without_the_legacy_tables():
    """
    **실측된 실패의 회귀 테스트.**

        ERROR: 42P01: relation "public.purchased_slots" does not exist

    두 표는 오래된 마이그레이션(20250302000000 / 20260821000100)이 만든다.
    그것을 적용한 적 없는 환경 — PayPal 시대를 겪지 않은 데이터베이스 — 에는
    표가 없고, 없는 표를 동결하는 것은 **오류가 아니라 할 일이 없는 것**이다.

    여기서 멈추면 뒤따르는 동결까지 함께 막혀 정작 존재하는 표가 열린 채로 남는다.
    반대로 없는 표를 만들어 놓지도 않는다 — 보존할 과거가 없는 곳의 빈 표는
    증거가 아니라 잡동사니다.
    """
    sql = (
        REPO / "supabase" / "migrations"
        / "20261009000000_freeze_legacy_purchase_tables.sql"
    ).read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))

    for table in ("public.purchased_slots", "public.theme_purchase_orders"):
        assert f"to_regclass('{table}')" in code, f"{table} 존재 확인이 없다"
    # 없는 표를 만들어서 해결하지 않는다.
    assert "create table" not in code.lower()


def test_order_module_is_independent_of_subscription_and_generation():
    """
    구조로 고정 — 주문 모듈이 구독·생성 모듈을 import 하지 않는다.

    wallet_service 는 Phase 4 에서 금지 목록에서 빠졌다: 테마가 Beam Credit 으로
    팔리게 되면서 지갑을 건드리는 것이 theme_purchase 의 일이 됐기 때문이다.
    구독·생성 축은 그대로 독립이어야 한다 (test_theme_store.py 의 같은 이름 참고).
    """
    forbidden = {
        "premium_entitlement", "subscription_store_service", "premium_generation",
        "generation_queue", "credit_generation_service",
        "premium_purchase",
    }
    for path in ("backend/services/theme_order.py", "backend/services/theme_purchase.py"):
        tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
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
