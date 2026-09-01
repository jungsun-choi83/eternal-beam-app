"""
테마 → Beam Credit 구매의 **원자성 계약** (Phase 4).

    Aurora → 5 Beam Credits → entitlement → 영구 소유

한 번의 구매는 세 가지를 바꾼다:

    1. 지갑 잔액        -5
    2. 원장 한 줄        theme_purchase
    3. 소유권 한 줄      user_theme_entitlements (영구)

**전부 일어나거나 하나도 일어나지 않아야 한다.** 부분 성공은 둘 다 나쁘다:

    차감만 성공  → 고객은 크레딧을 잃고 테마는 못 쓴다
    소유권만 성공 → 공짜로 준 것이고 원장이 그것을 설명하지 못한다

여기서는 인메모리 경로를 검증한다. 실제 트랜잭션 원자성은 SQL 계약 테스트
(test_theme_credit_purchase_sql.py)가 Postgres 에서 확인한다.
"""

from __future__ import annotations

import pytest

from backend.services import (
    credit_ledger,
    product_catalog,
    theme_entitlement,
    theme_purchase,
    wallet_service,
)

USER = "user_theme_credits"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    wallet_service._MOCK_WALLETS.clear()
    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()
    # Phase 4 가격표 — 마이그레이션 20261003000000 과 같은 값.
    product_catalog.set_price_for_tests("theme:aurora", 5, product_catalog.TYPE_THEME)
    product_catalog.set_price_for_tests("theme:sunset", 4, product_catalog.TYPE_THEME)
    yield
    wallet_service._MOCK_WALLETS.clear()
    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()


async def _fund(amount: int, user: str = USER) -> None:
    await wallet_service.add_credits(
        user, amount,
        reason=credit_ledger.REASON_CREDIT_PACK_TOPUP,
        idempotency_key=f"fund:{user}:{amount}",
    )


async def _balance(user: str = USER) -> int:
    w = await wallet_service.get_wallet(user, create_if_missing=True)
    return w.current_credits if w else 0


# ── 요구된 흐름 그대로 ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_documented_flow():
    """
    선택 → 잔액 12 / 가격 5 → 잠금 해제 → 잔액 7 · Aurora OWNED.
    """
    await _fund(12)
    assert await _balance() == 12
    assert await product_catalog.credit_price("theme:aurora") == 5

    out = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    assert out.charged == 5
    assert out.already_owned is False
    assert out.credits_remaining == 7
    assert await _balance() == 7
    assert await theme_entitlement.is_owned(USER, "aurora") is True


@pytest.mark.anyio
async def test_ownership_is_permanent():
    """OWNED FOREVER — 만료 시각이 없다."""
    await _fund(12)
    await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    ents = await theme_entitlement.list_entitlements(USER)
    aurora = next(e for e in ents if e.theme_key == "aurora")
    assert aurora.expires_at is None, "테마 소유권에 만료가 붙었다"
    assert aurora.active is True
    assert aurora.provider == "credits"
    assert aurora.amount == 5
    assert aurora.currency == "CREDIT"


@pytest.mark.anyio
async def test_the_ledger_explains_the_purchase():
    await _fund(12)
    await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    rows = credit_ledger.mock_entries(USER)
    spend = [r for r in rows if r.reason == credit_ledger.REASON_THEME_PURCHASE]
    assert len(spend) == 1
    assert spend[0].delta == -5
    assert spend[0].balance_after == 7
    assert spend[0].product_key == "theme:aurora"
    # 지불 시점 가격 스냅샷 — 나중에 카탈로그가 바뀌어도 이 거래는 5 였다.
    assert spend[0].unit_price == 5
    assert spend[0].ref_type == "user_theme_entitlements"
    assert credit_ledger.mock_balance(USER) == await _balance()


# ── 원자성 ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_insufficient_credits_changes_nothing():
    """
    잔액이 모자라면 **아무것도 일어나지 않는다.** 소유권도, 원장도, 잔액도.
    """
    await _fund(3)

    with pytest.raises(theme_purchase.ThemePurchaseError) as e:
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    assert e.value.code == "INSUFFICIENT_CREDITS"
    assert e.value.status == 402
    assert await _balance() == 3
    assert await theme_entitlement.is_owned(USER, "aurora") is False
    assert [r for r in credit_ledger.mock_entries(USER)
            if r.reason == credit_ledger.REASON_THEME_PURCHASE] == []


