"""
서버측 큐 전진 — 완료/실패 시 다음 액션을 **브라우저 없이** 제출한다.

배경: 큐 자체는 정상이었다(슬롯 해제·순서 모두 정확). 문제는 다음 액션을 다시
요청하는 주체가 브라우저의 20초 스윕뿐이었던 것이다. 생성 1건이 45~130초 걸리므로
3~5번째 이벤트는 조정 화면에 3~6분 머물러야 제출됐고, 실제로는 2~3개에서 멈췄다.

여기서 고정하는 계약:
  * 프리미엄 작업이 종료되면(승격/거절/실패) 서버가 즉시 다음 액션을 제출한다
  * 레거시 4종 종료에는 아무것도 하지 않는다
  * 전진 중 예외가 웹훅 처리를 깨뜨리지 않는다 (이미 승격된 자산이 유실되면 안 된다)
  * 반복 호출로 5종 전체가 결국 제출된다

프로바이더는 호출하지 않는다 — submit_generation 을 스텁으로 갈아 끼운다.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.scenarios.pet_scenarios import ACTION_ORDER
from backend.services import generated_motions_service as motions_svc
from backend.services import premium_generation
from backend.services.generation_queue import (
    GENERATION_ORDER,
    MAX_CONCURRENT_GENERATIONS_PER_PET,
)
from backend.models.hybrid_business import MotionJobStatus

USER, PET = "u_adv", "pet_adv"
IMG = "https://cdn.test/pet.png"
API = "https://api.test"


class _Job:
    def __init__(self, ext: str):
        self.external_id = ext
        self.model = "stub-model"


@pytest.fixture()
def stub(monkeypatch):
    """키프레임/프로바이더만 스텁. 큐·저장소는 실제 코드 그대로 돈다."""
    calls: list[str] = []
    n = {"i": 0}

    async def fake_kf(image_url: str, session_id: str) -> str:
        return "https://cdn.test/kf.jpg"

    async def fake_submit(kf, prompt, provider=None, callback_url=None):
        n["i"] += 1
        return _Job(f"ext{n['i']}")

    monkeypatch.setattr(premium_generation, "prepare_black_plate_keyframe", fake_kf)
    monkeypatch.setattr(premium_generation, "submit_generation", fake_submit)
    monkeypatch.setattr(premium_generation, "resolve_action_provider", lambda a: "luma")
    monkeypatch.setattr(
        premium_generation, "build_scenario_prompt", lambda kf, pk, a: f"PROMPT::{a}"
    )
    monkeypatch.setattr(premium_generation, "log_submission_receipt", lambda **kw: calls.append(kw["action_id"]))

    motions_svc._MOCK_JOBS.clear()
    motions_svc._MOCK_MOTIONS.clear()
    yield calls
    motions_svc._MOCK_JOBS.clear()
    motions_svc._MOCK_MOTIONS.clear()


def _advance() -> list[str]:
    return asyncio.run(
        premium_generation.advance_generation_queue(
            user_id=USER, pet_id=PET, pet_image_url=IMG, api_base=API
        )
    )


def _terminalise(action: str) -> None:
    """그 액션의 진행 중 작업을 완료로 만들고 canonical 을 심는다 (승격 모사)."""
    for row in motions_svc._MOCK_JOBS.values():
        if (row.action_id or "").upper() == action and row.status not in (
            MotionJobStatus.completed, MotionJobStatus.rejected, MotionJobStatus.failed
        ):
            row.status = MotionJobStatus.completed
    key = motions_svc._motion_key(USER, PET, "any", action)
    from backend.models.hybrid_business import GeneratedMotion
    motions_svc._MOCK_MOTIONS[key] = GeneratedMotion(
        user_id=USER, pet_id=PET, place_id="any", action_id=action,
        video_url=f"https://cdn.test/{action}.mp4",
    )


def _fail(action: str) -> None:
    """승격 없이 실패로 종료 — 슬롯은 비지만 canonical 은 없다."""
    for row in motions_svc._MOCK_JOBS.values():
        if (row.action_id or "").upper() == action:
            row.status = MotionJobStatus.failed


# ── 기본 전진 ────────────────────────────────────────────────────────────────


def test_first_advance_fills_both_slots(stub):
    submitted = _advance()
    assert submitted == ["COME_CLOSER", "BLINKING"], submitted
    assert len(submitted) == MAX_CONCURRENT_GENERATIONS_PER_PET


def test_completion_admits_the_next_action(stub):
    _advance()                       # CC + BLINKING 제출
    _terminalise("COME_CLOSER")      # 한 건 완료 → 슬롯 1개
    assert _advance() == ["EAR_TWITCHING"]


def test_failure_also_admits_the_next_action(stub):
    """실패도 슬롯을 비운다 — 실패가 큐를 막으면 안 된다."""
    _advance()
    _fail("COME_CLOSER")
    nxt = _advance()
    # COME_CLOSER 는 canonical 이 없으므로 재시도 후보로 맨 앞에 남는다.
    assert nxt == ["COME_CLOSER"], nxt


# ── 전체 배수 (요구 동작) ────────────────────────────────────────────────────


def test_one_pet_drains_all_five_without_a_browser(stub):
    """
    요구된 최종 동작:
      COME_CLOSER + BLINKING → 완료로 슬롯 확보 → EAR_TWITCHING → HEAD_TILTING
      → TAIL_WAGGING → 전부 ready
    브라우저 스윕은 한 번도 개입하지 않는다.
    """
    submitted: list[str] = []
    for _ in range(len(GENERATION_ORDER) * 3):  # 유한 루프 — 무한 대기 회귀도 잡는다
        batch = _advance()
        submitted += batch
        active = asyncio.run(motions_svc.list_active_action_ids_for_pet(USER, PET))
        assert len(active) <= MAX_CONCURRENT_GENERATIONS_PER_PET, active
        if not active:
            break
        _terminalise(active[0])       # 가장 오래된 것 하나가 완료된다

    assert sorted(submitted) == sorted(GENERATION_ORDER), submitted
    assert len(submitted) == len(set(submitted)), "같은 액션을 두 번 제출했다"

    ready = {
        (m.action_id or "").upper()
        for m in asyncio.run(motions_svc.list_motions_for_pet(USER, PET))
    }
    assert ready == set(GENERATION_ORDER), ready
    # 배수가 끝나면 더 제출할 것이 없다.
    assert _advance() == []


def test_generation_order_is_respected_across_advances(stub):
    order: list[str] = []
    for _ in range(len(GENERATION_ORDER) * 3):
        batch = _advance()
        order += batch
        active = asyncio.run(motions_svc.list_active_action_ids_for_pet(USER, PET))
        if not active:
            break
        _terminalise(active[0])
    assert order[0] == "COME_CLOSER", "COME_CLOSER 가 1순위여야 한다"
    for a in GENERATION_ORDER:
        assert a in order


# ── 안전장치 ─────────────────────────────────────────────────────────────────


def test_legacy_actions_never_trigger_advance():
    """레거시 4종은 자기 파이프라인이 동시성을 관리한다."""
    for legacy in ACTION_ORDER:
        assert not premium_generation.is_queued_action(legacy)
    for premium in GENERATION_ORDER:
        assert premium_generation.is_queued_action(premium)


def test_missing_pet_image_url_is_a_no_op(stub):
    assert asyncio.run(
        premium_generation.advance_generation_queue(
            user_id=USER, pet_id=PET, pet_image_url=None, api_base=API
        )
    ) == []
    assert asyncio.run(motions_svc.list_active_action_ids_for_pet(USER, PET)) == []


def test_advance_never_raises_even_if_submission_explodes(stub, monkeypatch):
    """
    전진은 웹훅의 **부수 작업**이다. 여기서 예외가 새면 이미 성공한 승격이 500 으로
    뒤집히고, 프로바이더가 재전송하면 duplicate 로 걸러져 자산이 유실된다.
    """
    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(premium_generation, "submit_generation", boom)
    assert _advance() == []  # 예외가 밖으로 나오지 않는다


def test_keyframe_failure_does_not_block_later_advances(stub, monkeypatch):
    async def kf_boom(image_url, session_id):
        from backend.services.credit_keyframe import KeyframePreparationError
        raise KeyframePreparationError("nope", stage="download")

    monkeypatch.setattr(premium_generation, "prepare_black_plate_keyframe", kf_boom)
    assert _advance() == []

    # 키프레임이 복구되면 다시 진행된다 — 영구 차단이 없다.
    async def kf_ok(image_url, session_id):
        return "https://cdn.test/kf.jpg"

    monkeypatch.setattr(premium_generation, "prepare_black_plate_keyframe", kf_ok)
    assert _advance() == ["COME_CLOSER", "BLINKING"]


# ── 웹훅 배선 (전진이 조용히 끊기지 않도록) ──────────────────────────────────


def test_webhook_calls_advance_on_every_terminal_path():
    """
    승격/거절/실패 세 경로 모두에서 전진이 불려야 한다. 하나라도 빠지면 그 경로로
    끝난 펫은 큐가 멈춘다 — 정확히 원래 장애 모습이다.
    """
    import inspect
    from backend.services import credit_generation_service as svc

    src = inspect.getsource(svc.handle_luma_webhook_for_credit)
    assert src.count("_advance_premium_queue(job, session)") == 3, (
        "종료 경로 3곳(완료/거절/실패) 모두에서 큐 전진을 호출해야 한다"
    )
    helper = inspect.getsource(svc._advance_premium_queue)
    assert "is_queued_action" in helper, "레거시 4종을 걸러야 한다"
    assert "pet_image_url" in helper, "세션에서 원본 이미지를 읽어야 한다"
