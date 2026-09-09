"""
유료 생성 보호 — **fail closed** (Phase 20).

핵심 계약 한 줄: **멱등성 예약이 성립하지 않으면 프로바이더에 아무것도 보내지 않는다.**

예전에는 저장소가 죽으면 경고만 남기고 생성을 계속했다. 그 상태에서 들어오는
모든 재시도가 각각 유료 작업을 만든다 — 보호 장치 없이 도는 것은 보호 장치가
없는 것과 같다.

검증하는 재시도 모양(요구사항 그대로):
  중복 POST · 브라우저 재시도 · HTTP 타임아웃/502 · 새로고침 · 동시 요청 ·
  워커 재시작 · 같은 (user, scene, behavior) 두 번
"""

from __future__ import annotations

import functools
import pathlib

import anyio
import pytest

from backend.services import scene_generation_jobs as jobs


def _sync(afn, *a, **k):
    return anyio.run(functools.partial(afn, *a, **k))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    jobs.__reset_for_tests()
    yield
    jobs.__reset_for_tests()


class _DeadStore:
    """모든 호출이 터지는 저장소 — 테이블 없음/권한 없음/네트워크 단절."""

    def table(self, *_a, **_k):
        raise RuntimeError("relation \"scene_generation_jobs\" does not exist")


def _make_store_dead(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "1")
    monkeypatch.setattr(jobs, "_supabase", lambda: _DeadStore())


# ── 저장소 불가 → 예약도 조회도 실패한다 (조용히 통과하지 않는다) ───────────


def test_get_raises_when_store_unavailable(monkeypatch):
    _make_store_dead(monkeypatch)
    with pytest.raises(jobs.IdempotencyUnavailableError):
        _sync(jobs.get, "u", "s1", "IDLE")


def test_reserve_raises_when_store_unavailable(monkeypatch):
    _make_store_dead(monkeypatch)
    with pytest.raises(jobs.IdempotencyUnavailableError):
        _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")


def test_error_carries_a_actionable_code():
    err = jobs.IdempotencyUnavailableError
    assert err.code == "GENERATION_IDEMPOTENCY_UNAVAILABLE"
    assert err.status == 503, "503 이어야 클라이언트가 '잠시 후 재시도'로 읽는다"
    assert err.message


def test_router_never_swallows_idempotency_failure():
    """
    **가장 중요한 검사.** 라우터가 멱등성 실패를 삼키고 계속 진행하면 안 된다.

    소스로 검사하는 이유: 이 라우터를 import 하면 생성 파이프라인 전체(cv2 등
    무거운 의존성)가 딸려 온다. 여기서 보려는 것은 "실패를 삼키는가"라는 제어
    흐름이지 파이프라인 동작이 아니다.
    """
    src = pathlib.Path("backend/routers/generate.py").read_text()

    # 예약/조회 실패는 반드시 전용 예외로 잡아 HTTP 로 올려야 한다.
    assert "except scene_generation_jobs.IdempotencyUnavailableError" in src
    assert "_idempotency_unavailable(" in src

    # 예전의 "실패해도 계속한다" 문구/패턴이 남아 있으면 안 된다.
    assert "생성은 계속한다" not in src, "멱등성 실패를 삼키는 경로가 남아 있다"

    # 예약 결과를 무시하지 않는다 — is_new 를 실제로 본다.
    assert "if not is_new:" in src, "예약 경합에서 져도 제출을 계속한다"

    # 제출은 예약 **뒤에** 있어야 한다.
    assert src.index("scene_generation_jobs.reserve(") < src.index(
        "create_generation_and_get_video_url("
    ), "예약보다 제출이 먼저다 — 그 사이 요청이 이중 제출한다"


# ── 재시도 모양별 보호 ───────────────────────────────────────────────────────


def test_duplicate_post_reserves_once():
    """중복 POST — 두 번째는 새 작업이 아니다."""
    _, a = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _, b = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    assert (a, b) == (True, False)


