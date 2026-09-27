"""
크레딧 원장의 **회계 계약** (Phase 2).

지키려는 불변식 하나:

    sum(credit_ledger.delta) == user_wallets.current_credits

이 등식이 성립하면 "잔액이 왜 이런가"에 언제나 답할 수 있다. 깨지면 원장은
설명이 아니라 그냥 로그다.

여기서 고정하는 것:
  * 모든 움직임이 기록된다 (충전·차감·환불·가입 보너스)
  * 사유 어휘가 DB CHECK 와 일치한다
  * 사유와 부호가 함께 움직인다 (차감을 충전으로 기록할 수 없다)
  * 멱등 키가 같으면 두 번째는 적용되지 않는다
  * 실패한 차감은 원장에 자국을 남기지 않는다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services import credit_ledger as ledger
from backend.services import wallet_service
from backend.services.wallet_service import InsufficientCreditsError

USER = "user_ledger"
REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261001000000_credit_ledger.sql"


@pytest.fixture(autouse=True)
def _memory_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    wallet_service._MOCK_WALLETS.clear()
    yield
    wallet_service._MOCK_WALLETS.clear()


def _sum(user: str = USER) -> int:
    return ledger.mock_balance(user)


async def _balance(user: str = USER) -> int:
    w = await wallet_service.get_wallet(user, create_if_missing=True)
    return w.current_credits if w else 0


# ── 어휘가 DB 와 일치하는가 ──────────────────────────────────────────────────


def test_python_reasons_match_the_database_check():
    """
    Python 상수와 DB CHECK 가 갈라지면, 한쪽에만 있는 사유가 런타임에 거절된다.
    그 거절은 돈이 움직이는 순간에 일어난다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("credit_ledger_reason_check check (", 1)[1].split(");", 1)[0]
    in_sql = set(re.findall(r"'([a-z_]+)'", block))
    assert in_sql == set(ledger.ALL_REASONS), (
        f"SQL 에만: {in_sql - set(ledger.ALL_REASONS)} / "
        f"Python 에만: {set(ledger.ALL_REASONS) - in_sql}"
    )


def test_states_match_the_database_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    # `add constraint` 를 앵커로 쓴다 — 이름만으로 자르면 바로 위의
    # `drop constraint if exists` 줄에 걸려 빈 블록이 나온다.
    block = sql.split("add constraint credit_ledger_state_check", 1)[1].split(";", 1)[0]
    in_sql = set(re.findall(r"'([A-Z]+)'", block))
    assert in_sql == set(ledger.ALL_STATES)


def test_direction_rules():
    assert ledger.direction_ok(ledger.REASON_CREDIT_PACK_TOPUP, 5)
    assert not ledger.direction_ok(ledger.REASON_CREDIT_PACK_TOPUP, -5)
    assert ledger.direction_ok(ledger.REASON_THEME_PURCHASE, -5)
    assert not ledger.direction_ok(ledger.REASON_THEME_PURCHASE, 5)
    # 개시 잔액만 0 을 허용한다 (잔액 0 인 지갑도 원장에 자리를 갖는다).
    assert ledger.direction_ok(ledger.REASON_LEGACY_MIGRATION, 0)
    assert not ledger.direction_ok(ledger.REASON_STARTER_BONUS, 0)
    # 운영 조정만 양방향.
    assert ledger.direction_ok(ledger.REASON_ADMIN_ADJUSTMENT, 5)
    assert ledger.direction_ok(ledger.REASON_ADMIN_ADJUSTMENT, -5)
    assert not ledger.direction_ok("nonsense", 5)


