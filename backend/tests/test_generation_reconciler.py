"""
누락 웹훅 리컨사일러 (Phase 14C).

계약:
  * 완료 처리를 두 번 구현하지 않는다 — 웹훅과 **같은** 함수로 넘긴다.
  * 프로바이더가 pending 이면 아무것도 건드리지 않는다.
  * 웹훅이 먼저 끝냈다면 no-op (경쟁 안전).
  * 두 번 돌려도 이중 승격/이중 환불이 없다.

유료 프로바이더는 호출하지 않는다 — 상태 조회를 전부 모의한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.hybrid_business import MotionJobStatus
from backend.services import credit_generation_service as cgs
from backend.services import generated_motions_service as gms
from backend.services import generation_reconciler as rec


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(gms, "_use_db", lambda: False)
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()
    yield
    gms._MOCK_JOBS.clear()
    gms._MOCK_SESSIONS.clear()
    gms._MOCK_MOTIONS.clear()


class H:
    def __init__(self):
        self.uploads: list[str] = []
        self.refunds: list[tuple] = []
        self.resubmits: list[dict] = []
        self.downloads = 0
        self.polls: list[str] = []


@pytest.fixture
def h(monkeypatch, tmp_path):
    hh = H()

    async def fake_download(url):
        hh.downloads += 1
        p = tmp_path / f"d{hh.downloads}.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return str(p)

    async def fake_upload(path, data, ctype):
        hh.uploads.append(path)
        return f"https://cdn/{path}"

    async def fake_refund(uid, amt, **_ledger):
        hh.refunds.append((uid, amt))
        class W:
            current_credits = 9
        return W()

    async def fake_resubmit(**kw):
        hh.resubmits.append(kw)
        ext = f"retry-{kw['action_id']}-{kw['attempt']}"
        await gms.register_generation_job(
            kw["session_id"], kw["user_id"], kw["pet_id"], kw["place_key"],
            kw["action_id"], ext, provider="wan_turbo", provider_model="M",
            attempt=kw["attempt"],
        )
        return {"action_id": kw["action_id"], "ok": True, "external_id": ext}

    import backend.services.luma_service as ls
    import backend.services.supabase_assets as sa
    import backend.services.credit_luma_batch as clb

    monkeypatch.setattr(ls, "download_video", fake_download)
    monkeypatch.setattr(sa, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(cgs, "refund_credits", fake_refund)
    monkeypatch.setattr(clb, "resubmit_action", fake_resubmit)
    monkeypatch.setattr(gms, "validate_candidate", lambda job, mp4: (True, {"ok": True}))
    return hh


def seed(action="TOUCH", provider="wan_turbo", ext="ext-TOUCH", attempt=1):
    sid = "sess-r"
    gms._MOCK_SESSIONS[sid] = {
        "session_id": sid, "user_id": "u1", "pet_id": "p1",
        "place_key": "01_snow_forest", "place_id": "snow_forest",
        "pet_image_url": "https://cdn/kf.jpg", "credits_charged": 4, "status": "processing",
    }
    asyncio.run(gms.register_generation_job(
        sid, "u1", "p1", "01_snow_forest", action, ext,
        provider=provider, provider_model="fal-ai/wan/v2.2-a14b/image-to-video/turbo",
        attempt=attempt,
    ))
    return gms._MOCK_JOBS[ext]


def wan_status(monkeypatch, h, status, video_url=None, error=None):
    import backend.services.wan_service as ws

    async def fake(request_id, model=None):
        h.polls.append(request_id)
        return {"status": status, "video_url": video_url, "error": error}

    monkeypatch.setattr(ws, "fetch_status", fake)


# ── Wan: 완료 복구 ──────────────────────────────────────────────────────────


def test_stale_wan_job_completed_is_promoted(h, monkeypatch):
    job = seed()
    wan_status(monkeypatch, h, "COMPLETED", video_url="https://v/t.mp4")
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "reconciled_completed"
    assert h.polls == ["ext-TOUCH"]
    assert any("/candidates/" in p for p in h.uploads), "후보가 저장돼야 한다"
    # Phase 6: 승격 경로에 작업 id 가 들어간다 — 버전마다 다른 객체여야
    # "Sleeping #1 / #2 가 공존한다"가 기록이 아니라 사실이 된다.
    assert any(
        u.startswith("u1/p1/library/SNOW_FOREST_TOUCH_") for u in h.uploads
    ), "버전별 라이브러리 경로로 승격돼야 한다"
    assert gms._MOCK_JOBS["ext-TOUCH"].status == MotionJobStatus.completed
    assert gms._MOCK_JOBS["ext-TOUCH"].promoted_at is not None


def test_reconciler_reuses_the_webhook_path(h, monkeypatch):
    """별도 완료 구현이 아니라 handle_luma_webhook_for_credit 을 호출해야 한다."""
    called = {"n": 0}
    real = cgs.handle_luma_webhook_for_credit

    async def spy(*a, **k):
        called["n"] += 1
        return await real(*a, **k)

    monkeypatch.setattr(cgs, "handle_luma_webhook_for_credit", spy)
    job = seed()
    wan_status(monkeypatch, h, "COMPLETED", video_url="https://v/t.mp4")
    asyncio.run(rec.reconcile_job(job))
    assert called["n"] == 1


# ── Wan: 실패 복구 → 재시도 ─────────────────────────────────────────────────


def test_stale_wan_job_failed_triggers_retry(h, monkeypatch):
    job = seed()
    wan_status(monkeypatch, h, "ERROR", error="fal 422")
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "reconciled_failed"
    assert gms._MOCK_JOBS["ext-TOUCH"].status == MotionJobStatus.failed
    assert len(h.resubmits) == 1 and h.resubmits[0]["action_id"] == "TOUCH"
    assert h.resubmits[0]["attempt"] == 2


def test_failed_at_max_attempts_finalizes_and_refunds(h, monkeypatch):
    job = seed(attempt=2)  # 이미 마지막 시도
    wan_status(monkeypatch, h, "FAILED", error="dead")
    r = asyncio.run(rec.reconcile_job(job))
    assert len(h.resubmits) == 0, "시도 소진 — 재시도 없음"
    assert r["summary"]["session_status"] == "failed"
    assert h.refunds == [("u1", 4)]


# ── 아직 진행 중이면 건드리지 않는다 ────────────────────────────────────────


@pytest.mark.parametrize("status", ["IN_QUEUE", "IN_PROGRESS", "", "UNKNOWN"])
def test_still_pending_leaves_job_untouched(h, monkeypatch, status):
    job = seed()
    wan_status(monkeypatch, h, status)
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "still_pending"
    assert gms._MOCK_JOBS["ext-TOUCH"].status == MotionJobStatus.submitted
    assert h.uploads == [] and h.resubmits == [] and h.refunds == []


def test_poll_failure_is_non_destructive(h, monkeypatch):
    import backend.services.wan_service as ws

    async def boom(request_id, model=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(ws, "fetch_status", boom)
    job = seed()
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "poll_failed"
    assert gms._MOCK_JOBS["ext-TOUCH"].status == MotionJobStatus.submitted


# ── 경쟁 / 멱등성 ───────────────────────────────────────────────────────────


def test_webhook_wins_race_reconciler_is_noop(h, monkeypatch):
    job = seed()
    # 웹훅이 먼저 완료시킨다
    asyncio.run(cgs.handle_luma_webhook_for_credit("ext-TOUCH", "completed", video_url="https://v/t.mp4"))
    d, u = h.downloads, len(h.uploads)

    wan_status(monkeypatch, h, "COMPLETED", video_url="https://v/t.mp4")
    stale = gms._MOCK_JOBS["ext-TOUCH"]
    r = asyncio.run(rec.reconcile_job(stale))
    assert r["result"] == "skipped_terminal", "이미 종료된 작업은 건드리지 않는다"
    assert h.downloads == d and len(h.uploads) == u
    assert h.polls == [], "종료된 작업은 폴링조차 하지 않는다"


def test_double_reconcile_does_not_double_promote(h, monkeypatch):
    job = seed()
    wan_status(monkeypatch, h, "COMPLETED", video_url="https://v/t.mp4")
    asyncio.run(rec.reconcile_job(job))
    u1, d1 = len(h.uploads), h.downloads
    r2 = asyncio.run(rec.reconcile_job(gms._MOCK_JOBS["ext-TOUCH"]))
    assert r2["result"] == "skipped_terminal"
    assert len(h.uploads) == u1 and h.downloads == d1


def test_double_reconcile_does_not_double_refund(h, monkeypatch):
    job = seed(attempt=2)
    wan_status(monkeypatch, h, "FAILED", error="dead")
    asyncio.run(rec.reconcile_job(job))
    assert h.refunds == [("u1", 4)]
    asyncio.run(rec.reconcile_job(gms._MOCK_JOBS["ext-TOUCH"]))
    assert h.refunds == [("u1", 4)], "이중 환불 금지"


# ── Luma 경로 ───────────────────────────────────────────────────────────────


def test_luma_stale_job_completed(h, monkeypatch):
    import backend.services.luma_service as ls

    async def fake(gen_id):
        h.polls.append(gen_id)
        return {"state": "completed", "video_url": "https://v/l.mp4", "error": None}

    monkeypatch.setattr(ls, "fetch_status", fake)
    job = seed(provider="luma", ext="luma-1")
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "reconciled_completed"
    assert h.polls == ["luma-1"]
    assert any(u.startswith("u1/p1/library/SNOW_FOREST_TOUCH_") for u in h.uploads)


def test_luma_stale_job_failed(h, monkeypatch):
    import backend.services.luma_service as ls

    async def fake(gen_id):
        return {"state": "failed", "video_url": None, "error": "moderation"}

    monkeypatch.setattr(ls, "fetch_status", fake)
    job = seed(provider="luma", ext="luma-2")
    r = asyncio.run(rec.reconcile_job(job))
    assert r["result"] == "reconciled_failed"
    assert gms._MOCK_JOBS["luma-2"].status == MotionJobStatus.failed


def test_luma_dreaming_is_pending(h, monkeypatch):
    import backend.services.luma_service as ls

    async def fake(gen_id):
        return {"state": "dreaming", "video_url": None, "error": None}

    monkeypatch.setattr(ls, "fetch_status", fake)
    job = seed(provider="luma", ext="luma-3")
    assert asyncio.run(rec.reconcile_job(job))["result"] == "still_pending"


# ── 정체 판정 ───────────────────────────────────────────────────────────────


def test_stale_threshold_is_env_configurable(monkeypatch):
    monkeypatch.setenv("GENERATION_RECONCILE_AFTER_SEC", "60")
    assert rec.reconcile_after_sec() == 60
    monkeypatch.delenv("GENERATION_RECONCILE_AFTER_SEC")
    assert rec.reconcile_after_sec() == rec.DEFAULT_RECONCILE_AFTER_SEC


def test_is_stale_uses_updated_then_created():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=1000)).isoformat()
    fresh = (now - timedelta(seconds=5)).isoformat()
    assert rec.is_stale({"updated_at": old}, now=now, threshold_sec=900) is True
    assert rec.is_stale({"updated_at": fresh}, now=now, threshold_sec=900) is False
    assert rec.is_stale({"created_at": old}, now=now, threshold_sec=900) is True
    # updated_at 이 우선
    assert rec.is_stale({"updated_at": fresh, "created_at": old}, now=now, threshold_sec=900) is False


def test_missing_timestamp_is_never_stale():
    assert rec.is_stale({}, threshold_sec=1) is False


def test_only_non_terminal_statuses_are_inspected():
    assert set(rec.NON_TERMINAL) == {
        MotionJobStatus.submitted, MotionJobStatus.pending, MotionJobStatus.dreaming
    }
    for s in (MotionJobStatus.completed, MotionJobStatus.failed, MotionJobStatus.rejected):
        assert s not in rec.NON_TERMINAL