def test_browser_retry_and_refresh_reuse_the_same_reservation():
    """브라우저 재시도·새로고침은 같은 키로 들어온다."""
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    for _ in range(5):
        _, is_new = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
        assert is_new is False


def test_timeout_then_retry_finds_the_submitted_job_id():
    """
    HTTP 타임아웃/502 후 재시도 — 이미 제출된 유료 작업의 id 가 남아 있어야
    폴링으로 되찾을 수 있다. 없으면 재제출뿐이고 그것이 이중 과금이다.
    """
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        jobs.mark_submitted,
        user_id="u", scene_id="s1", behavior="IDLE",
        provider="luma", provider_job_id="gen_abc",
    )
    again = _sync(jobs.get, "u", "s1", "IDLE")
    assert again.active is True
    assert again.provider_job_id == "gen_abc"
    _, is_new = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    assert is_new is False


def test_concurrent_requests_only_one_wins():
    """동시 요청 — 정확히 하나만 새 작업을 얻는다."""
    results: list[bool] = []

    async def _race():
        async def one():
            _, is_new = await jobs.reserve(
                user_id="u", scene_id="s1", behavior="IDLE"
            )
            results.append(is_new)

        async with anyio.create_task_group() as tg:
            for _ in range(8):
                tg.start_soon(one)

    anyio.run(_race)
    assert results.count(True) == 1, f"동시 제출이 {results.count(True)}건 생겼다"


def test_same_user_scene_behavior_twice_is_one_job():
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _, is_new = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    assert is_new is False


# ── 워커 재시작 ──────────────────────────────────────────────────────────────


def test_fresh_reservation_without_submission_blocks_resubmit():
    """
    갓 잡힌 예약은 **다른 요청이 지금 제출 중**일 수 있다. 회수하면 이중 제출이다.
    """
    job, _ = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    assert job.reserved_but_never_submitted is True
    assert job.is_stale_reservation is False
    reclaimed = _sync(
        jobs.reclaim_stale_reservation, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert reclaimed is False


def test_stale_reservation_is_reclaimed_after_worker_restart(monkeypatch):
    """
    워커가 제출 전에 죽으면 pending 이 영원히 남아 그 장면이 생성 불가가 된다.
    창(STALE_PENDING_SEC)을 넘기면 한 번 회수한다.
    """
    monkeypatch.setattr(jobs, "STALE_PENDING_SEC", 0)
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    reclaimed = _sync(
        jobs.reclaim_stale_reservation, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert reclaimed is True
    _, is_new = _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    assert is_new is True, "회수 후에도 재시도할 수 없다"


def test_submitted_job_is_never_reclaimed(monkeypatch):
    """
    **가장 비싼 실수 방지.** 제출된 작업을 회수하면 이미 낸 돈이 사라지고
    재제출로 또 낸다. 아무리 오래돼도 회수하지 않는다.
    """
    monkeypatch.setattr(jobs, "STALE_PENDING_SEC", 0)
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        jobs.mark_submitted,
        user_id="u", scene_id="s1", behavior="IDLE",
        provider="luma", provider_job_id="gen_paid",
    )
    reclaimed = _sync(
        jobs.reclaim_stale_reservation, user_id="u", scene_id="s1", behavior="IDLE"
    )
    assert reclaimed is False
    assert _sync(jobs.get, "u", "s1", "IDLE").provider_job_id == "gen_paid"


def test_completed_job_is_never_reclaimed(monkeypatch):
    monkeypatch.setattr(jobs, "STALE_PENDING_SEC", 0)
    _sync(jobs.reserve, user_id="u", scene_id="s1", behavior="IDLE")
    _sync(
        jobs.mark_completed,
        user_id="u", scene_id="s1", behavior="IDLE", video_url="https://s/v.mp4",
    )
    assert (
        _sync(jobs.reclaim_stale_reservation, user_id="u", scene_id="s1", behavior="IDLE")
        is False
    )
