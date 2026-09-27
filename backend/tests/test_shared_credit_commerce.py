"""
**Phase 8 종료 조건** — 테마·아이들·액션이 하나를 공유한다.

    하나의 지갑      user_wallets
    하나의 원장      credit_ledger
    하나의 카탈로그  digital_products
    하나의 멱등 모델 credit_ledger.idempotency_key

가격과 거동은 다르지만 **경로는 하나다.** 액션용 지갑도, 액션용 원장도, 액션용
멱등 모델도 없다 — 두 벌이 생기면 서로 조금씩 어긋나고, 그 어긋남은 돈에서 드러난다.

이 파일은 두 가지를 한다:
  1. 세 종류를 실제로 사서 같은 원장·같은 지갑에 남는지 확인한다
  2. **분리된 시스템이 생기지 못하게** 구조를 검사한다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services import (
    credit_ledger,
    credit_reservation,
    generation_credits,
    owned_assets,
    product_catalog,
    theme_entitlement,
    theme_purchase,
    wallet_service,
)

USER = "user_shared"
PET = "pet_shared"
REPO = Path(__file__).resolve().parents[2]
SERVICES = REPO / "backend" / "services"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    for mod in (wallet_service._MOCK_WALLETS,):
        mod.clear()
    credit_reservation.__reset_for_tests()
    owned_assets.__reset_for_tests()
    product_catalog.__reset_for_tests()
    theme_entitlement.__reset_for_tests()
    # 세 상품, 세 가격 — 같은 카탈로그.
    product_catalog.set_price_for_tests("theme:aurora", 5, product_catalog.TYPE_THEME)
    product_catalog.set_price_for_tests("idle:BLINKING", 3, product_catalog.TYPE_IDLE)
    product_catalog.set_price_for_tests("action:COME_CLOSER", 4, product_catalog.TYPE_ACTION)
    yield
    wallet_service._MOCK_WALLETS.clear()
    credit_reservation.__reset_for_tests()
    owned_assets.__reset_for_tests()
    product_catalog.__reset_for_tests()
    theme_entitlement.__reset_for_tests()


async def _fund(n: int) -> None:
    await wallet_service.add_credits(
        USER, n, reason=credit_ledger.REASON_CREDIT_PACK_TOPUP,
        idempotency_key=f"fund:{n}",
    )


async def _balance() -> int:
    w = await wallet_service.get_wallet(USER, create_if_missing=True)
    return w.current_credits if w else 0


# ── 액션도 같은 예약 프리미티브를 쓴다 ──────────────────────────────────────


@pytest.mark.anyio
async def test_actions_reserve_through_the_same_service():
    await _fund(20)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="act:1"
    )
    assert r.product_key == "action:COME_CLOSER"
    assert r.credits == 4
    assert await _balance() == 16

    entry = next(
        e for e in credit_ledger.mock_entries(USER)
        if e.reason == credit_ledger.REASON_ACTION_GENERATION
    )
    assert entry.state == credit_ledger.STATE_RESERVED
    assert entry.delta == -4


@pytest.mark.anyio
async def test_idle_and_action_differ_only_in_price_and_reason():
    """
    같은 함수, 같은 흐름. 갈라지는 것은 **데이터**뿐이다.
    """
    await _fund(20)
    idle = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="i:1"
    )
    action = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="a:1"
    )

    assert (idle.credits, action.credits) == (3, 4)
    assert generation_credits.reason_for(idle.product_key) == credit_ledger.REASON_IDLE_GENERATION
    assert generation_credits.reason_for(action.product_key) == credit_ledger.REASON_ACTION_GENERATION
    # 같은 지갑에서 나갔다.
    assert await _balance() == 20 - 3 - 4


@pytest.mark.anyio
async def test_generating_the_same_action_twice_owns_both():
    """
        Paw Wave → 4 → #1 영구 소유
        또 Paw Wave → 또 4 → #2 영구 소유
    """
    await _fund(20)
    for n in (1, 2):
        r = await generation_credits.reserve_for_action(
            user_id=USER, action_id="COME_CLOSER", idempotency_key=f"act:{n}"
        )
        await generation_credits.commit_for_asset(
            reservation_ledger_id=r.reservation_ledger_id, credits=r.credits,
            user_id=USER, pet_id=PET, action_id="COME_CLOSER",
            video_url=f"https://cdn/paw{n}.mp4", source_job_id=f"job{n}",
        )

    assert await _balance() == 12, "두 번째 액션이 과금되지 않았다"
    assert await owned_assets.count_for_product(USER, PET, "action:COME_CLOSER") == 2
    assert len({a.video_url for a in await owned_assets.list_for_pet(USER, PET)}) == 2


@pytest.mark.anyio
async def test_a_failed_action_releases_like_an_idle():
    await _fund(20)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="act:1"
    )
    assert await _balance() == 16
    assert await generation_credits.release_quietly(r.reservation_ledger_id) is True
    assert await _balance() == 20
    assert await owned_assets.list_for_pet(USER, PET) == []


# ── 하나의 지갑 · 하나의 원장 ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_theme_idle_and_action_share_one_wallet_and_one_ledger():
    """**Phase 8 종료 조건.** 셋을 사고 하나의 원장에서 전부 설명된다."""
    await _fund(20)

    # 테마 — 즉시 소유
    await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    # 아이들 — 예약 → 확정
    i = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="i:1"
    )
    await generation_credits.commit_for_asset(
        reservation_ledger_id=i.reservation_ledger_id, credits=i.credits,
        user_id=USER, pet_id=PET, action_id="BLINKING",
        video_url="https://cdn/blink.mp4", source_job_id="jb",
    )
    # 액션 — 같은 경로
    a = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="a:1"
    )
    await generation_credits.commit_for_asset(
        reservation_ledger_id=a.reservation_ledger_id, credits=a.credits,
        user_id=USER, pet_id=PET, action_id="COME_CLOSER",
        video_url="https://cdn/paw.mp4", source_job_id="ja",
    )

    assert await _balance() == 20 - 5 - 3 - 4 == 8

    reasons = [e.reason for e in credit_ledger.mock_entries(USER)]
    assert reasons == [
        credit_ledger.REASON_CREDIT_PACK_TOPUP,
        credit_ledger.REASON_THEME_PURCHASE,
        credit_ledger.REASON_IDLE_GENERATION,
        credit_ledger.REASON_ACTION_GENERATION,
    ]
    # 불변식: 원장 합계 = 잔액. 셋이 같은 원장을 쓴다는 것의 실질적 의미다.
    assert credit_ledger.mock_balance(USER) == await _balance()

    # 소유는 성격에 따라 다른 표에 있지만, **지출은 하나의 원장**에 있다.
    assert await theme_entitlement.is_owned(USER, "aurora") is True
    assert len(await owned_assets.list_for_pet(USER, PET)) == 2


@pytest.mark.anyio
async def test_all_three_read_prices_from_the_same_catalog():
    assert await product_catalog.credit_price("theme:aurora") == 5
    assert await product_catalog.credit_price("idle:BLINKING") == 3
    assert await product_catalog.credit_price("action:COME_CLOSER") == 4

    # 카탈로그를 바꾸면 셋 다 따라간다 — 배포 없이.
    product_catalog.set_price_for_tests("action:COME_CLOSER", 9)
    await _fund(20)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="a:9"
    )
    assert r.credits == 9


@pytest.mark.anyio
async def test_all_three_use_the_same_idempotency_axis():
    """재시도는 셋 모두에서 같은 방식으로 흡수된다."""
    await _fund(30)

    a1 = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    a2 = await theme_purchase.purchase_with_credits(user_id=USER, theme_key="aurora")
    assert (a1.charged, a2.charged) == (5, 0)

    i1 = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="dup"
    )
    i2 = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="dup"
    )
    assert (i1.replayed, i2.replayed) == (False, True)

    c1 = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="dup2"
    )
    c2 = await generation_credits.reserve_for_action(
        user_id=USER, action_id="COME_CLOSER", idempotency_key="dup2"
    )
    assert (c1.replayed, c2.replayed) == (False, True)

    assert await _balance() == 30 - 5 - 3 - 4


# ── 분리된 시스템이 생기지 못하게 ───────────────────────────────────────────


def test_there_is_no_separate_action_wallet_or_ledger():
    """
    "액션 전용" 저장소가 생기면 두 벌이 서로 어긋나고, 그 어긋남은 돈에서 드러난다.
    이름으로 잡는 것은 거칠지만, 이런 것은 대개 이름부터 갈라진다.
    """
    forbidden = re.compile(
        r"(action_wallet|action_ledger|action_credits_table|actions_wallet)", re.I
    )
    offenders = []
    for p in (REPO / "backend").rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts or "tests" in p.parts:
            continue
        if forbidden.search(p.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, f"액션 전용 지갑/원장으로 보이는 것이 있다: {offenders}"


def test_only_one_module_reserves_credits_for_generation():
    """
    예약을 부르는 곳이 여럿이면 규칙이 여럿이 된다. 생성 크레딧의 진입점은
    generation_credits 하나여야 한다.
    """
    callers = []
    for p in (SERVICES).rglob("*.py"):
        if p.name in ("credit_reservation.py", "generation_credits.py"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "credit_reservation.reserve(" in text:
            callers.append(p.name)
    # premium_purchase 는 레거시 kind 단위 경로라 예외적으로 허용한다.
    assert set(callers) <= {"premium_purchase.py"}, f"예약 진입점이 흩어져 있다: {callers}"


def test_idle_and_action_share_one_code_path():
    """
    갈라지는 지점이 reason_for() 하나인지 확인한다. 아이들/액션으로 갈라지는
    if 문이 늘어나면 두 흐름이 서서히 다른 것이 된다.
    """
    src = (SERVICES / "generation_credits.py").read_text(encoding="utf-8")
    # 예약·확정·해제 함수 본문에 종류별 분기가 없어야 한다.
    for fn in ("async def reserve_for_action", "async def commit_for_asset",
               "async def release_quietly"):
        body = src.split(fn, 1)[1].split("\nasync def ", 1)[0]
        assert "IDLE" not in body.replace("REASON_IDLE_GENERATION", ""), fn
        assert "ACTION" not in body.replace("REASON_ACTION_GENERATION", ""), fn


def test_the_module_name_reflects_that_it_serves_both():
    """
    예전 이름(idle_credit_generation)은 액션도 같은 경로를 타는데 그렇게 말하지
    않아, 읽는 사람이 "액션은 어디서?" 를 묻게 만들었다.
    """
    assert (SERVICES / "generation_credits.py").is_file()
    assert not (SERVICES / "idle_credit_generation.py").exists()


def test_action_prices_only_exist_for_registered_actions():
    """
    레지스트리에 없는 액션에 가격을 매기면 **살 수 있는데 받을 수 없는 것**을
    파는 것이다. 카탈로그와 레지스트리는 함께 움직여야 한다.
    """
    from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS

    known = {f"action:{a}" for a in PET_ACTIONS} | {f"idle:{a}" for a in IDLE_EVENTS}
    known |= {"idle:BREATHING", "idle:BUNDLE"}

    for key in product_catalog._mock_catalog():
        if key.startswith(("action:", "idle:")):
            assert key in known, f"레지스트리에 없는 상품에 가격이 있다: {key}"
