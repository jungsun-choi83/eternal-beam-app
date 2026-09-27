"""
크레딧 **예약 → 확정/해제** 계약 (Phase 7).

    Sleeping 선택 → 5 예약 → 생성 → 검증
        PASS → commit  → 소유 자산 → Sleeping #1 영구 소유
        FAIL → release → 아무 일도 없던 것과 같다

Phase 7 종료 조건 넷을 여기서 고정한다:
  1. 예약 없이는 프로바이더에 제출하지 않는다
  2. 재시도·새로고침이 두 번 청구하지 못한다
  3. 실패한 생성이 크레딧을 영구히 소모하지 않는다
  4. 성공한 생성은 영구 자산을 만든다
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
    wallet_service,
)

# ⚠️ 지시문의 예시는 "Sleeping" 이지만, 실제 레지스트리(pet_scenarios.IDLE_EVENTS)에
# 등록된 아이들 이벤트는 BLINKING / EAR_TWITCHING / HEAD_TILTING / TAIL_WAGGING 이다.
# 등록되지 않은 이름을 쓰면 product_key 가 action: 으로 잡혀 카탈로그와 어긋난다.
# 계약은 같으므로 실재하는 이벤트로 검증한다.
USER = "user_res"
PET = "pet_res"
REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20261006000000_credit_reservations.sql"


@pytest.fixture(autouse=True)
def _memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("STARTER_CREDITS", "0")
    wallet_service._MOCK_WALLETS.clear()
    credit_reservation.__reset_for_tests()
    owned_assets.__reset_for_tests()
    product_catalog.__reset_for_tests()
    product_catalog.set_price_for_tests("idle:BLINKING", 5, product_catalog.TYPE_IDLE)
    yield
    wallet_service._MOCK_WALLETS.clear()
    credit_reservation.__reset_for_tests()
    owned_assets.__reset_for_tests()
    product_catalog.__reset_for_tests()


async def _fund(n: int) -> None:
    await wallet_service.add_credits(
        USER, n, reason=credit_ledger.REASON_CREDIT_PACK_TOPUP,
        idempotency_key=f"fund:{USER}:{n}",
    )


async def _balance() -> int:
    w = await wallet_service.get_wallet(USER, create_if_missing=True)
    return w.current_credits if w else 0


# ── 예약 ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_reserving_holds_the_credits_immediately():
    """
    "예약"이지만 잔액은 **즉시** 빠진다. 그래야 5 크레딧으로 두 건을 동시에
    시작할 수 없다 — 잔액이 그대로면 둘 다 통과하고 하나는 낼 수 없는 돈이 된다.
    """
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    assert r is not None
    assert r.credits == 5
    assert await _balance() == 7

    entry = next(
        e for e in credit_ledger.mock_entries(USER)
        if e.reason == credit_ledger.REASON_IDLE_GENERATION
    )
    assert entry.state == credit_ledger.STATE_RESERVED
    assert entry.delta == -5
    assert entry.product_key == "idle:BLINKING"


@pytest.mark.anyio
async def test_two_concurrent_reservations_cannot_exceed_the_balance():
    await _fund(7)
    await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    with pytest.raises(generation_credits.GenerationCreditError) as e:
        await generation_credits.reserve_for_action(
            user_id=USER, action_id="BLINKING", idempotency_key="gen:2"
        )
    assert e.value.code == "INSUFFICIENT_CREDITS"
    assert await _balance() == 2


@pytest.mark.anyio
async def test_free_products_do_not_reserve():
    """BREATHING 은 무료다 — 예약을 요구하면 무료가 아니게 된다."""
    product_catalog.set_price_for_tests("idle:BREATHING", 0, product_catalog.TYPE_IDLE)
    assert await generation_credits.reserve_for_action(
        user_id=USER, action_id="BREATHING", idempotency_key="gen:free"
    ) is None
    assert await _balance() == 0


@pytest.mark.anyio
async def test_an_unsold_product_cannot_be_reserved():
    """가격이 없으면 **무료가 아니라 판매 불가**다."""
    product_catalog._mock_catalog().pop("idle:BLINKING", None)
    await _fund(12)
    with pytest.raises(generation_credits.GenerationCreditError) as e:
        await generation_credits.reserve_for_action(
            user_id=USER, action_id="BLINKING", idempotency_key="gen:x"
        )
    assert e.value.code == "PRODUCT_NOT_SOLD"
    assert await _balance() == 12


# ── 2. 재시도·새로고침이 두 번 청구하지 못한다 ──────────────────────────────


@pytest.mark.anyio
async def test_the_same_key_does_not_reserve_twice():
    await _fund(12)
    first = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:same"
    )
    second = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:same"
    )
    assert first.replayed is False
    assert second.replayed is True
    assert await _balance() == 7, "재시도가 두 번 잡았다"


# ── PASS: 확정 + 영구 자산 ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_commit_does_not_charge_again_and_creates_the_asset():
    """확정은 상태만 바꾼다 — 잔액은 예약 시점에 이미 빠졌다."""
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await generation_credits.commit_for_asset(
        reservation_ledger_id=r.reservation_ledger_id,
        credits=r.credits, user_id=USER, pet_id=PET, action_id="BLINKING",
        video_url="https://cdn/s1.mp4", source_job_id="job1",
    )

    assert await _balance() == 7, "확정이 두 번째로 청구했다"
    entry = next(
        e for e in credit_ledger.mock_entries(USER)
        if e.reason == credit_ledger.REASON_IDLE_GENERATION
    )
    assert entry.state == credit_ledger.STATE_COMMITTED

    assets = await owned_assets.list_for_pet(USER, PET)
    assert len(assets) == 1
    assert assets[0].product_key == "idle:BLINKING"
    assert assets[0].credits_spent == 5
    assert assets[0].ledger_id == r.reservation_ledger_id
    assert assets[0].source == owned_assets.SOURCE_PURCHASE


@pytest.mark.anyio
async def test_generating_again_costs_again_and_owns_both():
    """
    **Sleeping #1 · #2 가 모두 영구 소유.** 다시 만들면 다시 낸다.
    """
    await _fund(12)
    for n in (1, 2):
        r = await generation_credits.reserve_for_action(
            user_id=USER, action_id="BLINKING", idempotency_key=f"gen:{n}"
        )
        await generation_credits.commit_for_asset(
            reservation_ledger_id=r.reservation_ledger_id, credits=r.credits,
            user_id=USER, pet_id=PET, action_id="BLINKING",
            video_url=f"https://cdn/s{n}.mp4", source_job_id=f"job{n}",
        )

    assert await _balance() == 2, "두 번째 생성이 과금되지 않았다"
    assert await owned_assets.count_for_product(USER, PET, "idle:BLINKING") == 2
    urls = {a.video_url for a in await owned_assets.list_for_pet(USER, PET)}
    assert len(urls) == 2


@pytest.mark.anyio
async def test_playing_an_owned_asset_costs_nothing():
    """
    재생은 언제나 0 크레딧이다. 이 모듈에는 재생 경로가 없다 — 조회만으로
    잔액이 움직이지 않는다는 것을 확인한다.
    """
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await generation_credits.commit_for_asset(
        reservation_ledger_id=r.reservation_ledger_id, credits=r.credits,
        user_id=USER, pet_id=PET, action_id="BLINKING",
        video_url="https://cdn/s1.mp4", source_job_id="job1",
    )
    before = await _balance()

    for _ in range(3):
        await owned_assets.list_for_pet(USER, PET)
        await owned_assets.count_for_product(USER, PET, "idle:BLINKING")

    assert await _balance() == before == 7


# ── 3. 실패한 생성이 크레딧을 영구히 소모하지 않는다 ────────────────────────


@pytest.mark.anyio
async def test_release_returns_the_credits():
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    assert await _balance() == 7

    assert await generation_credits.release_quietly(r.reservation_ledger_id) is True
    assert await _balance() == 12, "실패한 생성이 크레딧을 삼켰다"

    reasons = [e.reason for e in credit_ledger.mock_entries(USER)]
    assert credit_ledger.REASON_RESERVATION_RELEASE in reasons
    assert credit_ledger.mock_balance(USER) == 12


@pytest.mark.anyio
async def test_release_is_idempotent():
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await generation_credits.release_quietly(r.reservation_ledger_id)
    await generation_credits.release_quietly(r.reservation_ledger_id)
    assert await _balance() == 12, "두 번 해제해 크레딧이 늘었다"


@pytest.mark.anyio
async def test_a_failed_generation_leaves_no_asset():
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await generation_credits.release_quietly(r.reservation_ledger_id)
    assert await owned_assets.list_for_pet(USER, PET) == []


@pytest.mark.anyio
async def test_a_committed_reservation_cannot_be_released():
    """확정된 예약을 해제하면 자산은 남고 크레딧은 돌아간다 — 공짜로 주는 셈이다."""
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await credit_reservation.commit(r.reservation_ledger_id)

    with pytest.raises(credit_reservation.ReservationError) as e:
        await credit_reservation.release(r.reservation_ledger_id)
    assert e.value.code == "RESERVATION_NOT_OPEN"
    assert await _balance() == 7


@pytest.mark.anyio
async def test_a_released_reservation_cannot_be_committed():
    """해제된 예약을 되살리면 돌려준 크레딧을 다시 가져가는 것이다."""
    await _fund(12)
    r = await generation_credits.reserve_for_action(
        user_id=USER, action_id="BLINKING", idempotency_key="gen:1"
    )
    await credit_reservation.release(r.reservation_ledger_id)

    with pytest.raises(credit_reservation.ReservationError) as e:
        await credit_reservation.commit(r.reservation_ledger_id)
    assert e.value.code == "RESERVATION_NOT_OPEN"
    assert await _balance() == 12


# ── 1. 예약 없이는 제출하지 않는다 ──────────────────────────────────────────


@pytest.mark.anyio
async def test_submitting_without_a_reservation_is_refused():
    """
    호출부 실수로 과금 없이 유료 생성이 도는 것을 막는 마지막 지점.
    (스키마의 credit_sessions_paid_has_reservation 이 그 다음 방어선이다.)
    """
    from backend.services import premium_generation

    with pytest.raises(premium_generation.PremiumSubmitError) as e:
        await premium_generation.submit_premium_action(
            user_id=USER, pet_id=PET, action_id="BLINKING",
            pet_image_url="https://cdn/pet.png", api_base="https://api.test",
            reservation_ledger_id=None, credits_reserved=5,
        )
    assert "예약" in str(e.value)


def test_the_schema_forbids_a_paid_session_without_a_reservation():
    """
    ⚠️ 세션 쪽 제약에 `legacy_charge` 예외가 하나 있다.

    credits_charged 는 `default 4` 인 **기존** 컬럼이라 예약 이전의 모든 세션이
    유료로 보인다. 예외 없이 걸면 마이그레이션이 기존 행에서 실패하고(실측),
    아직 은퇴하지 못한 4크레딧 기기 팩의 운영 insert 도 막힌다 — 그 경로는 차감이
    insert 보다 먼저라 고객이 크레딧만 잃는다. docs/LEGACY_RETIREMENT.md §5.

    예외의 범위는 test_legacy_charge_exemption.py 가 지킨다 (호출부 하나, 기본값
    False, 예약 없는 **새** 유료 세션은 여전히 거부).

    작업 표(scene_generation_jobs)에는 예외가 없다 — credits_reserved 가
    `default 0` 인 **새** 컬럼이라 기존 행이 걸리지 않았기 때문이다.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "credit_sessions_paid_has_reservation" in sql
    assert (
        "credits_charged = 0 or legacy_charge or reservation_ledger_id is not null" in sql
    )
    # 작업 표에는 예외 없는 원래 규칙이 그대로다.
    assert "scene_jobs_paid_has_reservation" in sql
    assert "credits_reserved = 0 or reservation_ledger_id is not null" in sql


def test_the_purchase_path_reserves_rather_than_deducts():
    """
    예약이 차감을 **대체**했는지 구조로 확인한다. 둘이 함께 있으면 두 번 청구된다
    (실제로 처음 구현에서 그렇게 됐고, 잔액 테스트가 잡았다).
    """
    src = (REPO / "backend" / "services" / "premium_purchase.py").read_text(encoding="utf-8")
    body = src.split("async def purchase(", 1)[1].split("\nasync def ", 1)[0]
    assert "credit_reservation.reserve(" in body
    assert "await deduct_credits(" not in body, "차감과 예약이 함께 있다 — 이중 청구"


def test_promotion_commits_the_reservation():
    src = (REPO / "backend" / "services" / "generated_motions_service.py").read_text(
        encoding="utf-8"
    )
    assert "generation_credits.commit_for_asset" in src


def test_terminal_failure_releases_the_reservation():
    src = (REPO / "backend" / "services" / "credit_generation_service.py").read_text(
        encoding="utf-8"
    )
    assert "generation_credits.release_quietly" in src
