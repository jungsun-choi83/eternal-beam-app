"""close_position_service 단위 테스트 (COME_CLOSER → RETURN_HOME, 2026-10-23 단순화).

COME_CLOSER 가 QA 를 통과해 발행되면 그 영상의 **리터럴 마지막 프레임**을 그대로
CLOSE_POSITION 으로 박제한다. 종단 구간 스캔/프레임 선택/사용성 게이트 없음.
ffmpeg 는 스텁한다(환경 비의존).
"""

from __future__ import annotations

import inspect
import io
from types import SimpleNamespace

import anyio
import numpy as np
import pytest

from backend.services import close_position_service as cps
from backend.services import motion_delivery_service
from backend.services import motion_video_qa as qa_mod
from backend.services import pet_generation_run_service as runs
from backend.services import premium_run_fulfillment as prf
from backend.services import supabase_assets


def _run(coro):
    return anyio.run(lambda: coro)


def _frame(val: int = 120) -> np.ndarray:
    rng = np.random.default_rng(val)
    return rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)


def test_no_frame_selection_or_gates_remain():
    for name in ("select_terminal_frame", "_frame_is_usable", "_laplacian_variance",
                 "_TERMINAL_FRACTIONS", "TerminalFrameResult"):
        assert not hasattr(cps, name), name


def test_final_frame_is_literal_last_frame_only(monkeypatch):
    # 마지막 프레임(분율 1.0)만 요청한다 — 다른 분율/관찰/스캔 없음.
    seen = []

    def fake_extract(vb, fraction):
        seen.append(fraction)
        return b"LASTPNG"

    def boom(*a, **k):
        raise AssertionError("must not sample/observe other frames")

    monkeypatch.setattr(qa_mod, "extract_frame_png", fake_extract)
    monkeypatch.setattr(qa_mod, "sample_frames", boom)
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", boom)
    assert cps.extract_final_frame_png(b"video") == b"LASTPNG"
    assert seen == [1.0]


def test_final_frame_pil_fallback_samples_only_last(monkeypatch):
    calls = []
    monkeypatch.setattr(qa_mod, "extract_frame_png", lambda vb, f: None)

    def fake_sample(vb, fractions=None):
        calls.append(fractions)
        return [_frame(7)]

    monkeypatch.setattr(qa_mod, "sample_frames", fake_sample)
    png = cps.extract_final_frame_png(b"video")
    assert calls == [(1.0,)]
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        assert im.format == "PNG" and im.size == (64, 48)
        assert np.array_equal(np.asarray(im.convert("RGB")), _frame(7))  # 무손실


def test_final_frame_none_when_undecodable(monkeypatch):
    monkeypatch.setattr(qa_mod, "extract_frame_png", lambda vb, f: None)
    monkeypatch.setattr(qa_mod, "sample_frames", lambda *a, **k: None)
    assert cps.extract_final_frame_png(b"video") is None


