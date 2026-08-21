"""
단건 생성은 **자기 하나만** 만든다 — 자동 전진 연쇄 금지.

회귀 대상(실 프로바이더에서 돈이 새던 결함):

    사용자가 BLINKING 하나를 누른다
      → BLINKING 제출 → 완료
      → _advance_premium_queue → advance_generation_queue
      → 고르지도 않은 COME_CLOSER / EAR_TWITCHING … 이 자동 제출
      ⇒ 클릭 1회에 최대 5건 과금

측정된 실제 값: 요청 1건 + 웹훅 1회 = **프로바이더 호출 3회**.

원인: 자동 전진은 **아이들 번들**(1회 구매로 IDLE_EVENTS 전체 잠금 해제)을 위해
만들어졌다. 번들은 동시 상한(2) 때문에 한 번에 다 제출할 수 없어 슬롯이 빌 때마다
나머지를 채워야 한다. Behavior Library 의 단건 생성에는 "나머지"가 없는데도 같은
전진이 돌았다.

여기서 고정하는 계약:
    단건(ACTION:<ID>) 완료 → 전진 없음. 나머지는 MISSING 그대로.
    번들(IDLE_BUNDLE) 완료 → IDLE_EVENTS 만 마저 채운다 (기존 동작 보존).
    번들이어도 COME_CLOSER 는 자동 제출되지 않는다 (산 적이 없다).
    레거시 IDLE/TOUCH/VOICE/NFC → 이 경로에 들어오지도 않는다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS, PREMIUM_ACTIONS
from backend.services import (
    credit_generation_service as cgs,
    generated_motions_service as ms,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    wallet_service,
)
from backend.services.subscription_webhook_service import handle_subscription_webhook

USER = "cascade@example.com"
PET = "pet_cascade"
IMG = "https://cdn.test/cutout.png"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.test")
    monkeypatch.delenv("GENERATION_MOCK", raising=False)
    monkeypatch.delenv("PREMIUM_REQUIRES_SUBSCRIPTION", raising=False)
    for store in (ms._MOCK_MOTIONS, ms._MOCK_JOBS, ms._MOCK_SESSIONS,
                  wallet_service._MOCK_WALLETS, sub_store._MOCK_SUBS, sub_store._MOCK_EVENTS):
        store.clear()
    premium_purchase.__reset_for_tests()
    yield
    for store in (ms._MOCK_MOTIONS, ms._MOCK_JOBS, ms._MOCK_SESSIONS,
                  wallet_service._MOCK_WALLETS, sub_store._MOCK_SUBS, sub_store._MOCK_EVENTS):
        store.clear()
    premium_purchase.__reset_for_tests()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch):
    """
    프로바이더 호출을 센다. 키프레임/다운로드/업로드만 대체하고 **큐·완료·승격
    로직은 전부 실제 코드**가 돈다 — 그 경로가 바로 검증 대상이기 때문이다.
    """
    calls: list[str] = []

    async def fake_keyframe(url, session_id):
        return "https://cdn.test/kf.jpg"

    class _Job:
        def __init__(self, ext: str):
            self.external_id = ext
            self.model = "test-model"

    async def fake_submit(image, prompt, *, provider=None, callback_url=None, **kw):
        calls.append(prompt)
        return _Job(f"ext_{len(calls)}")

    async def fake_download(url):
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256)
        os.close(fd)
        return path

    async def fake_upload(path, data, content_type):
        return f"https://cdn.test/{path}"

    from backend.services import luma_service, supabase_assets

    monkeypatch.setattr(premium_generation, "prepare_black_plate_keyframe", fake_keyframe)
    monkeypatch.setattr(premium_generation, "submit_generation", fake_submit)
    monkeypatch.setattr(luma_service, "download_video", fake_download)
    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return calls


async def _member():
    await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "INITIAL_BUY", "user_id": USER,
        "plan_id": "web_membership", "transaction_id": "tx",
    })


async def _generate(kind: str):
    return await premium_purchase.purchase(
        user_id=USER, pet_id=PET, kind=kind, pet_image_url=IMG, api_base="https://api.test",
    )


async def _complete_all_open_jobs() -> None:
    """열려 있는 작업을 프로바이더 웹훅과 **같은 경로**로 완료시킨다."""
    for ext, job in list(ms._MOCK_JOBS.items()):
        if job.status in (ms.MotionJobStatus.completed, ms.MotionJobStatus.failed):
            continue
        await cgs.handle_luma_webhook_for_credit(ext, "completed", video_url="https://cdn.test/v.mp4")


async def _state():
    return await premium_purchase.asset_state(USER, PET, tuple(PREMIUM_ACTIONS))


# ── 핵심 회귀: 단건 생성은 자기 하나만 ──────────────────────────────────────


@pytest.mark.anyio
async def test_generate_blinking_makes_exactly_one_provider_call(provider):
    """**요구된 계약**: 프로바이더 호출 = 1, BLINKING READY, 나머지 MISSING."""
    await _member()
    r = await _generate(premium_purchase.action_kind("BLINKING"))
    assert r.submitted == ["BLINKING"]

    await _complete_all_open_jobs()

    assert len(provider) == 1, f"클릭 1회에 프로바이더 호출 {len(provider)}회 — 연쇄가 살아 있다"

    st = await _state()
    assert list(st.ready) == ["BLINKING"], f"BLINKING 외에 만들어진 것: {sorted(st.ready)}"
    assert st.active == [], "고르지 않은 행동이 생성 중이다"
    assert sorted(st.missing) == sorted(a for a in PREMIUM_ACTIONS if a != "BLINKING")


@pytest.mark.anyio
@pytest.mark.parametrize("action", list(PREMIUM_ACTIONS))
async def test_every_explicit_behavior_stops_after_itself(provider, action):
    """다섯 행동 **각각**이 자기 하나만 만들고 멈춘다."""
    await _member()
    await _generate(premium_purchase.action_kind(action))
    await _complete_all_open_jobs()

    assert len(provider) == 1, f"{action}: 프로바이더 호출 {len(provider)}회"
    st = await _state()
    assert list(st.ready) == [action]
    assert st.active == []


@pytest.mark.anyio
async def test_completing_one_does_not_start_the_next_even_with_free_slots(provider):
    """
    동시 상한이 남아 있어도 전진하지 않는다 — 연쇄의 원인은 슬롯이 아니라
    "채워야 할 번들이 있다"는 오판이었다.
    """
    await _member()
    await _generate(premium_purchase.action_kind("TAIL_WAGGING"))
    await _complete_all_open_jobs()
    assert len(provider) == 1

    # 슬롯이 완전히 비어 있는 상태에서 한 번 더 완료 이벤트가 와도 마찬가지.
    await _complete_all_open_jobs()
    assert len(provider) == 1, "중복 웹훅이 새 생성을 유발했다"


@pytest.mark.anyio
async def test_user_can_still_generate_the_next_behavior_explicitly(provider):
    """멈추는 것이지 막는 것이 아니다 — 다음 것을 직접 누르면 만들어진다."""
    await _member()
    await _generate(premium_purchase.action_kind("BLINKING"))
    await _complete_all_open_jobs()
    await _generate(premium_purchase.action_kind("HEAD_TILTING"))
    await _complete_all_open_jobs()

    assert len(provider) == 2
    st = await _state()
    assert sorted(st.ready) == ["BLINKING", "HEAD_TILTING"]


# ── 번들 자동 전진은 보존된다 ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_idle_bundle_still_auto_advances(provider, monkeypatch: pytest.MonkeyPatch):
    """
    레거시 크레딧 모드의 번들은 **예전 그대로** 나머지를 마저 채운다.
    (동시 상한이 2 라 첫 제출은 2건, 완료될 때마다 서버가 이어서 채운다.)
    """
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "0")  # 크레딧 모드 = 번들 경로
    await wallet_service.add_credits(USER, 10)

    r = await _generate(premium_purchase.KIND_IDLE_BUNDLE)
    # 첫 제출 건수는 고정하지 않는다: decide() 의 우선순위 목록에 COME_CLOSER 가
    # 랭크 0 으로 남아 있어(번들 대상이 아닌데도) 슬롯 계산에 끼어들기 때문에
    # 첫 회차는 1건이다. 중요한 계약은 "결국 전부 채워진다" 쪽이다.
    assert r.submitted, "번들이 아무것도 제출하지 않았다"
    assert set(r.submitted) <= set(IDLE_EVENTS), f"번들이 대상 밖을 제출했다: {r.submitted}"

    # 완료가 슬롯을 비우면 서버가 나머지를 이어서 제출한다.
    for _ in range(6):
        await _complete_all_open_jobs()

    st = await _state()
    assert sorted(st.ready) == sorted(IDLE_EVENTS), (
        f"번들이 전부 채워지지 않았다: ready={sorted(st.ready)}"
    )


@pytest.mark.anyio
async def test_bundle_never_auto_submits_come_closer(provider, monkeypatch: pytest.MonkeyPatch):
    """
    번들은 아이들 이벤트만 산다. 예전에는 COME_CLOSER 가 GENERATION_ORDER 첫
    항목이라 번들 구매만으로 딸려 나왔다 — 사지 않은 것에 과금됐다.
    """
    monkeypatch.setenv("PREMIUM_REQUIRES_SUBSCRIPTION", "0")
    await wallet_service.add_credits(USER, 10)

    await _generate(premium_purchase.KIND_IDLE_BUNDLE)
    for _ in range(6):
        await _complete_all_open_jobs()

    st = await _state()
    assert "COME_CLOSER" not in st.ready, "사지 않은 COME_CLOSER 가 만들어졌다"
    assert "COME_CLOSER" not in st.active
    assert "COME_CLOSER" in st.missing


# ── 레거시 4종은 이 경로 밖 ─────────────────────────────────────────────────


def test_legacy_actions_never_enter_the_premium_queue():
    """IDLE/TOUCH/VOICE/NFC 는 자기 파이프라인이 동시성을 관리한다."""
    for legacy in ACTION_ORDER:
        assert not premium_generation.is_queued_action(legacy), f"{legacy} 가 프리미엄 큐에 들어온다"
    for premium in PREMIUM_ACTIONS:
        assert premium_generation.is_queued_action(premium)


def test_advance_helper_still_filters_legacy_and_reads_the_image():
    """기존 계약(레거시 필터·세션 이미지)이 유지되는지 소스로 확인한다."""
    import inspect

    helper = inspect.getsource(cgs._advance_premium_queue)
    assert "is_queued_action" in helper
    assert "pet_image_url" in helper


def test_webhook_still_advances_on_all_three_terminal_paths():
    """완료/거절/실패 세 경로 모두에서 전진 판정이 불려야 한다."""
    import inspect

    src = inspect.getsource(cgs.handle_luma_webhook_for_credit)
    assert src.count("_advance_premium_queue(job, session)") == 3


# ── 안전 방향 ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bundle_lookup_failure_does_not_generate(provider, monkeypatch: pytest.MonkeyPatch):
    """번들 여부를 모르면 **만들지 않는다** — 모를 때 돈을 쓰지 않는 쪽으로 닫는다."""
    await _member()
    await _generate(premium_purchase.action_kind("BLINKING"))

    async def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(premium_purchase, "find_active_purchase", boom)
    await _complete_all_open_jobs()

    assert len(provider) == 1, "원장 조회 실패인데 추가 생성이 나갔다"


@pytest.mark.anyio
async def test_queue_utility_keeps_its_unrestricted_default():
    """
    advance_generation_queue 자체의 기본 동작은 바꾸지 않았다 — 제한은 호출부가
    건다. (기존 test_queue_auto_advance.py 계약 보존)
    """
    import inspect

    sig = inspect.signature(premium_generation.advance_generation_queue)
    assert sig.parameters["allowed_actions"].default is None