# ── 모든 움직임이 기록되는가 ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_topup_is_recorded():
    await wallet_service.add_credits(
        USER, 5, reason=ledger.REASON_CREDIT_PACK_TOPUP,
        idempotency_key="k1", product_key="credit_pack_4", unit_price=4900,
    )
    rows = ledger.mock_entries(USER)
    assert len(rows) == 1
    assert rows[0].delta == 5
    assert rows[0].balance_after == 5
    assert rows[0].reason == ledger.REASON_CREDIT_PACK_TOPUP
    assert rows[0].product_key == "credit_pack_4"
    assert rows[0].unit_price == 4900
    assert _sum() == await _balance() == 5


@pytest.mark.anyio
async def test_spend_is_recorded_with_a_negative_delta():
    await wallet_service.add_credits(USER, 10, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="k1")
    await wallet_service.deduct_credits(
        USER, 4, reason=ledger.REASON_IDLE_GENERATION,
        idempotency_key="k2", product_key="idle:SLEEPING", unit_price=4,
    )
    rows = ledger.mock_entries(USER)
    assert [r.delta for r in rows] == [10, -4]
    assert rows[-1].balance_after == 6
    assert _sum() == await _balance() == 6


@pytest.mark.anyio
async def test_refund_is_recorded():
    await wallet_service.add_credits(USER, 10, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="k1")
    await wallet_service.deduct_credits(USER, 4, reason=ledger.REASON_IDLE_GENERATION, idempotency_key="k2")
    await wallet_service.refund_credits(USER, 4, idempotency_key="k3")

    rows = ledger.mock_entries(USER)
    assert [r.reason for r in rows] == [
        ledger.REASON_CREDIT_PACK_TOPUP,
        ledger.REASON_IDLE_GENERATION,
        ledger.REASON_REFUND,
    ]
    assert _sum() == await _balance() == 10


@pytest.mark.anyio
async def test_starter_bonus_is_recorded_not_conjured(monkeypatch):
    """
    예전에는 지갑을 STARTER_CREDITS 로 **직접 insert** 했다. 그러면 잔액 4 짜리
    지갑의 원장 합계가 0 이 되어, 첫 사용자부터 불변식이 깨진다.
    """
    monkeypatch.setenv("STARTER_CREDITS", "4")
    bal = await _balance("fresh_user")

    rows = ledger.mock_entries("fresh_user")
    assert bal == 4
    assert len(rows) == 1
    assert rows[0].reason == ledger.REASON_STARTER_BONUS
    assert rows[0].delta == 4
    assert ledger.mock_balance("fresh_user") == 4


@pytest.mark.anyio
async def test_starter_bonus_is_once_per_user(monkeypatch):
    """
    지갑을 지웠다 다시 만들어도 보너스는 다시 지급되지 않는다 (키가 사용자당 하나).
    예전에는 localStorage 를 지우면 STARTER_CREDITS 를 무한히 받을 수 있었다.
    """
    monkeypatch.setenv("STARTER_CREDITS", "4")
    await _balance("repeat_user")
    wallet_service._MOCK_WALLETS.clear()  # 지갑만 날린다 (원장은 남는다)

    again = await _balance("repeat_user")
    assert again == 0, "가입 보너스가 두 번 지급됐다"
    assert len(ledger.mock_entries("repeat_user")) == 1


# ── 멱등성 ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_same_key_does_not_apply_twice():
    await wallet_service.add_credits(USER, 5, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="dup")
    await wallet_service.add_credits(USER, 5, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="dup")

    assert await _balance() == 5, "같은 영수증으로 두 번 충전됐다"
    assert len(ledger.mock_entries(USER)) == 1
    assert _sum() == 5


@pytest.mark.anyio
async def test_replayed_refund_does_not_inflate_the_balance():
    """웹훅 재전송이 환불을 두 번 만들지 않는다."""
    await wallet_service.add_credits(USER, 4, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="k1")
    await wallet_service.deduct_credits(USER, 4, reason=ledger.REASON_ACTION_GENERATION, idempotency_key="k2")
    await wallet_service.refund_credits(USER, 4, idempotency_key="refund:1")
    await wallet_service.refund_credits(USER, 4, idempotency_key="refund:1")

    assert await _balance() == 4
    assert _sum() == 4