def test_persist_stores_exact_final_frame_with_lineage(monkeypatch):
    uploaded, recorded = {}, {}

    async def fake_upload(object_path, data, content_type):
        uploaded.update(path=object_path, bytes=bytes(data), ct=content_type)

    async def fake_record_derived(**kw):
        recorded.update(kw)
        return SimpleNamespace(id="ref-close-1")

    monkeypatch.setattr(cps.supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(cps.pet_reference_service, "record_derived", fake_record_derived)

    out = _run(cps.persist_close_position(
        user_id="user-1", content_id="c1", pet_id="pet_c1",
        come_closer_run_id="run-abc", come_closer_motion_version_id="mv-1",
        selected_candidate_id="cand-1", source_video_object_path="user-1/c1/motion/raw.mp4",
        png=b"EXACT-FINAL-PNG",
    ))
    assert uploaded["bytes"] == b"EXACT-FINAL-PNG" and uploaded["ct"] == "image/png"
    assert out.object_path == uploaded["path"] == "user-1/c1/derived/close_position_run-abc.png"
    assert out.reference_id == "ref-close-1"
    assert recorded["derived_kind"] == cps.DERIVED_KIND_CLOSE_POSITION
    diag = recorded["diagnostics"]["close_position"]
    assert diag["come_closer_run_id"] == "run-abc"
    assert diag["selected_candidate_id"] == "cand-1"
    assert diag["frame"] == "final"
    assert diag["identity_corrected"] is False


def _published_run():
    return SimpleNamespace(
        motion_id="COME_CLOSER", user_id="u", pet_id="pet_c1", content_id="c1",
        selected_candidate_id="cand", motion_version_id="mv", id="cc-run",
    )


def test_published_come_closer_final_frame_reaches_return_home_start(monkeypatch):
    """발행된 COME_CLOSER → 마지막 프레임 PNG 그대로 저장 → 그 경로가 RETURN_HOME 의
    start_image_override 로 전달된다."""
    stored, ensured = {}, {}

    async def fake_load(cid):
        return {"raw_video_path": "u/c1/raw.mp4", "raw_bucket": "b"}

    async def fake_download(path, bucket=None):
        return b"RAWVIDEO"

    async def fake_upload(object_path, data, content_type):
        stored["bytes"] = bytes(data)
        stored["path"] = object_path

    async def fake_record(**kw):
        return SimpleNamespace(id="ref-9")

    async def fake_ensure(*, user_id, pet_id, action_id, source_run_id, start_image_override=None):
        assert source_run_id == "cc-run"
        ensured.update(action=action_id, override=start_image_override)
        return "rh-1"

    monkeypatch.setattr(motion_delivery_service, "_load_candidate", fake_load)
    monkeypatch.setattr(supabase_assets, "download_asset_from_storage", fake_download)
    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(cps.pet_reference_service, "record_derived", fake_record)
    monkeypatch.setattr(qa_mod, "extract_frame_png",
                        lambda vb, f: b"FINAL" if (vb == b"RAWVIDEO" and f == 1.0) else None)
    monkeypatch.setattr(prf, "ensure_system_companion", fake_ensure)

    _run(runs._maybe_enqueue_system_companion(_published_run()))
    assert stored["bytes"] == b"FINAL"                        # 정확히 그 마지막 프레임
    assert ensured["action"] == "COME_CLOSER"
    assert ensured["override"]["object_path"] == stored["path"]  # RETURN_HOME 시작 = 그 파일
    assert ensured["override"]["reference_id"] == "ref-9"


def test_unextractable_final_frame_creates_no_return_home(monkeypatch):
    ensured = []

    async def fake_load(cid):
        return {"raw_video_path": "p"}

    async def fake_download(path, bucket=None):
        return b"RAW"

    async def fake_ensure(**kw):
        ensured.append(kw)

    monkeypatch.setattr(motion_delivery_service, "_load_candidate", fake_load)
    monkeypatch.setattr(supabase_assets, "download_asset_from_storage", fake_download)
    monkeypatch.setattr(qa_mod, "extract_frame_png", lambda vb, f: None)
    monkeypatch.setattr(qa_mod, "sample_frames", lambda *a, **k: None)
    monkeypatch.setattr(prf, "ensure_system_companion", fake_ensure)
    _run(runs._maybe_enqueue_system_companion(_published_run()))
    assert ensured == []


def test_failed_or_fallback_come_closer_never_enqueues_companion():
    """_maybe_enqueue_system_companion 의 호출처는 DELIVERED_GENERATED 발행 직후 단 한
    곳 — 실패/폴백 경로에는 호출이 없다."""
    src = inspect.getsource(runs)
    assert src.count("await _maybe_enqueue_system_companion(") == 1
    call = src.index("await _maybe_enqueue_system_companion(")
    assert src.rfind("business_qa.DELIVERED_GENERATED", 0, call) > src.rfind(
        "business_qa.DELIVERED_FALLBACK", 0, call
    )
    assert "STATUS_PUBLISHED" in src[call - 2500:call]
