"""
Phase 4 — Behavior Library 의 **행동 단건 생성** 계약.

바뀐 것 하나: ACTION:<ID> 의 대상이 PET_ACTIONS 에서 PREMIUM_ACTIONS 로 넓어졌다.
그 전에는 아이들 모션을 만들 방법이 IDLE_BUNDLE 뿐이었고, 그러면 BLINKING 하나를
눌러도 4종이 전부 제출된다 — "선택한 것만 생성한다"는 규칙이 깨진다.

고정하는 것:
  * BLINKING 을 요청하면 **BLINKING 만** 제출된다.
  * READY/진행 중인 행동은 다시 제출되지 않는다 (구독은 재생성권이 아니다).
  * 멤버가 아니면 단건 요청도 402 다.
  * 레거시 IDLE/TOUCH/VOICE/NFC 는 이 경로로 들어올 수 없다.
  * 번들 경로는 그대로 살아 있다 (크레딧 모드 롤백 계약).

프로바이더는 부르지 않는다 — 제출 계층을 목업한다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS, PREMIUM_ACTIONS
from backend.services import (
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    wallet_service,
)
from backend.services.subscription_webhook_service import handle_subscription_webhook

USER = "lib_user"
PET = "lib_pet"
IMG = "https://example.test/cutout.png"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    # Phase 7H: 이 파일은 **레거시 이행 계약**을 검증한다 — 명시 회귀 스위치.
    monkeypatch.setenv("PREMIUM_FULFILLMENT", "legacy")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()
    yield
    premium_purchase.__reset_for_tests()
    wallet_service._MOCK_WALLETS.clear()
    sub_store._MOCK_SUBS.clear()
    sub_store._MOCK_EVENTS.clear()


class _World:
    def __init__(self) -> None:
        self.ready: dict[str, str] = {}
        self.active: set[str] = set()
        self.submitted: list[str] = []


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
        return sorted(w.active)

    async def submit(*, user_id, pet_id, action_id, pet_image_url, api_base, **kw):
        w.submitted.append(action_id)
        w.active.add(action_id)
        return premium_generation.SubmitResult(
            action_id=action_id, session_id=f"s_{action_id}", external_id=f"e_{action_id}",
            provider="mock", provider_model=None, keyframe_url=pet_image_url,
        )

    monkeypatch.setattr(premium_purchase.motions_svc, "list_motions_for_pet", list_motions)
    monkeypatch.setattr(premium_purchase.motions_svc, "list_active_action_ids_for_pet", list_active)
    monkeypatch.setattr(premium_generation, "submit_premium_action", submit)
    return w


async def _member():
    return await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "INITIAL_BUY",
        "user_id": USER, "plan_id": "standard_subscription", "transaction_id": "tx1",
    })


async def _generate(action_id: str, *, user_id: str = USER):
    return await premium_purchase.purchase(
        user_id=user_id, pet_id=PET,
        kind=premium_purchase.action_kind(action_id),
        pet_image_url=IMG, api_base="https://api.test",
    )


# ── kind 해석 ────────────────────────────────────────────────────────────────


def test_every_premium_behavior_is_individually_addressable():
    """5종 전부 단건 kind 로 지정할 수 있어야 Behavior Library 가 성립한다."""
    for action in PREMIUM_ACTIONS:
        kind = premium_purchase.action_kind(action)
        assert premium_purchase.resolve_kind(kind) == kind
        assert premium_purchase.target_actions(kind) == (action,), f"{action} 이 단건이 아니다"


def test_bare_behavior_id_resolves_to_single_action_kind():
    assert premium_purchase.resolve_kind("BLINKING") == "ACTION:BLINKING"
    assert premium_purchase.target_actions("ACTION:BLINKING") == ("BLINKING",)


def test_legacy_device_pack_actions_are_still_rejected():
    """IDLE/TOUCH/VOICE/NFC 는 크레딧 경로다 — 단건 프리미엄으로 들어올 수 없다."""
    for legacy in ACTION_ORDER:
        with pytest.raises(premium_purchase.PurchaseError) as e:
            premium_purchase.resolve_kind(premium_purchase.action_kind(legacy))
        assert e.value.code == "ACTION_NOT_SUPPORTED"


def test_breathing_is_not_addressable():
    with pytest.raises(premium_purchase.PurchaseError):
        premium_purchase.resolve_kind("ACTION:BREATHING")


def test_unknown_kinds_are_still_rejected():
    for bad in ("FREE_STUFF", "", "ACTION:", "ACTION:NOPE"):
        with pytest.raises(premium_purchase.PurchaseError):
            premium_purchase.resolve_kind(bad)


def test_bundle_kind_still_works():
    """레거시 크레딧 모드의 번들 계약은 그대로다."""
    assert premium_purchase.target_actions(premium_purchase.KIND_IDLE_BUNDLE) == tuple(IDLE_EVENTS)


# ── 선택한 것만 생성 ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_generating_one_behavior_submits_only_that_one(world):
    """**Phase 4 의 핵심.** BLINKING 을 눌러도 나머지 3종이 따라오지 않는다."""
    await _member()
    r = await _generate("BLINKING")

    assert r.submitted == ["BLINKING"]
    assert world.submitted == ["BLINKING"]
    for other in ("EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING", "COME_CLOSER"):
        assert other not in world.submitted, f"{other} 가 자동으로 생성됐다"


@pytest.mark.anyio
async def test_each_behavior_can_be_generated_independently(world):
    await _member()
    for action in ("HEAD_TILTING", "COME_CLOSER"):
        await _generate(action)
        world.active.clear()
        world.ready[action] = f"https://cdn.test/{action}.mp4"

    assert world.submitted == ["HEAD_TILTING", "COME_CLOSER"]


@pytest.mark.anyio
async def test_generating_all_five_one_by_one_touches_each_once(world):
    await _member()
    for action in PREMIUM_ACTIONS:
        await _generate(action)
        world.active.clear()
        world.ready[action] = f"https://cdn.test/{action}.mp4"

    assert sorted(world.submitted) == sorted(PREMIUM_ACTIONS)
    assert len(world.submitted) == len(set(world.submitted)), "같은 행동이 두 번 제출됐다"


# ── 재생성 금지 ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ready_behavior_is_not_regenerated(world):
    await _member()
    world.ready["BLINKING"] = "https://cdn.test/BLINKING.mp4"

    r = await _generate("BLINKING")

    assert r.status == "ready"
    assert r.submitted == []
    assert world.submitted == [], "READY 인 행동을 다시 만들었다"


@pytest.mark.anyio
async def test_generating_behavior_is_not_resubmitted(world):
    await _member()
    await _generate("TAIL_WAGGING")
    count = len(world.submitted)

    second = await _generate("TAIL_WAGGING")

    assert second.submitted == []
    assert len(world.submitted) == count, "진행 중인 행동을 다시 제출했다"


@pytest.mark.anyio
async def test_canonical_ready_assets_are_reused_across_requests(world):
    """이미 만든 것은 계속 재사용된다 — 재생성 없이 URL 이 그대로 돌아온다."""
    await _member()
    world.ready["COME_CLOSER"] = "https://cdn.test/COME_CLOSER.mp4"

    r = await _generate("COME_CLOSER")

    assert r.ready["COME_CLOSER"] == "https://cdn.test/COME_CLOSER.mp4"
    assert world.submitted == []


# ── 멤버십 게이트 ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_non_member_cannot_generate_a_single_behavior(world):
    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _generate("BLINKING")
    assert e.value.code == "SUBSCRIPTION_REQUIRED"
    assert world.submitted == []


@pytest.mark.anyio
async def test_expired_member_cannot_generate_but_keeps_ready(world):
    await _member()
    world.ready["BLINKING"] = "https://cdn.test/BLINKING.mp4"
    await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "EXPIRATION",
        "user_id": USER, "plan_id": "standard_subscription", "transaction_id": "tx2",
    })

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _generate("EAR_TWITCHING")
    assert e.value.code == "SUBSCRIPTION_REQUIRED"

    state = await premium_purchase.asset_state(USER, PET, tuple(PREMIUM_ACTIONS))
    assert state.ready == {"BLINKING": "https://cdn.test/BLINKING.mp4"}, "만료가 자산을 지웠다"


@pytest.mark.anyio
async def test_single_behavior_generation_never_charges_credits(world):
    await _member()
    before = (await wallet_service.get_wallet(USER)).current_credits

    r = await _generate("HEAD_TILTING")

    assert r.credits_charged == 0
    assert (await wallet_service.get_wallet(USER)).current_credits == before
