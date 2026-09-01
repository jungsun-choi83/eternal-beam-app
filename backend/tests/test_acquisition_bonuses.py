"""
획득 보너스 (Phase 9) + 멤버십 크레딧 (Phase 10).

    Soul Trace 핸드오프 → +5  → Eternal Beam 창작
    LETTER / MEMORY BOX → 보너스 → 고객이 돌아온다
    멤버십 갱신         → +N  → **같은 지갑**

Phase 9 종료 조건: 획득 생태계가 크레딧으로 이어진다.
Phase 10 종료 조건: **멤버십은 크레딧 전달 수단이지 소유의 조건이 아니다.**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services import (
    acquisition_bonus,
    credit_ledger,
    owned_assets,
    product_catalog,
    theme_entitlement,
    theme_purchase,
    wallet_service,
)

USER = "user_acq"
PET = "pet_acq"
REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261008000000_acquisition_bonuses.sql"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    wallet_service._MOCK_WALLETS.clear()
    acquisition_bonus.__reset_for_tests()
    owned_assets.__reset_for_tests()
    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()
    product_catalog.set_price_for_tests("theme:aurora", 5, product_catalog.TYPE_THEME)
    yield
    wallet_service._MOCK_WALLETS.clear()
    acquisition_bonus.__reset_for_tests()
    owned_assets.__reset_for_tests()
    theme_entitlement.__reset_for_tests()
    product_catalog.__reset_for_tests()


async def _balance(user: str = USER) -> int:
    w = await wallet_service.get_wallet(user, create_if_missing=True)
    return w.current_credits if w else 0


# ── Soul Trace ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_soultrace_handoff_grants_credits():
    granted = await acquisition_bonus.grant_soultrace(
        user_id=USER, source_letter_id="letter-abc"
    )
    assert granted == 5
    assert await _balance() == 5
    assert credit_ledger.mock_entries(USER)[0].reason == credit_ledger.REASON_SOULTRACE_BONUS


@pytest.mark.anyio
async def test_the_same_letter_grants_only_once():
    await acquisition_bonus.grant_soultrace(user_id=USER, source_letter_id="letter-abc")
    again = await acquisition_bonus.grant_soultrace(user_id=USER, source_letter_id="letter-abc")
    assert again == 0
    assert await _balance() == 5


@pytest.mark.anyio
async def test_the_same_letter_cannot_be_farmed_across_accounts():
    """
    **핵심 남용 방어.** 멱등 키가 Soul Trace 원본 편지 id 라, 같은 편지를 여러
    계정으로 가져가도 보너스는 하나뿐이다.

    우리 쪽 파생 letter_id(안에 user_id 가 들어 있다)로 잡았다면 계정마다 보너스가
    나갔을 것이다.
    """
    assert await acquisition_bonus.grant_soultrace(
        user_id=USER, source_letter_id="letter-shared"
    ) == 5
    assert await acquisition_bonus.grant_soultrace(
        user_id="other_user", source_letter_id="letter-shared"
    ) == 0
    assert await _balance("other_user") == 0


def test_the_key_is_the_letter_not_the_handoff_token():
    """
    임시 핸드오프 토큰은 편지 하나에 대해 **몇 번이든** 새로 발급된다
    (POST /api/handoff 에 횟수 제한이 없다 — 실패한 핸드오프를 다시 시도할 수
    있어야 하므로 그것이 옳다). 토큰을 키로 삼으면 토큰을 다시 받는 것만으로
    보너스를 다시 받는다.
    """
    assert acquisition_bonus.soultrace_idempotency_key("L1") == "soultrace:L1"

    src = (REPO / "backend" / "routers" / "orders_v1.py").read_text(encoding="utf-8")
    call = src.split("grant_soultrace(", 1)[1].split(")", 1)[0]
    assert "source.letter_id" in call, "핸드오프 토큰으로 키를 만들고 있다"
    assert "handoff" not in call


# ── 실물 상품 ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("product,expected", [("LETTER", 3), ("MEMORY_BOX", 10)])
async def test_physical_purchase_grants_its_bonus(product, expected):
    granted = await acquisition_bonus.grant_physical(
        user_id=USER, order_id=f"ord_{product}", product_type=product
    )
    assert granted == expected
    assert await _balance() == expected
    assert (
        credit_ledger.mock_entries(USER)[0].reason
        == credit_ledger.REASON_PHYSICAL_PRODUCT_BONUS
    )


@pytest.mark.anyio
async def test_refreshing_checkout_confirmation_cannot_grant_again():
    """**지시된 요구사항.** 새로고침 한 번이 10 크레딧이 되면 안 된다."""
    first = await acquisition_bonus.grant_physical(
        user_id=USER, order_id="ord_1", product_type="MEMORY_BOX"
    )
    for _ in range(5):  # 새로고침 다섯 번
        assert await acquisition_bonus.grant_physical(
            user_id=USER, order_id="ord_1", product_type="MEMORY_BOX"
        ) == 0
    assert first == 10
    assert await _balance() == 10


@pytest.mark.anyio
async def test_two_different_orders_each_grant():
    await acquisition_bonus.grant_physical(user_id=USER, order_id="o1", product_type="LETTER")
    await acquisition_bonus.grant_physical(user_id=USER, order_id="o2", product_type="LETTER")
    assert await _balance() == 6


@pytest.mark.anyio
async def test_an_unknown_product_grants_nothing_and_does_not_raise():
    """보너스는 덤이다 — 규칙이 없다고 결제가 실패해서는 안 된다."""
    assert await acquisition_bonus.grant_physical(
        user_id=USER, order_id="o3", product_type="MYSTERY_BOX"
    ) == 0
    assert await _balance() == 0


@pytest.mark.anyio
async def test_a_bonus_failure_never_breaks_the_paying_path(monkeypatch):
    """고객은 돈을 냈고 주문은 성사됐다 — 덤을 못 줬다고 그것을 취소할 수 없다."""
    async def _boom(*_a, **_k):
        raise RuntimeError("wallet down")

    monkeypatch.setattr(wallet_service, "add_credits", _boom)
    assert await acquisition_bonus.grant_physical(
        user_id=USER, order_id="o4", product_type="LETTER"
    ) == 0  # 예외가 밖으로 나가지 않는다


def test_bonus_amounts_are_server_controlled():
    """숫자가 코드에 박혀 있으면 마케팅이 값을 바꿀 때마다 배포가 필요하다."""
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("insert into public.credit_bonus_rules", 1)[1].split("on conflict", 1)[0]
    rows = dict(re.findall(r"\('([a-z_:A-Z]+)',\s*(\d+),", block))
    assert {k: int(v) for k, v in rows.items()} == acquisition_bonus._SEED

    # 재배포가 운영에서 조정한 값을 되돌리지 않는다.
    assert "credits = excluded.credits" not in sql


def test_hooks_are_wired_into_both_paths():
    orders = (REPO / "backend" / "routers" / "orders_v1.py").read_text(encoding="utf-8")
    checkout = (REPO / "backend" / "services" / "physical_checkout.py").read_text(encoding="utf-8")
    assert "grant_soultrace" in orders
    assert "grant_physical" in checkout


# ── Phase 10: 멤버십은 소유의 조건이 아니다 ─────────────────────────────────


def test_membership_grants_beam_credits_not_a_separate_currency():
    """
    member coin 도 subscription token 도 만들지 않는다. 멤버십이 지급하는 것은
    크레딧 팩으로 산 것과 **구분되지 않는** 같은 Beam Credit 이다.
    """
    from backend.data.subscription_plans import get_subscription_plan

    plan = get_subscription_plan("web_membership")
    assert plan.credits_per_month > 0, "멤버십이 크레딧을 지급하지 않는다"

    # 지급 사유가 공용 어휘 안에 있다 — 별도 화폐라면 별도 사유가 필요했을 것이다.
    assert credit_ledger.REASON_MEMBERSHIP_GRANT in credit_ledger.ALL_REASONS
    assert credit_ledger.REASON_MEMBERSHIP_GRANT in credit_ledger.CREDIT_REASONS


@pytest.mark.anyio
async def test_membership_credits_are_indistinguishable_from_purchased_ones():
    """
    멤버십 크레딧으로 테마를 산다. 지갑에서 어느 것이 먼저 쓰이는지 물을 필요가
    없다 — 화폐가 하나뿐이라 그 질문이 성립하지 않는다.
    """
    await wallet_service.add_credits(
        USER, 12, reason=credit_ledger.REASON_MEMBERSHIP_GRANT, idempotency_key="m1"
    )
    out = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    assert out.charged == 5
    assert await _balance() == 7


@pytest.mark.anyio
async def test_cancelling_membership_does_not_revoke_anything_owned():
    """
    **Phase 10 종료 조건.**

        해지 → 혜택은 멈춘다
             → Aurora 는 그대로 소유
             → Sleeping #1 · Paw Wave 는 그대로 소유
    """
    from backend.services import subscription_store_service as sub_store

    await wallet_service.add_credits(
        USER, 20, reason=credit_ledger.REASON_MEMBERSHIP_GRANT, idempotency_key="m1"
    )
    await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    for n, product in ((1, "BLINKING"), (2, "COME_CLOSER")):
        await owned_assets.record(
            owned_assets.OwnedAsset(
                user_id=USER, pet_id=PET,
                product_key=owned_assets.product_key_for_action(product),
                video_url=f"https://cdn/{product}.mp4", source_job_id=f"j{n}",
            )
        )

    # 구독을 만료시킨다.
    sub_store._MOCK_SUBS.clear()

    assert await theme_entitlement.is_owned(USER, "aurora") is True, "해지가 테마를 회수했다"
    assert len(await owned_assets.list_for_pet(USER, PET)) == 2, "해지가 모션을 회수했다"
    # 이미 받은 크레딧도 그대로다.
    assert await _balance() == 15


def test_ownership_tables_never_read_subscription_state():
    """
    구조적 보장 — 소유를 정하는 표의 서비스가 구독을 읽지 않는다.
    읽기 시작하면 "만료되면 못 쓴다"가 코드 한 줄로 생길 수 있다.
    """
    import ast

    for name in ("theme_entitlement.py", "owned_assets.py"):
        src = (REPO / "backend" / "services" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.rsplit(".", 1)[-1])
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name.rsplit(".", 1)[-1] for a in node.names)
        leaked = imported & {
            "premium_entitlement",
            "subscription_store_service",
            "billing_service",
            "billing_store",
        }
        assert not leaked, f"{name} 이 구독을 읽는다: {leaked}"
