"""
프리미엄 구매 모델 — 과금 정확성 계약.

지키려는 것(확정된 사업 규칙):
  * IDLE_BUNDLE = **정확히 1 크레딧**, 등록된 아이들 이벤트 전체. 개수와 무관하다.
  * ACTION:<ID> = 1 크레딧, 액션 1건.
  * 크레딧은 **생성/잠금 해제**에만 쓴다 — 재생은 언제나 0원이고, 잔액이 0이 되어도
    이미 승격된 자산은 계속 재생된다.
  * 새로고침·다중 탭·재시도·Preview/Memorial 중복이 이중 과금을 만들지 못한다.
  * 크레딧만 잃고 생성 작업이 없는 상태가 절대 만들어지지 않는다.

프로바이더는 부르지 않는다 — 제출 계층을 목업하고 원장/지갑만 검사한다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
from backend.services import premium_generation, premium_purchase
from backend.services import wallet_service

USER = "user_alice"
OTHER = "user_bob"
PET = "pet_alice_1"
IMG = "https://example.test/cutout.png"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """DB 없이 인메모리 원장/지갑으로 돈다. 매 테스트마다 초기화."""
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    yield
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()


class _World:
    """canonical / 진행중 자산을 흉내 내는 최소 세계."""

    def __init__(self) -> None:
        self.ready: dict[str, str] = {}
        self.active: set[str] = set()
        self.submitted: list[str] = []
        self.submit_fails = False


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    class _Motion:
        def __init__(self, action_id: str, url: str):
            self.action_id = action_id
            self.video_url = url

    async def list_motions(user_id, pet_id=None):
        return [_Motion(a, u) for a, u in w.ready.items()]

    async def list_active(user_id, pet_id=None):
        return list(w.active)

    async def submit(*, user_id, pet_id, action_id, pet_image_url, api_base, keyframe_url=None):
        if w.submit_fails:
            raise premium_generation.PremiumSubmitError("boom", stage="submit")
        w.submitted.append(action_id)
        w.active.add(action_id)
        return premium_generation.SubmitResult(
            action_id=action_id, session_id="s", external_id="e",
            provider="luma", provider_model=None, keyframe_url="k",
        )

    monkeypatch.setattr(premium_purchase.motions_svc, "list_motions_for_pet", list_motions)
    monkeypatch.setattr(
        premium_purchase.motions_svc, "list_active_action_ids_for_pet", list_active
    )
    monkeypatch.setattr(premium_generation, "submit_premium_action", submit)
    return w


async def _grant(user: str, credits: int) -> None:
    await wallet_service.add_credits(user, credits)


async def _balance(user: str) -> int:
    wallet = await wallet_service.get_wallet(user, create_if_missing=True)
    return wallet.current_credits


async def _buy(kind: str, *, user: str = USER, pet: str = PET, image: str | None = IMG):
    return await premium_purchase.purchase(
        user_id=user, pet_id=pet, kind=kind, pet_image_url=image, api_base="https://api.test"
    )


# ── 가격 모델 ────────────────────────────────────────────────────────────────


def test_bundle_covers_the_registered_idle_set_not_a_hardcoded_four():
    """5번째 아이들 모션이 추가돼도 같은 1 크레딧 번들에 들어와야 한다."""
    assert premium_purchase.target_actions(premium_purchase.KIND_IDLE_BUNDLE) == tuple(IDLE_EVENTS)
    assert premium_purchase.credits_for_kind(premium_purchase.KIND_IDLE_BUNDLE) == 1


def test_breathing_is_not_part_of_the_paid_bundle():
    """BREATHING 은 무료 기본 모션이다 — 유료 번들 대상에 있으면 안 된다."""
    targets = premium_purchase.target_actions(premium_purchase.KIND_IDLE_BUNDLE)
    for free in ("BREATHING", "IDLE"):
        assert free not in targets


def test_action_events_are_one_credit_each():
    for action in PET_ACTIONS:
        kind = premium_purchase.action_kind(action)
        assert premium_purchase.credits_for_kind(kind) == 1
        assert premium_purchase.target_actions(kind) == (action,)


def test_unknown_kind_is_rejected():
    with pytest.raises(premium_purchase.PurchaseError):
        premium_purchase.resolve_kind("FREE_STUFF")
    with pytest.raises(premium_purchase.PurchaseError):
        premium_purchase.resolve_kind("")


# ── 아이들 번들 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_first_bundle_purchase_deducts_exactly_one_credit(world):
    await _grant(USER, 5)
    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 1
    assert await _balance(USER) == 4
    # 누락 전부를 겨냥한다(큐 상한 안에서).
    assert set(world.submitted) <= set(IDLE_EVENTS)
    assert world.submitted


@pytest.mark.anyio
async def test_many_missing_idle_events_still_cost_only_one_credit(world):
    """4종이 전부 없어도 1 크레딧이다 — 이벤트당 과금이 아니다."""
    await _grant(USER, 5)
    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 1
    assert await _balance(USER) == 4


@pytest.mark.anyio
async def test_partial_ready_is_reused_and_only_missing_are_generated(world):
    """예시 그대로: BLINKING·TAIL READY, EAR·HEAD 누락 → 1 크레딧, 둘만 생성."""
    world.ready = {"BLINKING": "u1", "TAIL_WAGGING": "u2"}
    await _grant(USER, 5)

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)

    assert r.credits_charged == 1
    assert await _balance(USER) == 4
    assert "BLINKING" not in world.submitted, "이미 READY 인 자산을 재생성했다"
    assert "TAIL_WAGGING" not in world.submitted
    assert set(world.submitted) <= {"EAR_TWITCHING", "HEAD_TILTING"}
    # READY 였던 것은 결과에 그대로 남는다.
    assert r.ready.get("BLINKING") == "u1"


@pytest.mark.anyio
async def test_complete_bundle_costs_zero_and_does_not_regenerate(world):
    world.ready = {e: f"url_{e}" for e in IDLE_EVENTS}
    await _grant(USER, 5)

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)

    assert r.credits_charged == 0
    assert r.status == "ready"
    assert await _balance(USER) == 5
    assert world.submitted == [], "READY 인데 재생성했다"


@pytest.mark.anyio
async def test_repeated_bundle_purchase_never_double_charges(world):
    """새로고침·다중 탭·재시도 — 두 번째부터는 0원이다."""
    await _grant(USER, 5)
    first = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert first.credits_charged == 1

    for _ in range(4):
        again = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
        assert again.credits_charged == 0
        assert again.already_owned is True
    assert await _balance(USER) == 4


@pytest.mark.anyio
async def test_purchase_while_generation_active_charges_zero(world):
    await _grant(USER, 5)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert world.active, "생성이 시작되지 않았다"

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 0
    assert r.status == "processing"
    assert await _balance(USER) == 4


@pytest.mark.anyio
async def test_zero_balance_does_not_remove_ready_playback_access(world):
    """확정 규칙: '지금 크레딧이 있다' 와 '이미 만든 모션을 갖고 있다' 는 다르다."""
    await _grant(USER, 1)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert await _balance(USER) == 0

    world.active.clear()
    world.ready = {e: f"url_{e}" for e in IDLE_EVENTS}

    state = await premium_purchase.asset_state(USER, PET, tuple(IDLE_EVENTS))
    assert len(state.ready) == len(IDLE_EVENTS), "잔액 0이 재생 접근권을 없앴다"

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.status == "ready"
    assert r.credits_charged == 0


# ── 액션 이벤트 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_first_come_closer_deducts_one(world):
    await _grant(USER, 3)
    r = await _buy(premium_purchase.action_kind("COME_CLOSER"))
    assert r.credits_charged == 1
    assert await _balance(USER) == 2
    assert world.submitted == ["COME_CLOSER"]


@pytest.mark.anyio
async def test_ready_come_closer_costs_zero(world):
    world.ready = {"COME_CLOSER": "u"}
    await _grant(USER, 3)
    r = await _buy(premium_purchase.action_kind("COME_CLOSER"))
    assert r.credits_charged == 0
    assert r.status == "ready"
    assert await _balance(USER) == 3
    assert world.submitted == []


@pytest.mark.anyio
async def test_processing_come_closer_costs_zero(world):
    """진행 중은 언제나 0원 — 구매 기록이 없어도 그렇다(만들 것이 없으므로)."""
    world.active = {"COME_CLOSER"}
    await _grant(USER, 3)
    r = await _buy(premium_purchase.action_kind("COME_CLOSER"))
    assert r.credits_charged == 0
    assert r.status == "processing"
    assert await _balance(USER) == 3
    assert world.submitted == []


@pytest.mark.anyio
async def test_bundle_with_all_targets_active_costs_zero(world):
    """번들도 마찬가지 — 남은 게 전부 생성 중이면 과금하지 않는다."""
    world.active = set(IDLE_EVENTS)
    await _grant(USER, 3)
    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 0
    assert r.status == "processing"
    assert await _balance(USER) == 3


@pytest.mark.anyio
async def test_bundle_charges_once_when_some_active_and_some_missing(world):
    """일부 생성 중 + 일부 누락 → 만들 것이 있으니 1 크레딧, 누락분만 제출."""
    world.active = {"BLINKING"}
    world.ready = {"TAIL_WAGGING": "u"}
    await _grant(USER, 3)

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)

    assert r.credits_charged == 1
    assert await _balance(USER) == 2
    assert "BLINKING" not in world.submitted, "이미 생성 중인 것을 중복 제출했다"
    assert "TAIL_WAGGING" not in world.submitted
    assert set(world.submitted) <= {"EAR_TWITCHING", "HEAD_TILTING"}


@pytest.mark.anyio
async def test_repeated_come_closer_cannot_double_charge(world):
    await _grant(USER, 3)
    assert (await _buy(premium_purchase.action_kind("COME_CLOSER"))).credits_charged == 1
    for _ in range(3):
        assert (await _buy(premium_purchase.action_kind("COME_CLOSER"))).credits_charged == 0
    assert await _balance(USER) == 2


@pytest.mark.anyio
async def test_bundle_and_action_are_priced_separately(world):
    """번들 구매가 액션을 공짜로 주지 않는다(그 반대도 마찬가지)."""
    await _grant(USER, 5)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert await _balance(USER) == 4
    await _buy(premium_purchase.action_kind("COME_CLOSER"))
    assert await _balance(USER) == 3


# ── 실패 / 환불 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_submission_failure_refunds_and_allows_retry(world):
    """크레딧만 잃고 생성 작업이 없는 상태가 만들어지면 안 된다."""
    world.submit_fails = True
    await _grant(USER, 2)

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert e.value.code == "GENERATION_SUBMIT_FAILED"
    assert await _balance(USER) == 2, "제출 실패인데 크레딧이 사라졌다"

    # 선점도 풀려 있어야 재구매가 가능하다.
    world.submit_fails = False
    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 1
    assert await _balance(USER) == 1


@pytest.mark.anyio
async def test_insufficient_credits_charges_nothing_and_generates_nothing(world):
    assert await _balance(USER) == 0  # 충전하지 않는다
    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert e.value.code == "INSUFFICIENT_CREDITS"
    assert e.value.status == 402
    assert world.submitted == []
    assert await _balance(USER) == 0


@pytest.mark.anyio
async def test_terminal_failure_with_zero_promoted_refunds_once(world):
    await _grant(USER, 2)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert await _balance(USER) == 1

    # 전부 종료됐고 승격은 0건.
    world.active.clear()
    world.ready.clear()

    assert await premium_purchase.reconcile_after_terminal(USER, PET, "BLINKING") is True
    assert await _balance(USER) == 2

    # 웹훅 재전송 — 두 번째부터는 환불이 없다.
    for _ in range(3):
        assert await premium_purchase.reconcile_after_terminal(USER, PET, "BLINKING") is False
    assert await _balance(USER) == 2, "중복 웹훅이 이중 환불을 만들었다"


@pytest.mark.anyio
async def test_partial_success_does_not_refund_the_bundle(world):
    """
    하나라도 나왔으면 번들은 값을 했다 — 스케줄러는 READY 인 것만 골라 쓴다.
    레거시 4코인 세트(부분 성공도 전액 환불)와 정책이 다른 지점이다.
    """
    await _grant(USER, 2)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert await _balance(USER) == 1

    world.active.clear()
    world.ready = {"BLINKING": "u1"}  # 1/4 성공

    assert await premium_purchase.reconcile_after_terminal(USER, PET, "EAR_TWITCHING") is False
    assert await _balance(USER) == 1


@pytest.mark.anyio
async def test_no_refund_while_siblings_still_generating(world):
    await _grant(USER, 2)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    world.ready.clear()
    # active 가 남아 있다 → 아직 종료가 아니다.
    assert world.active
    assert await premium_purchase.reconcile_after_terminal(USER, PET, "BLINKING") is False
    assert await _balance(USER) == 1


@pytest.mark.anyio
async def test_refunded_bundle_can_be_purchased_again(world):
    await _grant(USER, 2)
    await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    world.active.clear()
    world.ready.clear()
    await premium_purchase.reconcile_after_terminal(USER, PET, "BLINKING")
    assert await _balance(USER) == 2

    r = await _buy(premium_purchase.KIND_IDLE_BUNDLE)
    assert r.credits_charged == 1, "환불된 구매가 재구매를 막고 있다"


# ── 소유권 ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pet_is_claimed_on_first_use_and_protected_after(world):
    await _grant(USER, 3)
    await _grant(OTHER, 3)

    await _buy(premium_purchase.KIND_IDLE_BUNDLE)  # USER 가 PET 을 귀속

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(premium_purchase.KIND_IDLE_BUNDLE, user=OTHER)
    assert e.value.code == "PET_NOT_OWNED"
    assert e.value.status == 403
    assert await _balance(OTHER) == 3, "남의 펫 요청이 크레딧을 소모했다"


@pytest.mark.anyio
async def test_missing_identity_is_rejected(world):
    with pytest.raises(premium_purchase.PurchaseError):
        await premium_purchase.assert_pet_owned("", PET)
    with pytest.raises(premium_purchase.PurchaseError):
        await premium_purchase.assert_pet_owned(USER, "")


# ── 발견(discovery)은 과금도 생성도 하지 않는다 ──────────────────────────────


@pytest.mark.anyio
async def test_asset_state_never_charges_or_generates(world):
    await _grant(USER, 3)
    for _ in range(5):
        await premium_purchase.asset_state(USER, PET, tuple(IDLE_EVENTS))
    assert await _balance(USER) == 3
    assert world.submitted == []