@pytest.mark.anyio
async def test_a_failed_entitlement_returns_the_credits(monkeypatch):
    """
    **크레딧만 잃는 상태를 남기지 않는다.**

    차감은 됐는데 소유권 부여가 실패하면 되돌린다. DB 경로에서는 트랜잭션이
    통째로 롤백되고, 인메모리 경로에서는 보상 환불이 같은 결과를 만든다.
    """
    await _fund(12)

    async def _boom(**_kw):
        raise theme_entitlement.ThemeEntitlementError(
            "THEME_ENTITLEMENTS_UNAVAILABLE", "down", status=503
        )

    monkeypatch.setattr(theme_entitlement, "grant", _boom)

    with pytest.raises(theme_purchase.ThemePurchaseError):
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    assert await _balance() == 12, "소유권을 못 만들었는데 크레딧이 사라졌다"
    assert await theme_entitlement.is_owned(USER, "aurora") is False
    # 원장은 차감과 되돌림을 **둘 다** 설명한다 — 합계가 맞을 뿐 아니라 이유가 남는다.
    reasons = [r.reason for r in credit_ledger.mock_entries(USER)]
    assert credit_ledger.REASON_THEME_PURCHASE in reasons
    assert credit_ledger.REASON_RESERVATION_RELEASE in reasons
    assert credit_ledger.mock_balance(USER) == 12


# ── 멱등성 ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_double_tap_charges_once():
    await _fund(12)
    first = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    second = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    assert first.charged == 5
    assert second.charged == 0
    assert second.already_owned is True
    assert await _balance() == 7


@pytest.mark.anyio
async def test_owning_via_toss_is_not_overwritten_by_a_credit_call():
    """
    이미 KRW 로 산 테마에 크레딧 구매를 부르면 **과금하지 않고 기록도 유지한다.**
    덮어쓰면 결제 이력(provider/amount/currency)이 사라진다.
    """
    await theme_entitlement.grant(
        user_id=USER, theme_key="aurora", order_id="toss_order_1",
        provider="toss", amount=4900, currency="KRW",
    )
    await _fund(12)

    out = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")

    assert out.charged == 0
    assert out.already_owned is True
    assert await _balance() == 12

    ents = await theme_entitlement.list_entitlements(USER)
    aurora = next(e for e in ents if e.theme_key == "aurora")
    assert aurora.provider == "toss", "KRW 결제 기록이 크레딧 기록으로 덮였다"
    assert aurora.amount == 4900
    assert aurora.currency == "KRW"


# ── 카탈로그가 가격의 권위 ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_price_comes_from_the_catalog_not_the_code():
    await _fund(20)
    product_catalog.set_price_for_tests("theme:aurora", 9, product_catalog.TYPE_THEME)

    out = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    assert out.charged == 9
    assert await _balance() == 11


@pytest.mark.anyio
async def test_two_themes_can_cost_different_amounts():
    """Aurora 5 · Sunset 4 — 같은 카테고리, 다른 값."""
    await _fund(20)
    a = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    s = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="sunset")

    assert (a.charged, s.charged) == (5, 4)
    assert await _balance() == 11


@pytest.mark.anyio
async def test_a_theme_without_a_credit_price_is_not_sold():
    """가격이 없으면 **무료가 아니라 판매 불가**다."""
    await _fund(20)
    product_catalog._mock_catalog().pop("theme:ocean_deep", None)

    with pytest.raises(theme_purchase.ThemePurchaseError) as e:
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="ocean_deep")
    assert e.value.code == "THEME_PRODUCT_NOT_SOLD"
    assert await _balance() == 20


@pytest.mark.anyio
async def test_free_themes_are_not_purchasable():
    await _fund(20)
    with pytest.raises(theme_purchase.ThemePurchaseError) as e:
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="fresh_forest")
    assert e.value.code == "THEME_IS_FREE"
    assert await _balance() == 20


@pytest.mark.anyio
async def test_unknown_theme_is_rejected():
    await _fund(20)
    with pytest.raises(theme_purchase.ThemePurchaseError):
        await theme_purchase.purchase_with_credits(user_id=USER, theme_key="not_a_theme")


# ── 소유권 저장소는 하나뿐 ──────────────────────────────────────────────────


def test_no_second_ownership_table_was_invented():
    """
    크레딧 구매도 KRW 구매도 결과물은 user_theme_entitlements 한 줄이다.

    소유권 표가 둘이 되면 "어느 쪽이 진짜인가"가 생기고, 그 질문은 PayPal 의
    purchased_slots 가 이미 한 번 만들어 낸 문제다(docs/PAYPAL_LEGACY.md).
    """
    from pathlib import Path

    sql = Path("supabase/migrations/20261003000000_theme_purchase_with_credits.sql").read_text(
        encoding="utf-8"
    )
    assert "create table" not in sql.lower(), "새 소유권 테이블을 만들었다"
    assert "user_theme_entitlements" in sql

    src = Path("backend/services/theme_purchase.py").read_text(encoding="utf-8")
    assert "theme_entitlement" in src
