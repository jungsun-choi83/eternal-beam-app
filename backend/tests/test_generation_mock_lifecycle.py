"""
GENERATION_MOCK 은 **막기만 하는 것이 아니라 끝내야 한다**.

회귀 대상(실제로 났던 버그): BLINKING 을 수동 생성하면 영원히 GENERATING 이었다.

원인: 제출 완료는 **push 방식**이다 — 프로바이더가 /api/v1/pet/generation-webhook 을
POST 해야 후보 저장 → 검증 → 승격이 돈다. GENERATION_MOCK=1 은 프로바이더 호출을
막고 가짜 external_id 만 돌려줬으므로, 부를 프로바이더가 없어 **콜백이 영원히 오지
않았다**. 작업은 submitted 로 남고 asset_state 는 계속 active(=GENERATING) 였다.

여기서 고정하는 계약:
    MISSING → Generate → (GENERATING) → READY   ... GENERATION_MOCK=1 에서
    완료는 **실제 웹훅과 같은 경로**를 탄다 (후보 → 검증 → 승격 → 세션 확정)
    실제 프로바이더는 한 번도 호출되지 않는다
    대역 영상이 없으면 **작업을 만들기 전에** 실패한다 (stuck 금지)
    이미 READY 면 재생성하지 않는다
"""

from __future__ import annotations

import os
import tempfile

import pytest

from backend.services import (
    generated_motions_service as ms,
    luma_service,
    premium_generation,
    premium_purchase,
    subscription_store_service as sub_store,
    supabase_assets,
    video_generation,
    wallet_service,
)
from backend.services.subscription_webhook_service import handle_subscription_webhook

USER = "mockgen@example.com"
PET = "pet_mockgen"
IMG = "https://cdn.test/cutout.png"
MOCK_MP4 = "https://cdn.test/mock-idle.mp4"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    # Phase 7H: 이 파일은 **레거시 이행 계약**을 검증한다 — 명시 회귀 스위치.
    monkeypatch.setenv("PREMIUM_FULFILLMENT", "legacy")
    monkeypatch.setenv("PET_HYBRID_SEED", "0")
    monkeypatch.setenv("SUBSCRIPTION_MOCK", "1")
    monkeypatch.setenv("GENERATION_MOCK", "1")
    monkeypatch.setenv("MOCK_LUMA_VIDEO_URL", MOCK_MP4)
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
def world(monkeypatch: pytest.MonkeyPatch):
    """
    네트워크/스토리지만 대체한다. **생성 수명주기 로직은 전부 실제 코드가 돈다** —
    이 버그는 로직이 아니라 "완료를 부르는 주체"의 문제였으므로, 그 경로를 목업으로
    가리면 회귀를 잡지 못한다.
    """
    calls = {"provider": 0, "keyframe": 0, "downloads": []}

    async def fake_keyframe(url, session_id):
        calls["keyframe"] += 1
        return "https://cdn.test/keyframe.jpg"

    async def fake_download(url):
        calls["downloads"].append(url)
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
        os.close(fd)
        return path

    async def fake_upload(path, data, content_type):
        return f"https://cdn.test/{path}"

    real_submit = video_generation.submit_generation

    async def counting_submit(*a, **k):
        # GENERATION_MOCK 게이트를 통과해 실제 프로바이더에 닿으면 여기서 잡힌다.
        calls["provider"] += 1
        return await real_submit(*a, **k)

    monkeypatch.setattr(premium_generation, "prepare_black_plate_keyframe", fake_keyframe)
    monkeypatch.setattr(premium_generation, "submit_generation", counting_submit)
    monkeypatch.setattr(luma_service, "download_video", fake_download)
    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return calls


async def _member():
    await handle_subscription_webhook({
        "store_type": "mock", "notification_type": "INITIAL_BUY", "user_id": USER,
        "plan_id": "web_membership", "transaction_id": "tx_mock",
    })


async def _generate(action="BLINKING"):
    return await premium_purchase.purchase(
        user_id=USER, pet_id=PET, kind=premium_purchase.action_kind(action),
        pet_image_url=IMG, api_base="https://api.test",
    )


async def _state(action="BLINKING"):
    return await premium_purchase.asset_state(USER, PET, (action,))


# ── 핵심 회귀: MISSING → GENERATING → READY ──────────────────────────────────


@pytest.mark.anyio
async def test_missing_before_generate(world):
    await _member()
    st = await _state()
    assert st.missing == ["BLINKING"] and not st.ready and not st.active


@pytest.mark.anyio
async def test_generate_reaches_ready_under_generation_mock(world):
    """**이 버그의 재발 방지선.** 예전에는 영원히 active(=GENERATING) 였다."""
    await _member()
    r = await _generate()

    assert r.submitted == ["BLINKING"], "요청한 행동이 제출되지 않았다"
    st = await _state()
    assert "BLINKING" in st.ready, "GENERATION_MOCK 에서 READY 에 도달하지 못했다"
    assert st.active == [], "작업이 GENERATING 에 갇혔다"
    assert st.missing == []


@pytest.mark.anyio
async def test_asset_state_reports_a_playable_url(world):
    await _member()
    await _generate()
    st = await _state()
    assert st.ready["BLINKING"], "READY 인데 재생 URL 이 없다"


