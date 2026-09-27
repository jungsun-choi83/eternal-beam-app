"""
Beam Credit 팩의 **커머스 계약** (Phase 5).

    packs → checkout → Toss → confirm → credit_ledger + wallet

여기서 고정하는 것:
  * 팩 구성·가격은 **서버가 정한다** (프론트가 하드코딩하지 않는다)
  * 금액은 체크아웃 시점에 주문에 고정된다 — 리다이렉트 파라미터를 믿지 않는다
  * 확인은 멱등하다 (새로고침·뒤로가기가 두 번 충전하지 않는다)
  * 남의 주문을 확인할 수 없다
  * 실패한 결제는 크레딧을 주지 않는다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services import (
    credit_ledger,
    credit_pack_service,
    toss_billing,
    wallet_service,
)

USER = "user_packs"
OTHER = "user_other"
REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261004000000_credit_packs.sql"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    monkeypatch.setenv("TOSS_MOCK", "1")
    wallet_service._MOCK_WALLETS.clear()
    credit_pack_service.__reset_for_tests()
    yield
    wallet_service._MOCK_WALLETS.clear()
    credit_pack_service.__reset_for_tests()


async def _balance(user: str = USER) -> int:
    w = await wallet_service.get_wallet(user, create_if_missing=True)
    return w.current_credits if w else 0


async def _buy(pack_key: str, user: str = USER):
    c = await credit_pack_service.start_checkout(user_id=user, pack_key=pack_key)
    return c, await credit_pack_service.confirm(
        user_id=user, order_id=c.order_id, payment_key="pk_test", amount=c.amount
    )


# ── 카탈로그는 서버가 정한다 ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_packs_come_from_the_server():
    packs = await credit_pack_service.list_packs()
    assert [(p.credits, p.price_krw) for p in packs] == [(5, 4900), (12, 9900), (30, 19900)]


@pytest.mark.anyio
async def test_packs_are_sorted_by_the_server_not_the_client():
    """정렬을 프론트가 추측하지 않게 서버가 순서를 정한다."""
    packs = await credit_pack_service.list_packs()
    assert [p.pack_key for p in packs] == ["pack_5", "pack_12", "pack_30"]


def test_mock_seed_matches_the_migration_seed():
    """
    목업과 SQL 이 갈라지면 그 차이는 **프로덕션에서만** 드러난다.
    (이 저장소가 Phase 8 에서 이미 겪은 실패 방식이다.)
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("insert into public.credit_packs", 1)[1].split("on conflict", 1)[0]
    rows = re.findall(r"\('([a-z0-9_]+)',\s*(\d+),\s*(\d+),", block)
    in_sql = {k: (int(c), int(p)) for k, c, p in rows}
    in_py = {k: (c, p) for k, c, p, _n, _o in credit_pack_service._SEED}
    assert in_sql == in_py


