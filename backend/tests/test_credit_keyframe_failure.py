"""
System B 키프레임 준비 실패 안전성 (Phase 7B).

계약:
  준비 성공 → 4건 제출
  준비 실패 → **0건 제출**, 크레딧 환불, 명확한 오류
             → 절대로 원본 RGBA URL 로 폴백하지 않는다

폴백이 위험한 이유: RGBA 누끼의 RGB 채널에는 알파 뒤에 원본 사진 배경이 그대로
남아 있다. 그걸로 생성하면 사용자의 거실이 영상에 살아나고, S23 검정 키를 통과해
Pi 배경 위에 겹쳐 보인다. 잘못된 결과에 크레딧 4개를 태우느니 0건이 낫다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from backend.services import credit_generation_service as svc
from backend.services import credit_keyframe as ck
from backend.services.credit_keyframe import KeyframePreparationError

RGBA_URL = "https://cdn/cutout_rgba.png"
BLACK_URL = "https://cdn/creditkf/s1/keyframe_black.jpg"


class _Wallet:
    def __init__(self, credits: int = 12):
        self.current_credits = credits


def _install(monkeypatch, *, prepare, submit_result=(4, [])):
    """지갑/세션/제출을 전부 가짜로 바꾸고 호출 기록을 돌려준다."""
    calls: dict = {"deduct": [], "refund": [], "submit": []}

    async def fake_deduct(uid, cost):
        calls["deduct"].append((uid, cost))
        return _Wallet()

    async def fake_refund(uid, cost):
        calls["refund"].append((uid, cost))
        return _Wallet()

    async def fake_session(*a, **k):
        return "s1"

    async def fake_submit(**kw):
        calls["submit"].append(kw)
        return submit_result

    monkeypatch.setattr(svc, "deduct_credits", fake_deduct)
    monkeypatch.setattr(svc, "refund_credits", fake_refund)
    monkeypatch.setattr(svc.motions_svc, "create_credit_session", fake_session)
    monkeypatch.setattr(svc, "submit_place_motion_set", fake_submit)
    monkeypatch.setattr(svc, "prepare_black_plate_keyframe", prepare)
    return calls


def _run():
    return asyncio.run(
        svc.generate_with_credit(
            user_id="u1",
            pet_image_url=RGBA_URL,
            selected_place_id="snow_forest",
            webhook_base_url="https://api/hook",
        )
    )


# ── 준비 실패 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["download", "flatten", "upload"])
def test_preparation_failure_submits_zero_jobs(monkeypatch, stage: str):
    async def failing(image_url, session_id):
        raise KeyframePreparationError(stage, "boom")

    calls = _install(monkeypatch, prepare=failing)
    with pytest.raises(KeyframePreparationError):
        _run()
    assert calls["submit"] == [], "준비 실패인데 Luma 제출이 일어났다"


def test_preparation_failure_refunds_credits(monkeypatch):
    async def failing(image_url, session_id):
        raise KeyframePreparationError("download", "boom")

    calls = _install(monkeypatch, prepare=failing)
    with pytest.raises(KeyframePreparationError):
        _run()
    assert calls["deduct"] == [("u1", 4)]
    assert calls["refund"] == [("u1", 4)], "차감분이 환불되지 않았다"


def test_preparation_failure_never_submits_rgba_url(monkeypatch):
    """폴백 금지 — 어떤 제출도 원본 RGBA URL 을 쓰면 안 된다."""
    async def failing(image_url, session_id):
        raise KeyframePreparationError("upload", "boom")

    calls = _install(monkeypatch, prepare=failing)
    with pytest.raises(KeyframePreparationError):
        _run()
    for kw in calls["submit"]:
        assert kw.get("pet_image_url") != RGBA_URL
    assert calls["submit"] == []


def test_error_carries_stage_for_diagnostics(monkeypatch):
    async def failing(image_url, session_id):
        raise KeyframePreparationError("flatten", "PIL exploded")

    _install(monkeypatch, prepare=failing)
    with pytest.raises(KeyframePreparationError) as ei:
        _run()
    assert ei.value.stage == "flatten"
    assert "flatten" in str(ei.value)


# ── 준비 함수 자체가 폴백하지 않는지 ────────────────────────────────────────


def test_prepare_raises_instead_of_returning_source_url(monkeypatch):
    async def no_bytes(url: str):
        return None

    monkeypatch.setattr(ck, "_fetch_bytes", no_bytes)
    with pytest.raises(KeyframePreparationError) as ei:
        asyncio.run(ck.prepare_black_plate_keyframe(RGBA_URL, "s1"))
    assert ei.value.stage == "download"


def test_prepare_raises_when_upload_returns_empty(monkeypatch):
    rgba = Image.new("RGBA", (8, 8), (200, 200, 200, 255))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")

    async def ok_bytes(url: str):
        return buf.getvalue()

    async def empty_upload(path, data, content_type):
        return ""

    monkeypatch.setattr(ck, "_fetch_bytes", ok_bytes)
    import backend.services.supabase_assets as sa

    monkeypatch.setattr(sa, "upload_asset_to_storage", empty_upload)
    with pytest.raises(KeyframePreparationError) as ei:
        asyncio.run(ck.prepare_black_plate_keyframe(RGBA_URL, "s1"))
    assert ei.value.stage == "upload"


# ── 성공 경로는 그대로 ──────────────────────────────────────────────────────


def test_success_path_still_submits_four_actions(monkeypatch):
    async def ok(image_url, session_id):
        assert image_url == RGBA_URL
        return BLACK_URL

    calls = _install(monkeypatch, prepare=ok)
    res = _run()

    assert len(calls["submit"]) == 1
    assert calls["submit"][0]["pet_image_url"] == BLACK_URL
    assert calls["refund"] == [], "성공했는데 환불이 일어났다"
    assert res.submitted == 4
    assert res.credits_charged == 4
    assert res.status == "processing"


def test_existing_all_failed_refund_behaviour_preserved(monkeypatch):
    """준비는 성공했지만 4건 모두 제출 실패 → 기존대로 환불 + status=failed."""
    async def ok(image_url, session_id):
        return BLACK_URL

    calls = _install(
        monkeypatch, prepare=ok, submit_result=(0, [{"action_id": "IDLE", "ok": False}])
    )
    res = _run()
    assert calls["refund"] == [("u1", 4)]
    assert res.submitted == 0
    assert res.credits_charged == 0
    assert res.status == "failed"


def test_partial_submit_still_charges_as_before(monkeypatch):
    """3건만 성공하는 기존 동작은 이번 변경으로 달라지지 않는다."""
    async def ok(image_url, session_id):
        return BLACK_URL

    calls = _install(monkeypatch, prepare=ok, submit_result=(3, [{"ok": False}]))
    res = _run()
    assert calls["refund"] == []
    assert res.submitted == 3
    assert res.status == "partial"
    assert res.credits_charged == 4