@pytest.mark.anyio
async def test_the_job_row_actually_completes(world):
    """작업이 등록(=GENERATING)됐다가 승격까지 갔는지 원장으로 확인한다."""
    await _member()
    await _generate()

    jobs = [j for j in ms._MOCK_JOBS.values() if j.action_id == "BLINKING"]
    assert len(jobs) == 1, "작업이 등록되지 않았거나 중복 등록됐다"
    job = jobs[0]
    assert job.status is ms.MotionJobStatus.completed, f"작업이 {job.status} 로 남았다"
    assert job.promoted_at is not None, "승격되지 않았다 — canonical 자산이 없다"


@pytest.mark.anyio
async def test_session_is_finalised(world):
    await _member()
    await _generate()
    sessions = list(ms._MOCK_SESSIONS.values())
    assert sessions, "세션이 만들어지지 않았다"
    assert all(s.get("status") == "completed" for s in sessions), "세션이 processing 으로 남았다"


# ── 정상 수명주기를 **그대로** 탄다 ─────────────────────────────────────────


@pytest.mark.anyio
async def test_mock_completion_runs_the_real_candidate_pipeline(world):
    """
    후보 저장은 실제 download_video 를 거친다. 목업이 승격만 흉내 내면
    "목업에서는 되는데 실제에서는 안 되는" 경로가 생긴다.
    """
    await _member()
    await _generate()
    assert world["downloads"], "후보 다운로드가 일어나지 않았다 — 수명주기를 건너뛰었다"
    assert MOCK_MP4 in world["downloads"], "MOCK_LUMA_VIDEO_URL 이 쓰이지 않았다"


@pytest.mark.anyio
async def test_no_real_provider_call_happens(world):
    """
    비용 안전장치 — 목업이 완료를 만들어도 실제 프로바이더는 부르지 않는다.

    ⚠️ 제출 **횟수**는 여기서 검사하지 않는다. 완료 시 advance_generation_queue 가
    요청하지 않은 행동까지 자동 제출하는 **별개의 선행 결함**이 있어(GENERATION_MOCK
    과 무관하게 실 프로바이더에서도 발생한다) 횟수가 1을 넘는다. 그 결함을 이
    테스트가 통과시켜 굳히지 않도록, 여기서는 "실 호출이 없다"만 고정한다.
    """
    await _member()
    await _generate()
    assert world["provider"] >= 1, "제출 계층을 거치지 않았다"
    # 발급된 모든 id 가 목업이어야 한다 = 실제 프로바이더에 닿은 적이 없다.
    ids = [j.luma_generation_id for j in ms._MOCK_JOBS.values()]
    assert ids and all(i.startswith("mock_") for i in ids), f"실 프로바이더 id 발급: {ids}"


# ── 설정 누락은 stuck 이 아니라 즉시 실패 ────────────────────────────────────


@pytest.mark.anyio
async def test_missing_mock_video_url_fails_fast_instead_of_stranding(
    world, monkeypatch: pytest.MonkeyPatch
):
    """
    대역 영상이 없으면 **작업을 만들기 전에** 끊는다. 만들어 놓고 실패하면
    그 작업이 정확히 이 버그(영원한 GENERATING)를 재현한다.
    """
    monkeypatch.delenv("MOCK_LUMA_VIDEO_URL", raising=False)
    await _member()

    with pytest.raises(premium_purchase.PurchaseError):
        await _generate()

    st = await _state()
    assert st.active == [], "실패했는데 작업이 GENERATING 으로 남았다"
    assert st.missing == ["BLINKING"], "재시도할 수 있게 MISSING 으로 돌아와야 한다"
    assert not ms._MOCK_JOBS, "제출 전에 끊지 못하고 작업 행을 만들었다"


# ── 재생성 금지는 그대로 ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ready_behavior_is_not_regenerated(world):
    await _member()
    await _generate()
    before = world["provider"]

    r = await _generate()

    assert r.submitted == []
    assert world["provider"] == before, "READY 인데 다시 생성했다"


@pytest.mark.anyio
async def test_each_behavior_can_be_generated_and_reaches_ready(world):
    """COME_CLOSER 도 같은 경로로 READY 가 된다."""
    await _member()
    await _generate("COME_CLOSER")
    st = await _state("COME_CLOSER")
    assert "COME_CLOSER" in st.ready and st.active == []


# ── 구조 가드 ────────────────────────────────────────────────────────────────


def test_mock_completion_reuses_the_webhook_handler():
    """
    별도 완료 로직을 두지 않는다 — 목업만 다른 길로 가면 목업 통과가 실제 통과를
    보장하지 못한다.
    """
    import inspect

    src = inspect.getsource(premium_generation._complete_mocked_generation)
    assert "handle_luma_webhook_for_credit" in src, "정상 웹훅 경로를 쓰지 않는다"


def test_mock_gate_is_checked_before_the_job_row_is_created():
    """설정 검사가 register_generation_job 보다 앞에 있어야 stuck 이 없다."""
    import inspect

    src = inspect.getsource(premium_generation.submit_premium_action)
    assert src.index("mock_completion_video_url()") < src.index("register_generation_job")