def test_the_frontend_does_not_hardcode_pack_prices():
    """
    가격이 브라우저 번들에 있으면 바꾸는 데 배포가 필요하고, 서버와 어긋나면
    눌러도 거절당하는 버튼이 생긴다 — themes.ts 의 "$2.99" 가 그 문제였다.
    """
    for rel in ("src/lib/credits-api.ts", "src/components/memorial/credit-pack-sheet.tsx"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for amount in ("4900", "9900", "19900"):
            assert amount not in src, f"{rel} 에 팩 가격 {amount} 이 박혀 있다"
        for credits in ("pack_5", "pack_12", "pack_30"):
            assert credits not in src, f"{rel} 에 팩 키 {credits} 가 박혀 있다"


# ── 체크아웃 ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_checkout_moves_no_money_and_fixes_the_amount():
    before = await _balance()
    c = await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_5")

    assert c.amount == 4900
    assert c.credits == 5
    assert c.order_id
    assert await _balance() == before, "체크아웃이 잔액을 바꿨다"


@pytest.mark.anyio
async def test_unknown_pack_is_rejected():
    with pytest.raises(credit_pack_service.CreditPackError) as e:
        await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_999")
    assert e.value.code == "CREDIT_PACK_UNKNOWN"


# ── 확인 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_credits_the_wallet_and_the_ledger():
    _, out = await _buy("pack_5")

    assert out.credits_added == 5
    assert out.credits_remaining == 5
    assert await _balance() == 5

    rows = [
        r for r in credit_ledger.mock_entries(USER)
        if r.reason == credit_ledger.REASON_CREDIT_PACK_TOPUP
    ]
    assert len(rows) == 1
    assert rows[0].delta == 5
    assert rows[0].product_key == "pack_5"
    # KRW 스냅샷 — 이 크레딧이 얼마짜리였는지가 환불 계산의 근거다.
    assert rows[0].unit_price == 4900
    assert rows[0].ref_type == "credit_pack_orders"
    assert credit_ledger.mock_balance(USER) == 5


@pytest.mark.anyio
async def test_confirm_is_idempotent():
    """새로고침·뒤로가기가 두 번 충전하지 않는다."""
    c, first = await _buy("pack_12")
    second = await credit_pack_service.confirm(
        user_id=USER, order_id=c.order_id, payment_key="pk_test", amount=c.amount
    )

    assert first.credits_added == 12
    assert second.credits_added == 0
    assert second.replayed is True
    assert await _balance() == 12


@pytest.mark.anyio
async def test_another_users_order_is_not_found():
    """order_id 는 리다이렉트 URL 에 노출된다 — 남의 결제로 자기 지갑을 채울 수 없다."""
    c = await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_5")

    with pytest.raises(credit_pack_service.CreditPackError) as e:
        await credit_pack_service.confirm(
            user_id=OTHER, order_id=c.order_id, payment_key="pk", amount=c.amount
        )
    assert e.value.code == "CREDIT_ORDER_NOT_FOUND"
    assert await _balance(OTHER) == 0
    assert await _balance(USER) == 0


@pytest.mark.anyio
async def test_a_forged_amount_is_rejected():
    """
    amount 는 **주소창에 있다.** 그대로 믿으면 URL 을 고쳐 1원짜리 승인으로
    30 크레딧을 받을 수 있다.
    """
    c = await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_30")

    with pytest.raises(credit_pack_service.CreditPackError) as e:
        await credit_pack_service.confirm(
            user_id=USER, order_id=c.order_id, payment_key="pk", amount=1
        )
    assert e.value.code == "CREDIT_AMOUNT_MISMATCH"
    assert await _balance() == 0


@pytest.mark.anyio
async def test_a_failed_payment_grants_nothing(monkeypatch):
    c = await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_5")

    async def _decline(**_kw):
        return toss_billing.ConfirmResult(
            ok=False, payment_key=None, order_id=c.order_id, amount=0,
            failure_code="REJECT_CARD", failure_message="카드 거절", raw={},
        )

    monkeypatch.setattr(toss_billing, "confirm_payment", _decline)

    with pytest.raises(credit_pack_service.CreditPackError) as e:
        await credit_pack_service.confirm(
            user_id=USER, order_id=c.order_id, payment_key="pk", amount=c.amount
        )
    assert e.value.code == "CREDIT_PAYMENT_FAILED"
    assert await _balance() == 0
    assert credit_ledger.mock_entries(USER) == []


@pytest.mark.anyio
async def test_a_failed_order_cannot_be_confirmed_later(monkeypatch):
    """실패로 종료된 주문을 나중에 되살릴 수 없다."""
    c = await credit_pack_service.start_checkout(user_id=USER, pack_key="pack_5")

    # ⚠️ monkeypatch.undo() 를 쓰지 않는다. pytest 의 monkeypatch 픽스처는 함수
    # 스코프에서 **하나를 공유**하므로, undo() 는 autouse 픽스처가 건 환경변수
    # (HYBRID_USE_SUPABASE=0)까지 되돌린다. 그러면 이 테스트가 갑자기 DB 모드로
    # 넘어가 엉뚱한 오류(CREDIT_ORDER_UNAVAILABLE)를 보게 된다 — 실제로 그렇게
    # 실패했다. 대신 플래그로 응답만 바꾼다.
    declining = {"on": True}

    async def _maybe_decline(**_kw):
        return toss_billing.ConfirmResult(
            ok=not declining["on"], payment_key="pk_ok", order_id=c.order_id,
            amount=c.amount,
            failure_code="REJECT_CARD" if declining["on"] else None,
            failure_message="카드 거절" if declining["on"] else None,
            raw={},
        )

    monkeypatch.setattr(toss_billing, "confirm_payment", _maybe_decline)
    with pytest.raises(credit_pack_service.CreditPackError):
        await credit_pack_service.confirm(
            user_id=USER, order_id=c.order_id, payment_key="pk", amount=c.amount
        )

    # 이제 카드가 통과해도 **주문이 이미 종료**돼 있어 충전되지 않는다.
    declining["on"] = False
    with pytest.raises(credit_pack_service.CreditPackError) as e:
        await credit_pack_service.confirm(
            user_id=USER, order_id=c.order_id, payment_key="pk", amount=c.amount
        )
    assert e.value.code == "CREDIT_ORDER_NOT_PENDING"
    assert await _balance() == 0


# ── 전체 고리: KRW → 크레딧 → 테마 → 영구 소유 ─────────────────────────────


@pytest.mark.anyio
async def test_the_full_loop(monkeypatch):
    """
    **Phase 5 의 종료 조건.**

        잔액 2 → Aurora(5) 시도 → 부족 → 팩 구매 → 잔액 7 → Aurora → 영구 소유
    """
    from backend.services import product_catalog, theme_entitlement, theme_purchase

    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()
    product_catalog.set_price_for_tests("theme:aurora", 5, product_catalog.TYPE_THEME)

    # 잔액 2 — Aurora 를 살 수 없다.
    await wallet_service.add_credits(
        USER, 2, reason=credit_ledger.REASON_ADMIN_ADJUSTMENT, idempotency_key="seed"
    )
    with pytest.raises(theme_purchase.ThemePurchaseError) as e:
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    assert e.value.code == "INSUFFICIENT_CREDITS"

    # 부족분은 3 — pack_5 면 충분하다.
    assert 5 - await _balance() == 3

    # KRW → 크레딧
    _, topup = await _buy("pack_5")
    assert topup.credits_added == 5
    assert await _balance() == 7

    # 크레딧 → 테마
    bought = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    assert bought.charged == 5
    assert await _balance() == 2

    # 영구 소유
    ents = await theme_entitlement.list_entitlements(USER)
    aurora = next(x for x in ents if x.theme_key == "aurora")
    assert aurora.active and aurora.expires_at is None
    assert aurora.provider == "credits"

    # 원장이 전 과정을 설명한다.
    assert [r.reason for r in credit_ledger.mock_entries(USER)] == [
        credit_ledger.REASON_ADMIN_ADJUSTMENT,
        credit_ledger.REASON_CREDIT_PACK_TOPUP,
        credit_ledger.REASON_THEME_PURCHASE,
    ]
    assert credit_ledger.mock_balance(USER) == await _balance()

    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()
