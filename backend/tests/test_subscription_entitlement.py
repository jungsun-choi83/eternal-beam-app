"""
Phase 2 — 프리미엄 생성 인가는 **크레딧이 아니라 월 구독**이다.

고정하려는 계약 (PM 확정):

    INITIAL_BUY  → 구독 ACTIVE
                 → 프리미엄 모션을 **자동 생성하지 않는다**
    EXPIRED      → 프리미엄 생성 차단
                 → BREATHING 은 계속 재생 가능
                 → 이미 READY 인 자산과 설정은 그대로 남는다
    RENEWAL      → 다시 ACTIVE
                 → 기존 READY 자산을 **재사용**한다
                 → 재생성하지 않는다

그리고 뒤집힌 판정 두 가지가 바로잡혔는지:
    * 구독 중 + 잔액 0      → 생성 **가능**  (예전엔 불가능)
    * 만료 + 크레딧 충분    → 생성 **불가능** (예전엔 가능)

프로바이더는 부르지 않는다 — 제출 계층을 목업하고 인가/제출 여부만 검사한다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS, PREMIUM_ACTIONS
from backend.services import (
    premium_entitlement,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    wallet_service,
)
from backend.services.subscription_webhook_service import handle_subscription_webhook

USER = "sub_user_1"
PET = "sub_pet_1"
IMG = "https://example.test/cutout.png"
BUNDLE = premium_purchase.KIND_IDLE_BUNDLE
COME_CLOSER = premium_purchase.action_kind("COME_CLOSER")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """DB 없이 인메모리 구독/지갑/원장으로 돈다."""
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    # Phase 7H: 이 파일은 **레거시 이행 계약**을 검증한다 — 명시 회귀 스위치.
    monkeypatch.setenv("PREMIUM_FULFILLMENT", "legacy")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)  # 기본=켜짐
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
    """canonical / 진행중 자산 + 제출 기록."""

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
            action_id=action_id,
            session_id=f"sess_{action_id}",
            external_id=f"ext_{action_id}",
            provider="mock",
            provider_model=None,
            keyframe_url=pet_image_url,
        )

    monkeypatch.setattr(premium_purchase.motions_svc, "list_motions_for_pet", list_motions)
    monkeypatch.setattr(
        premium_purchase.motions_svc, "list_active_action_ids_for_pet", list_active
    )
    monkeypatch.setattr(premium_generation, "submit_premium_action", submit)
    return w


async def _webhook(event: str, *, user_id: str = USER, tx: str | None = None):
    return await handle_subscription_webhook(
        {
            "store_type": "mock",
            "notification_type": event,
            "user_id": user_id,
            "plan_id": "standard_subscription",
            "transaction_id": tx or f"tx_{event.lower()}_{user_id}",
        }
    )


async def _buy(kind: str = BUNDLE, *, user_id: str = USER, image: str | None = IMG):
    return await premium_purchase.purchase(
        user_id=user_id,
        pet_id=PET,
        kind=kind,
        pet_image_url=image,
        api_base="https://api.test",
    )


# ── 프리미엄 행동 집합 ────────────────────────────────────────────────────────


def test_premium_behaviors_are_exactly_the_five_pm_named():
    """BLINKING/EAR_TWITCHING/HEAD_TILTING/TAIL_WAGGING/COME_CLOSER — 그리고 그게 전부."""
    assert set(PREMIUM_ACTIONS) == {
        "BLINKING",
        "EAR_TWITCHING",
        "HEAD_TILTING",
        "TAIL_WAGGING",
        "COME_CLOSER",
    }


def test_breathing_is_never_a_premium_behavior():
    """무료 홈 루프 — 구독 판정 자체가 적용되지 않는다."""
    assert "BREATHING" not in PREMIUM_ACTIONS
    assert "BREATHING" not in IDLE_EVENTS
    assert "BREATHING" not in PET_ACTIONS


def test_legacy_device_pack_actions_are_not_premium():
    """IDLE/TOUCH/VOICE/NFC 는 계속 크레딧 경로다 — 구독 게이트 밖."""
    for legacy in ("IDLE", "TOUCH", "VOICE", "NFC"):
        assert legacy not in PREMIUM_ACTIONS


# ── INITIAL_BUY ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_initial_buy_activates_the_subscription(world):
    r = await _webhook("INITIAL_BUY")
    assert r.subscription_status == "active"
    assert r.entitled is True

    ent = await premium_entitlement.get_entitlement(USER)
    assert ent.entitled is True
    assert ent.status == "active"


@pytest.mark.anyio
async def test_initial_buy_does_not_generate_anything(world):
    """구독을 샀다고 해서 5종이 자동으로 만들어지지 않는다 — 프로바이더 비용 통제."""
    await _webhook("INITIAL_BUY")

    assert world.submitted == []
    assert world.active == set()
    assert world.ready == {}

    # 5종 전부 여전히 MISSING 이다 — 사용자가 나중에 직접 고른다.
    state = await premium_purchase.asset_state(USER, PET, tuple(PREMIUM_ACTIONS))
    assert sorted(state.missing) == sorted(PREMIUM_ACTIONS)


@pytest.mark.anyio
async def test_subscribed_user_generates_only_what_they_asked_for(world):
    """선택한 것만 만든다. COME_CLOSER 를 요청해도 아이들 모션은 건드리지 않는다."""
    await _webhook("INITIAL_BUY")
    r = await _buy(COME_CLOSER)

    assert r.submitted == ["COME_CLOSER"]
    assert set(world.submitted) == {"COME_CLOSER"}
    for idle in IDLE_EVENTS:
        assert idle not in world.submitted


# ── 인가 방향이 뒤집혀 있던 두 경우 ───────────────────────────────────────────


@pytest.mark.anyio
async def test_subscriber_with_zero_credits_can_generate(world):
    """구독 중이면 잔액 0이어도 생성된다. 예전에는 INSUFFICIENT_CREDITS 였다."""
    await _webhook("INITIAL_BUY")
    await wallet_service.deduct_credits(USER, 12)  # 지급받은 월 크레딧을 모두 소진
    w = await wallet_service.get_wallet(USER)
    assert w.current_credits == 0

    r = await _buy(BUNDLE)

    assert r.submitted, "구독자가 잔액 0이라는 이유로 막혔다"
    assert r.credits_charged == 0


@pytest.mark.anyio
async def test_expired_user_with_credits_cannot_generate(world):
    """만료되면 크레딧이 남아 있어도 프리미엄 생성은 막힌다."""
    await _webhook("INITIAL_BUY")
    await _webhook("EXPIRATION")
    w = await wallet_service.get_wallet(USER)
    assert w.current_credits >= 4, "레거시 재원용 크레딧은 남아 있어야 한다"

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(BUNDLE)

    assert e.value.code == "SUBSCRIPTION_REQUIRED"
    assert e.value.status == 402
    assert world.submitted == [], "차단됐는데 제출이 일어났다"


@pytest.mark.anyio
async def test_user_with_no_subscription_history_cannot_generate(world):
    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(BUNDLE)
    assert e.value.code == "SUBSCRIPTION_REQUIRED"
    assert world.submitted == []


# ── EXPIRED ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_expiry_keeps_ready_assets_and_preferences(world):
    """만료는 자산을 지우지 않는다 — 재생 권한의 권위는 generated_motions 다."""
    await _webhook("INITIAL_BUY")
    await _buy(BUNDLE)
    world.active.clear()
    world.ready = {a: f"https://cdn.test/{a}.mp4" for a in IDLE_EVENTS}

    await _webhook("EXPIRATION")

    state = await premium_purchase.asset_state(USER, PET, tuple(IDLE_EVENTS))
    assert sorted(state.ready) == sorted(IDLE_EVENTS), "만료로 READY 자산이 사라졌다"
    assert state.missing == []


@pytest.mark.anyio
async def test_expiry_does_not_touch_the_wallet_or_legacy_credits(world):
    """레거시 IDLE/TOUCH/VOICE/NFC 재원은 구독 만료와 무관하게 남는다."""
    await _webhook("INITIAL_BUY")
    before = (await wallet_service.get_wallet(USER)).current_credits

    await _webhook("EXPIRATION")

    after = (await wallet_service.get_wallet(USER)).current_credits
    assert after == before


@pytest.mark.anyio
async def test_canceled_within_grace_period_can_still_generate(world):
    """해지했지만 결제 기간이 남았다 — is_entitled 의 유예 규칙 그대로."""
    await _webhook("INITIAL_BUY")
    await _webhook("CANCEL")

    ent = await premium_entitlement.get_entitlement(USER)
    assert ent.status == "canceled"
    assert ent.entitled is True

    r = await _buy(BUNDLE)
    assert r.submitted


# ── RENEWAL ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_renewal_reactivates_and_reuses_ready_assets(world):
    """갱신은 기존 READY 를 재사용한다 — 재생성하지 않는다."""
    await _webhook("INITIAL_BUY")
    await _buy(BUNDLE)
    world.active.clear()
    world.ready = {a: f"https://cdn.test/{a}.mp4" for a in IDLE_EVENTS}
    submitted_before = list(world.submitted)

    await _webhook("EXPIRATION")
    await _webhook("RENEWAL")

    ent = await premium_entitlement.get_entitlement(USER)
    assert ent.entitled is True

    r = await _buy(BUNDLE)
    assert r.status == "ready"
    assert r.submitted == [], "갱신 후 이미 있는 자산을 다시 만들었다"
    assert world.submitted == submitted_before
    assert sorted(r.ready) == sorted(IDLE_EVENTS)


@pytest.mark.anyio
async def test_renewal_does_not_generate_by_itself(world):
    """갱신 웹훅 자체는 아무것도 제출하지 않는다."""
    await _webhook("INITIAL_BUY")
    await _webhook("RENEWAL", tx="tx_renew_2")
    assert world.submitted == []


@pytest.mark.anyio
async def test_renewal_only_fills_what_is_still_missing(world):
    """일부만 READY 인 채로 갱신되면 **누락분만** 만든다."""
    await _webhook("INITIAL_BUY")
    world.ready = {IDLE_EVENTS[0]: "https://cdn.test/a.mp4"}
    await _webhook("RENEWAL", tx="tx_renew_3")

    r = await _buy(BUNDLE)

    assert IDLE_EVENTS[0] not in r.submitted, "이미 READY 인 것을 다시 만들었다"
    assert set(r.submitted).issubset(set(IDLE_EVENTS[1:]))


# ── 구독 ≠ 무제한 재생성 ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_subscription_is_not_unlimited_regeneration(world):
    """전부 READY 인데 다시 요청 → 제출 0건. 구독은 재생성권이 아니다."""
    await _webhook("INITIAL_BUY")
    world.ready = {a: f"https://cdn.test/{a}.mp4" for a in IDLE_EVENTS}

    r1 = await _buy(BUNDLE)
    r2 = await _buy(BUNDLE)

    assert r1.submitted == [] and r2.submitted == []
    assert world.submitted == []
    assert r1.status == "ready" and r2.status == "ready"


@pytest.mark.anyio
async def test_repeat_request_while_generating_does_not_resubmit(world):
    await _webhook("INITIAL_BUY")
    first = await _buy(BUNDLE)
    count_after_first = len(world.submitted)

    second = await _buy(BUNDLE)

    assert second.submitted == []
    assert len(world.submitted) == count_after_first
    assert first.status == "processing"


# ── 과금 경계 ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_subscription_generation_never_charges_credits(world):
    await _webhook("INITIAL_BUY")
    before = (await wallet_service.get_wallet(USER)).current_credits

    r = await _buy(BUNDLE)

    after = (await wallet_service.get_wallet(USER)).current_credits
    assert r.credits_charged == 0
    assert after == before, "구독 모드에서 크레딧이 차감됐다"


@pytest.mark.anyio
async def test_subscription_generation_writes_no_purchase_ledger_row(world):
    """구독 모드는 premium_purchases 를 건드리지 않는다 — 크레딧 시대 기록 보존."""
    await _webhook("INITIAL_BUY")
    await _buy(BUNDLE)

    row = await premium_purchase.find_active_purchase(USER, PET, BUNDLE)
    assert row is None


# ── 롤백 스위치 ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_flag_off_restores_the_credit_path(world, monkeypatch: pytest.MonkeyPatch):
    """PREMIUM_REQUIRES_SUBSCRIPTION=0 → 구독 없이도 크레딧으로 생성된다."""
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "0")
    await wallet_service.add_credits(USER, 5)

    r = await _buy(BUNDLE)

    assert r.credits_charged == 1
    assert r.submitted
    row = await premium_purchase.find_active_purchase(USER, PET, BUNDLE)
    assert row is not None


# ── 조회 실패는 통과가 아니다 ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_subscription_lookup_failure_blocks_generation(
    world, monkeypatch: pytest.MonkeyPatch
):
    """fail-closed — 구독 상태를 못 읽으면 생성하지 않는다(프로바이더 비용 보호)."""

    async def boom(user_id):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(sub_store, "get_subscription", boom)

    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(BUNDLE)

    assert e.value.code == "SUBSCRIPTION_CHECK_UNAVAILABLE"
    assert e.value.status == 503
    assert world.submitted == []


# ── 소유권이 인가보다 먼저 ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_other_users_pet_is_rejected_as_not_owned_not_as_unsubscribed(world):
    """남의 펫은 구독 유무와 무관하게 PET_NOT_OWNED — 존재가 새어 나가지 않게."""
    await _webhook("INITIAL_BUY", user_id="owner_user", tx="tx_owner")
    await premium_purchase.assert_pet_owned("owner_user", PET)

    await _webhook("INITIAL_BUY", user_id="intruder", tx="tx_intruder")
    with pytest.raises(premium_purchase.PurchaseError) as e:
        await _buy(BUNDLE, user_id="intruder")

    assert e.value.code == "PET_NOT_OWNED"
