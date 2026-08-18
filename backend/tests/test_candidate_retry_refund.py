"""
후보→승격 / 재시도 / 환불 / 재전송 방어 (Phase 14B).

라이브 System B 테스트에서 확인된 3가지 결함을 함께 고친다:
  1. 실패한 액션이 4코인을 그대로 소모했다
  2. 같은 pet/place/action 완료본이 서로 덮어썼다
  3. 세션이 completed/failed 이후에도 processing 으로 남았다

유료 프로바이더는 호출하지 않는다 — 모든 I/O 를 모의한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from backend.models.hybrid_business import MotionJobRow, MotionJobStatus, SessionStatus
from backend.services import credit_generation_service as cgs
from backend.services import generated_motions_service as gms


# ── 공통 하네스 ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """DB 를 끄고 인메모리 저장소만 쓴다."""
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()
    yield
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()


class Harness:
    """업로드/다운로드/재제출/환불을 기록하는 모의 계층."""

    def __init__(self):
        self.uploads: list[str] = []
        self.resubmits: list[dict] = []
        self.refunds: list[tuple[str, int]] = []
        self.downloads = 0


@pytest.fixture
def h(monkeypatch, tmp_path):
    hh = Harness()

    async def fake_download(url):
        hh.downloads += 1
        p = tmp_path / f"dl_{hh.downloads}.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 64)
        return str(p)

    async def fake_upload(path, data, ctype):
        hh.uploads.append(path)
        return f"https://cdn/{path}"

    async def fake_resubmit(**kw):
        hh.resubmits.append(kw)
        ext = f"retry-{kw['action_id']}-{kw['attempt']}"
        await gms.register_generation_job(
            kw["session_id"], kw["user_id"], kw["pet_id"], kw["place_key"],
            kw["action_id"], ext, provider="wan_turbo", provider_model="M",
            attempt=kw["attempt"],
        )
        return {"action_id": kw["action_id"], "ok": True, "external_id": ext,
                "attempt": kw["attempt"]}

    async def fake_refund(uid, amount):
        hh.refunds.append((uid, amount))
        class W:
            current_credits = 99
        return W()

    import backend.services.luma_service as ls
    import backend.services.supabase_assets as sa
    import backend.services.credit_luma_batch as clb

    monkeypatch.setattr(ls, "download_video", fake_download)
    monkeypatch.setattr(sa, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(clb, "resubmit_action", fake_resubmit)
    monkeypatch.setattr(cgs, "refund_credits", fake_refund)
    # 검증은 기본 통과 (진단 전용)
    monkeypatch.setattr(gms, "validate_candidate", lambda job, mp4: (True, {"ok": True}))
    return hh


def make_session(actions=("IDLE", "TOUCH", "VOICE", "NFC"), credits=4) -> str:
    sid = "sess-1"
    gms._MOCK_SESSIONS[sid] = {
        "session_id": sid, "user_id": "u1", "pet_id": "p1",
        "place_key": "01_snow_forest", "place_id": "snow_forest",
        "pet_image_url": "https://cdn/kf.jpg", "credits_charged": credits,
        "status": "processing",
    }
    for a in actions:
        asyncio.run(gms.register_generation_job(
            sid, "u1", "p1", "01_snow_forest", a, f"ext-{a}",
            provider="wan_turbo", provider_model="M", attempt=1,
        ))
    return sid


def hook(ext, state, video_url=None, error=None):
    return asyncio.run(cgs.handle_luma_webhook_for_credit(ext, state, video_url=video_url, error=error))


# ── 1. 실패한 액션만 재시도 ─────────────────────────────────────────────────


def test_failed_action_retries_only_that_action(h):
    make_session()
    r = hook("ext-TOUCH", "failed", error="fal 422")
    assert r["status"] == "failed"
    assert len(h.resubmits) == 1
    assert h.resubmits[0]["action_id"] == "TOUCH", "실패한 액션만 재제출돼야 한다"
    assert h.resubmits[0]["attempt"] == 2
    assert r["session_status"] == "processing"


def test_retry_reuses_session_and_keyframe(h):
    sid = make_session()
    hook("ext-VOICE", "failed", error="boom")
    kw = h.resubmits[0]
    assert kw["session_id"] == sid
    assert kw["pet_image_url"] == "https://cdn/kf.jpg", "같은 검정 플레이트를 재사용해야 한다"


def test_retry_costs_no_extra_credits(h):
    make_session()
    hook("ext-NFC", "failed", error="boom")
    assert h.refunds == [], "재시도 시점에는 환불도 추가 과금도 없다"


# ── 2. 두 번째 실패 → 종료 상태 ─────────────────────────────────────────────


def test_second_failure_exhausts_attempts(h):
    make_session()
    hook("ext-TOUCH", "failed", error="1st")
    r = hook("retry-TOUCH-2", "failed", error="2nd")
    assert len(h.resubmits) == 1, "MAX_ACTION_ATTEMPTS=2 — 재시도는 한 번뿐"
    assert r["session_status"] == "processing", "다른 액션이 아직 열려 있다"


def test_all_actions_fail_twice_gives_failed_and_one_refund(h):
    make_session()
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        hook(f"ext-{a}", "failed", error="1st")
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        r = hook(f"retry-{a}-2", "failed", error="2nd")
    assert r["session_status"] == "failed"
    assert r["refunded"] is True
    assert h.refunds == [("u1", 4)], "전액 1회 환불"


def test_partial_set_gives_partial_and_full_refund(h):
    make_session()
    hook("ext-IDLE", "completed", video_url="https://v/i.mp4")
    hook("ext-TOUCH", "completed", video_url="https://v/t.mp4")
    hook("ext-VOICE", "completed", video_url="https://v/v.mp4")
    hook("ext-NFC", "failed", error="1st")
    r = hook("retry-NFC-2", "failed", error="2nd")
    assert r["session_status"] == "partial", "3/4 는 partial"
    assert r["refunded"] is True
    assert h.refunds == [("u1", 4)], "불완전 세트는 전액 환불 (device/sync 가 404 라 가치 0)"


def test_complete_set_gives_completed_and_no_refund(h):
    make_session()
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        r = hook(f"ext-{a}", "completed", video_url=f"https://v/{a}.mp4")
    assert r["session_status"] == "completed"
    assert r["refunded"] is False
    assert h.refunds == []
    assert gms._MOCK_SESSIONS["sess-1"]["status"] == "completed"
    assert gms._MOCK_SESSIONS["sess-1"].get("finalized_at")


# ── 3. 재전송(중복 웹훅) 방어 ───────────────────────────────────────────────


def test_duplicate_completion_does_not_reupload_or_redownload(h):
    make_session()
    hook("ext-TOUCH", "completed", video_url="https://v/t.mp4")
    d1, u1 = h.downloads, len(h.uploads)
    r = hook("ext-TOUCH", "completed", video_url="https://v/t.mp4")
    assert r["duplicate"] is True
    assert h.downloads == d1, "중복 웹훅이 재다운로드를 유발하면 안 된다"
    assert len(h.uploads) == u1, "중복 웹훅이 재업로드를 유발하면 안 된다"


def test_duplicate_failure_does_not_double_retry(h):
    make_session()
    hook("ext-TOUCH", "failed", error="boom")
    assert len(h.resubmits) == 1
    r = hook("ext-TOUCH", "failed", error="boom")
    assert r["duplicate"] is True
    assert len(h.resubmits) == 1, "중복 실패 웹훅이 재시도를 두 번 만들면 안 된다"


def test_refund_is_idempotent_across_replays(h):
    make_session()
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        hook(f"ext-{a}", "failed", error="1st")
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        hook(f"retry-{a}-2", "failed", error="2nd")
    assert h.refunds == [("u1", 4)]
    # 같은 웹훅들을 통째로 재전송
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        hook(f"retry-{a}-2", "failed", error="2nd")
    assert h.refunds == [("u1", 4)], "refunded_at 이 이중 환불을 막아야 한다"


def test_mark_session_refunded_only_succeeds_once():
    gms._MOCK_SESSIONS["s"] = {"session_id": "s", "user_id": "u", "credits_charged": 4}
    assert asyncio.run(gms.mark_session_refunded("s")) is True
    assert asyncio.run(gms.mark_session_refunded("s")) is False


# ── 4. 후보 → 승격 ──────────────────────────────────────────────────────────


def test_accepted_candidate_is_promoted_to_canonical(h):
    make_session()
    r = hook("ext-TOUCH", "completed", video_url="https://v/t.mp4")
    assert r["status"] == "completed"
    cand = [p for p in h.uploads if "/candidates/" in p]
    canon = [p for p in h.uploads if "/candidates/" not in p]
    assert len(cand) == 1 and len(canon) == 1
    assert cand[0] == "u1/p1/candidates/SNOW_FOREST_TOUCH_1_ext-TOUCH.mp4"
    assert canon[0] == "u1/p1/SNOW_FOREST_TOUCH.mp4", "정규 경로는 그대로여야 한다"
    assert gms._MOCK_JOBS["ext-TOUCH"].promoted_at is not None


def test_rejected_candidate_leaves_canonical_untouched(h, monkeypatch):
    monkeypatch.setattr(gms, "validate_candidate", lambda job, mp4: (False, {"reason": "drift"}))
    make_session()
    r = hook("ext-TOUCH", "completed", video_url="https://v/t.mp4")
    assert r["status"] == "rejected"
    assert all("/candidates/" in p for p in h.uploads), "탈락 후보가 canonical 을 덮어쓰면 안 된다"
    assert gms._MOCK_JOBS["ext-TOUCH"].status == MotionJobStatus.rejected
    assert gms._MOCK_JOBS["ext-TOUCH"].promoted_at is None
    assert len(h.resubmits) == 1, "탈락도 재시도 대상"


def test_repeated_attempts_keep_candidates_separate(h):
    """두 번 완료되면 후보가 각각 남고, canonical 만 마지막 승인본으로 갱신된다."""
    make_session()
    hook("ext-TOUCH", "completed", video_url="https://v/t1.mp4")
    # 같은 액션을 다시 생성한 상황(새 세션/새 시도)을 모사
    asyncio.run(gms.register_generation_job(
        "sess-1", "u1", "p1", "01_snow_forest", "TOUCH", "ext-TOUCH-b",
        provider="wan_turbo", provider_model="M", attempt=2,
    ))
    hook("ext-TOUCH-b", "completed", video_url="https://v/t2.mp4")

    cands = [p for p in h.uploads if "/candidates/" in p]
    assert len(cands) == 2, "후보는 시도마다 별도로 보존돼야 한다"
    assert len(set(cands)) == 2, f"후보 경로가 겹친다: {cands}"
    assert "_1_" in cands[0] and "_2_" in cands[1], "시도 번호가 경로에 들어간다"

    canon = [p for p in h.uploads if "/candidates/" not in p]
    assert set(canon) == {"u1/p1/SNOW_FOREST_TOUCH.mp4"}, "canonical 경로는 하나로 유지"


def test_candidate_name_is_unique_per_attempt():
    a = gms.candidate_object_name("01_snow_forest", "TOUCH", 1, "abc")
    b = gms.candidate_object_name("01_snow_forest", "TOUCH", 2, "def")
    assert a != b
    assert a == "candidates/SNOW_FOREST_TOUCH_1_abc.mp4"


# ── 5. 세션 상태 계산 (순수 함수) ───────────────────────────────────────────


def _job(action, status, attempt=1, promoted=False):
    return MotionJobRow(
        session_id="s", user_id="u", pet_id="p", place_key="01_snow_forest",
        action_id=action, luma_generation_id=f"{action}-{attempt}",
        status=status, attempt=attempt,
        promoted_at=datetime.utcnow() if promoted else None,
    )


def test_status_processing_while_actions_open():
    jobs = [_job("IDLE", MotionJobStatus.completed, promoted=True),
            _job("TOUCH", MotionJobStatus.submitted)]
    assert gms.compute_session_status(jobs) == SessionStatus.processing


def test_status_completed_when_all_promoted():
    jobs = [_job(a, MotionJobStatus.completed, promoted=True)
            for a in ("IDLE", "TOUCH", "VOICE", "NFC")]
    assert gms.compute_session_status(jobs) == SessionStatus.completed


def test_status_failed_when_none_promoted():
    jobs = [_job(a, MotionJobStatus.failed, attempt=2) for a in ("IDLE", "TOUCH")]
    assert gms.compute_session_status(jobs) == SessionStatus.failed


def test_status_partial_when_some_promoted():
    jobs = [_job("IDLE", MotionJobStatus.completed, promoted=True),
            _job("TOUCH", MotionJobStatus.failed, attempt=2)]
    assert gms.compute_session_status(jobs) == SessionStatus.partial


def test_empty_session_is_processing():
    assert gms.compute_session_status([]) == SessionStatus.processing


def test_status_respects_actual_submitted_actions():
    """DEV_ACTION_SUBSET 로 1개만 제출한 세션도 올바르게 종료된다."""
    jobs = [_job("TOUCH", MotionJobStatus.completed, promoted=True)]
    assert gms.compute_session_status(jobs) == SessionStatus.completed


# ── 6. IDLE seamless loop 은 플래그가 켜졌을 때만 ───────────────────────────


def test_seamless_loop_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PET_VIDEO_SEAMLESS_LOOP", raising=False)
    assert gms._seamless_loop_enabled() is False


def test_idle_promotion_does_not_run_ffmpeg_when_flag_off(h, monkeypatch):
    monkeypatch.delenv("PET_VIDEO_SEAMLESS_LOOP", raising=False)
    called = {"n": 0}

    def boom(mp4):
        called["n"] += 1
        return mp4, {}

    import backend.services.seamless_loop_service as sls

    monkeypatch.setattr(sls, "make_seamless_loop_mp4", boom)
    make_session(actions=("IDLE",))
    hook("ext-IDLE", "completed", video_url="https://v/i.mp4")
    assert called["n"] == 0, "플래그가 꺼져 있으면 ffmpeg 을 돌리면 안 된다 (512MB OOM)"


# ── 7. 드리프트 게이트는 아직 켜지 않는다 ───────────────────────────────────


def test_validator_remains_non_blocking(monkeypatch, tmp_path):
    """실제 validate_candidate 는 지표를 기록하되 항상 accepted 를 돌려준다."""
    job = _job("TOUCH", MotionJobStatus.submitted)
    accepted, meta = gms.validate_candidate(job, b"not-a-real-mp4")
    assert accepted is True
    assert meta.get("gate_enforced") is False