# ── 사유·부호 검증이 호출부에서 걸리는가 ────────────────────────────────────


@pytest.mark.anyio
async def test_unknown_reason_is_rejected_before_the_wallet_moves():
    with pytest.raises(ValueError, match="사유"):
        await wallet_service.add_credits(USER, 5, reason="idle_generatoin", idempotency_key="k")
    assert await _balance() == 0
    assert ledger.mock_entries(USER) == []


@pytest.mark.anyio
async def test_a_spend_reason_cannot_be_used_to_add_credits():
    """차감 사유로 충전하면 원장은 합계가 맞으면서 설명이 거꾸로가 된다."""
    with pytest.raises(ValueError, match="충전"):
        await wallet_service.add_credits(
            USER, 5, reason=ledger.REASON_THEME_PURCHASE, idempotency_key="k"
        )


@pytest.mark.anyio
async def test_a_credit_reason_cannot_be_used_to_deduct():
    with pytest.raises(ValueError, match="차감"):
        await wallet_service.deduct_credits(
            USER, 5, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="k"
        )


# ── 실패는 자국을 남기지 않는다 ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_failed_deduction_records_nothing():
    """
    잔액 부족으로 실패한 차감은 지갑도 원장도 건드리지 않는다.
    (부분 적용이 남으면 그게 곧 불변식 위반이다.)
    """
    await wallet_service.add_credits(USER, 2, reason=ledger.REASON_CREDIT_PACK_TOPUP, idempotency_key="k1")

    with pytest.raises(InsufficientCreditsError):
        await wallet_service.deduct_credits(
            USER, 99, reason=ledger.REASON_IDLE_GENERATION, idempotency_key="k2"
        )

    assert await _balance() == 2
    assert len(ledger.mock_entries(USER)) == 1
    assert _sum() == 2


# ── 불변식 ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_invariant_holds_across_a_realistic_sequence(monkeypatch):
    monkeypatch.setenv("STARTER_CREDITS", "4")
    u = "journey_user"

    await _balance(u)                                                   # +4 starter
    await wallet_service.add_credits(                                   # +12 membership
        u, 12, reason=ledger.REASON_MEMBERSHIP_GRANT, idempotency_key="m1"
    )
    await wallet_service.deduct_credits(                                # -5 theme
        u, 5, reason=ledger.REASON_THEME_PURCHASE, idempotency_key="t1",
        product_key="theme:aurora", unit_price=5,
    )
    await wallet_service.deduct_credits(                                # -3 idle
        u, 3, reason=ledger.REASON_IDLE_GENERATION, idempotency_key="i1"
    )
    await wallet_service.refund_credits(u, 3, idempotency_key="r1")     # +3 refund
    await wallet_service.add_credits(                                   # +3 physical bonus
        u, 3, reason=ledger.REASON_PHYSICAL_PRODUCT_BONUS, idempotency_key="p1"
    )

    w = await wallet_service.get_wallet(u)
    assert w is not None
    assert w.current_credits == 4 + 12 - 5 - 3 + 3 + 3 == 14
    assert ledger.mock_balance(u) == w.current_credits, "원장 합계와 잔액이 어긋났다"

    # 모든 움직임이 설명 가능해야 한다.
    reasons = [r.reason for r in ledger.mock_entries(u)]
    assert reasons == [
        ledger.REASON_STARTER_BONUS,
        ledger.REASON_MEMBERSHIP_GRANT,
        ledger.REASON_THEME_PURCHASE,
        ledger.REASON_IDLE_GENERATION,
        ledger.REASON_REFUND,
        ledger.REASON_PHYSICAL_PRODUCT_BONUS,
    ]
    # balance_after 가 매 시점의 잔액을 정확히 따라간다.
    assert [r.balance_after for r in ledger.mock_entries(u)] == [4, 16, 11, 8, 11, 14]
