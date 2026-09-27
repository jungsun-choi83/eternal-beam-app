"""
모션 QA + 배송(delivery) 레이턴시 병렬화 계약 테스트.

이 병렬화가 반드시 지켜야 하는 것:
  1. 서로 독립인 QA 가지(conformance/probe vs 프레임 샘플링·시간축·VLM 체인)는
     실제로 동시에 돈다.
  2. 최종 판정은 여전히 모든 필수 검사가 끝난 뒤에만 내려진다 — 어느 한쪽이
     끝나기 전에 결정이 확정되지 않는다.
  3/4. packed-alpha 포장은 QA 가 이미 뽑아 둔 probe(불변 raw 바이트 기준)를
     재사용해도, 재사용하지 않을 때와 **완전히 같은** 프레임/fps 를 낸다.
  5. QA 판정(PASS/REVIEW/FAIL, checks, temporal 증거)은 이 병렬화 이전과
     동일하다 — 실행 방식만 바뀌었다.
  6. 동시성 도입이 프로바이더/VLM 호출 횟수를 늘리지 않는다.
"""

from __future__ import annotations

import io
import subprocess
import threading
import time

import anyio
import numpy as np
import pytest
from PIL import Image

from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import motion_delivery_service as delivery
from backend.services import motion_video_qa as qa_mod
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity

from .test_action_keyframes import install_kf_vlm, VLM_KF_OK, _build_kf, _prepare_canonical
from .test_canonical_pet_builder import FakeProvider, GOOD
from .test_motion_video_generation import (
    VLM_MV_OK,
    FakeVideoProvider,
    conformance_ok,
    install_mv_vlm,
    sampler_identical,
)
from .test_pet_reference_sets import PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    monkeypatch.delenv("VIDEO_GENERATION_MOCK", raising=False)
    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "0")
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _run(coro):
    return anyio.run(lambda: coro)


def _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE",)):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    for role in roles:
        built = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], role=role)
        assert built.status == kf.STATUS_COMPLETE
    install_mv_vlm(monkeypatch, VLM_MV_OK)
    return h, canonical


def _build_motion(h, motion_id, providers, *, sampler=sampler_identical, conformance=conformance_ok, **kw):
    return _run(
        mv.build_motion_video(
            user_id=USER, pet_id=PET, motion_id=motion_id,
            fetch_bytes=h.kf_fetch, providers=providers, frame_sampler=sampler,
            conformance_fn=conformance, **kw,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1) 독립 QA 가지(conformance vs 프레임/VLM 체인)가 실제로 동시에 돈다
# ══════════════════════════════════════════════════════════════════════════


def test_conformance_probe_overlaps_frame_and_vlm_chain(storage, monkeypatch):
    from backend.services import breathing_temporal_qa

    h, _ = _prepare_pipeline(monkeypatch, storage)
    delay = 0.12
    lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}

    # BREATHING 의 시간축 분석 + 추가 VLM 프레임(별도로 아래에서 검증)은 이
    # 테스트의 관심사가 아니다 — 실제 ffmpeg/ffprobe 왕복 시간이 섞여 신호가
    # 흐려지지 않도록 즉시 반환하는 값으로 고정한다.
    monkeypatch.setattr(
        breathing_temporal_qa, "analyze",
        lambda video_bytes, keyframe_rgb: {
            "version": breathing_temporal_qa.BREATHING_TEMPORAL_QA_VERSION,
            "verdict": "unmeasurable", "reason": "test_stub",
        },
    )
    monkeypatch.setattr(qa_mod, "sample_frames", lambda video_bytes, fractions=None: None)

    def slow_sampler(video_bytes):
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        time.sleep(delay)
        result = sampler_identical(video_bytes)
        with lock:
            state["inflight"] -= 1
        return result

    def slow_conformance(video_bytes, output_spec):
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        time.sleep(delay)
        with lock:
            state["inflight"] -= 1
        return conformance_ok(video_bytes, output_spec)

    provider = FakeVideoProvider("seedance", [GOOD()])
    started = time.monotonic()
    v = _build_motion(h, "BREATHING", [provider], sampler=slow_sampler, conformance=slow_conformance)
    elapsed = time.monotonic() - started

    assert v.status == mv.STATUS_COMPLETE
    assert provider.calls == 1
    assert state["max_inflight"] >= 2, "sampler 와 conformance 가 실제로 동시에 돌지 않았다"
    # 순차였다면 2*delay(~0.24s) 이상. 겹치면 delay 하나 정도(~0.12~0.2s).
    assert elapsed < delay * 1.8, f"conformance 와 프레임 체인이 겹치지 않은 것 같다 (elapsed={elapsed:.3f}s)"


def test_breathing_temporal_and_extra_vlm_frames_overlap(storage, monkeypatch):
    """BREATHING 전용 시간축 분석 + VLM 근접쌍 추가 샘플링도 서로 동시에 돈다."""
    from backend.services import breathing_temporal_qa

    h, _ = _prepare_pipeline(monkeypatch, storage)
    delay = 0.12
    lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}

    def slow_analyze(video_bytes, keyframe_rgb):
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        time.sleep(delay)
        with lock:
            state["inflight"] -= 1
        return {"version": breathing_temporal_qa.BREATHING_TEMPORAL_QA_VERSION,
                "verdict": "unmeasurable", "reason": "test_stub"}

    def slow_sample_frames(video_bytes, fractions=None):
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        time.sleep(delay)
        with lock:
            state["inflight"] -= 1
        return None  # 측정 불가 — _breathing_evidence 는 base_frames 로 폴백한다

    monkeypatch.setattr(breathing_temporal_qa, "analyze", slow_analyze)
    monkeypatch.setattr(qa_mod, "sample_frames", slow_sample_frames)

    started = time.monotonic()
    temporal, frames, fractions = _run(
        mv._breathing_evidence("BREATHING", b"fake-video-bytes", None, [None] * 9)
    )
    elapsed = time.monotonic() - started

    assert temporal is not None and temporal["verdict"] == "unmeasurable"
    assert state["max_inflight"] >= 2, "시간축 분석과 추가 VLM 프레임 샘플링이 동시에 돌지 않았다"
    assert elapsed < delay * 1.8, f"두 디코딩이 겹치지 않은 것 같다 (elapsed={elapsed:.3f}s)"


# ══════════════════════════════════════════════════════════════════════════
# 2) 최종 판정은 여전히 모든 필수 검사가 끝난 뒤에만 내려진다
# ══════════════════════════════════════════════════════════════════════════


def test_final_decision_waits_for_conformance_even_when_qa_chain_is_slow(storage, monkeypatch):
    """conformance 가 FAIL 이면, 프레임/VLM 체인이 전부 PASS 라도 최종 FAIL 이다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)

    def slow_sampler(video_bytes):
        time.sleep(0.05)
        return sampler_identical(video_bytes)

    def conformance_fail(video_bytes, output_spec):
        return {
            "version": qa_mod.OUTPUT_CONFORMANCE_VERSION,
            "status": "FAIL",
            "checks": {"aspect_ratio": "FAIL"},
            "reasons": ["aspect_mismatch requested 9:16 got 16:9"],
            "probe": {"width": 1280, "height": 720, "duration": 5.0, "has_audio": False, "fps": 24.0},
        }

    provider = FakeVideoProvider("seedance", [GOOD()])
    v = _build_motion(h, "BREATHING", [provider], sampler=slow_sampler, conformance=conformance_fail)

    candidate = v.candidates[0]
    assert candidate.decision == "FAIL"
    assert candidate.qa_result["output_conformance"]["status"] == "FAIL"
    assert any(r.startswith("output_conformance:") for r in candidate.qa_result["reasons"])
    # 필수 검사(신원/구조/모션)는 별도로 계속 계산되고 기록된다 — conformance
    # 가 다른 검사를 "건너뛰게" 만들지 않는다.
    assert candidate.qa_result["checks"].get("identity_over_time") in ("PASS", "REVIEW", "unknown")


def test_final_decision_waits_for_frame_and_vlm_chain_even_when_conformance_is_slow(storage, monkeypatch):
    """프레임/VLM 체인이 REVIEW/FAIL 이면, 빠른 conformance PASS 가 그걸 가리지 않는다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)

    def unknown_sampler(_video_bytes):
        return [None] * 9  # 프레임 샘플링 실패 — identity/temporal 이 unknown

    def slow_conformance(video_bytes, output_spec):
        time.sleep(0.05)
        return conformance_ok(video_bytes, output_spec)

    provider = FakeVideoProvider("seedance", [GOOD()])
    v = _build_motion(h, "BREATHING", [provider], sampler=unknown_sampler, conformance=slow_conformance)

    candidate = v.candidates[0]
    assert candidate.qa_result["checks"]["identity_over_time"] == "unknown"
    assert candidate.decision != "PASS"
    assert candidate.qa_result["output_conformance"]["status"] == "PASS"


# ══════════════════════════════════════════════════════════════════════════
# 3/4) packed-alpha: QA probe 재사용 — 출력은 재사용 여부와 무관하게 동일하다
# ══════════════════════════════════════════════════════════════════════════


def _make_real_clip(tmp_path, *, w=64, h=48, n=6) -> bytes:
    png = tmp_path / "f.png"
    Image.fromarray(np.full((h, w, 3), 120, dtype=np.uint8)).save(png)
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-loop", "1", "-i", str(png),
         "-t", "1", "-r", str(n), "-pix_fmt", "yuv420p", str(out)],
        check=True, timeout=60,
    )
    return out.read_bytes()


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None or __import__("shutil").which("ffprobe") is None,
    reason="ffmpeg 필요",
)
def test_decode_video_reuses_qa_probe_without_reprobing(tmp_path, monkeypatch):
    video_bytes = _make_real_clip(tmp_path)

    # QA(motion_video_qa.verify_output_conformance)가 이미 계산해 뒀을 probe.
    real_probe = qa_mod._probe_video_streams(video_bytes)
    assert real_probe and real_probe["width"] and real_probe["height"] and real_probe["fps"]

    baseline_frames, baseline_fps = delivery.decode_video(video_bytes)

    def boom(_path):
        raise AssertionError("probe 가 재사용되지 않고 다시 ffprobe 됐다")

    monkeypatch.setattr(delivery, "_probe_stream", boom)
    reused_frames, reused_fps = delivery.decode_video(video_bytes, probe=real_probe)

    assert reused_fps == baseline_fps
    assert len(reused_frames) == len(baseline_frames)
    for a, b in zip(reused_frames, baseline_frames):
        assert np.array_equal(a, b), "재사용 경로가 다른 프레임을 냈다 — packed-alpha 출력이 바뀐다"


@pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None or __import__("shutil").which("ffprobe") is None,
    reason="ffmpeg 필요",
)
def test_decode_video_falls_back_when_reused_probe_is_incomplete(tmp_path):
    video_bytes = _make_real_clip(tmp_path)
    # fps 가 없는 불완전한 probe — 안전하게 자체 probe 로 돌아가야 한다(크래시 금지).
    incomplete = {"width": 64, "height": 48, "duration": 1.0, "has_audio": False}
    frames, fps = delivery.decode_video(video_bytes, probe=incomplete)
    assert frames and fps > 0


def test_package_breathing_for_delivery_passes_stored_probe_through(storage, monkeypatch):
    """운영 경로: candidate.qa_result 에 남은 probe 가 decode_video 로 그대로 전달된다."""
    from backend.services import canonical_pet_service

    captured: dict[str, object] = {}

    def fake_decode(raw, *, probe=None):
        captured["probe"] = probe
        return [np.zeros((4, 4, 3), dtype=np.uint8)] * 3, 24.0

    def fake_matte(frames):
        return [np.full(f.shape[:2], 255, dtype=np.uint8) for f in frames], {"backend": "test"}

    def fake_encode(frames, fps):
        return b"packed-bytes"

    version_row = {
        "id": "v1", "pet_id": PET, "user_id": USER, "motion_id": "BREATHING",
        "selected_candidate_id": "c1",
    }
    candidate_row = {
        "id": "c1", "motion_version_id": "v1", "pet_id": PET, "user_id": USER,
        "motion_id": "BREATHING", "decision": "PASS",
        "raw_bucket": "b", "raw_video_path": "raw.mp4",
        "generation_metadata": {},
        "qa_result": {
            "output_conformance": {
                "probe": {"width": 64, "height": 48, "fps": 24.0, "duration": 1.0, "has_audio": False}
            }
        },
    }
    _run(canonical_pet_service._insert(mv._versions_table(), mv._MOCK_VERSIONS, version_row))
    _run(canonical_pet_service._insert(mv._candidates_table(), mv._MOCK_CANDIDATES, candidate_row))

    monkeypatch.setattr(delivery, "decode_video", fake_decode)
    result = _run(
        delivery.package_breathing_for_delivery(
            user_id=USER, pet_id=PET, motion_version_id="v1",
            video_bytes=b"raw-bytes-not-decoded-by-a-real-codec",
            matte_fn=fake_matte, encode_fn=fake_encode,
            upload_fn=lambda path, data: None,
        )
    )
    assert result.deduplicated is False
    assert captured["probe"] == candidate_row["qa_result"]["output_conformance"]["probe"]


# ══════════════════════════════════════════════════════════════════════════
# 5) QA 판정은 병렬화 이전과 동일하다
# ══════════════════════════════════════════════════════════════════════════


def test_breathing_qa_decision_matches_pre_parallelization_expectations(storage, monkeypatch):
    """
    BREATHING 후보의 판정(PASS) + temporal evidence + checks 는 이 병렬화가
    실행 방식만 바꿨을 뿐, 값은 그대로임을 확인한다.
    """
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()])
    v = _build_motion(h, "BREATHING", [provider])

    assert v.status == mv.STATUS_COMPLETE
    candidate = v.candidates[0]
    qa = candidate.qa_result
    assert qa["qa_version"] == qa_mod.MOTION_VIDEO_QA_VERSION
    assert qa["sampling_version"] == qa_mod.FRAME_SAMPLING_VERSION
    assert qa["checks"]["identity_over_time"] == "PASS"
    assert qa["checks"]["temporal_stability"] == "PASS"
    assert qa["output_conformance"]["status"] == "PASS"
    assert candidate.decision == "PASS"


def test_evaluate_motion_video_is_a_pure_function_untouched_by_refactor():
    """
    evaluate_motion_video 자체(판정 로직)는 이 작업에서 건드리지 않았다 —
    같은 입력 → 같은 출력을 직접 확인한다(동시성과 무관한 순수 함수 계약).
    """
    contract = {"motion_class": "MICRO", "requirements": {"qa": {}}, "video_compat": {}}
    out1 = qa_mod.evaluate_motion_video(
        frames=None, spec_contract=contract, start_keyframe_rgb=None,
        target_keyframe_rgb=None, vlm_qa=None, temporal_qa=None,
    )
    out2 = qa_mod.evaluate_motion_video(
        frames=None, spec_contract=contract, start_keyframe_rgb=None,
        target_keyframe_rgb=None, vlm_qa=None, temporal_qa=None,
    )
    assert out1["decision"] == out2["decision"] == "REVIEW"
    assert out1["checks"] == out2["checks"]


# ══════════════════════════════════════════════════════════════════════════
# 6) 동시성 도입이 프로바이더/VLM 호출 횟수를 늘리지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_no_extra_provider_or_vlm_calls_from_concurrency(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    vlm_calls = {"n": 0}

    def counting_vlm(*args, **kwargs):
        vlm_calls["n"] += 1
        return VLM_MV_OK

    monkeypatch.setattr(vlm_identity, "qa_motion_video", counting_vlm)

    conformance_calls = {"n": 0}

    def counting_conformance(video_bytes, output_spec):
        conformance_calls["n"] += 1
        return conformance_ok(video_bytes, output_spec)

    provider = FakeVideoProvider("seedance", [GOOD()])
    v = _build_motion(h, "BREATHING", [provider], conformance=counting_conformance)

    assert v.status == mv.STATUS_COMPLETE
    assert provider.calls == 1  # 유료 생성 호출 — 정확히 한 번
    assert vlm_calls["n"] == 1  # 유료 VLM 호출 — 정확히 한 번 (중복 없음)
    assert conformance_calls["n"] == 1  # 무료지만, 동시성이 중복 실행을 만들지 않는다


def test_breathing_no_extra_decode_calls_from_concurrency(storage, monkeypatch):
    """BREATHING 의 두 독립 디코딩(시간축/추가 VLM 프레임)도 정확히 한 번씩만 돈다."""
    from backend.services import breathing_temporal_qa

    h, _ = _prepare_pipeline(monkeypatch, storage)
    analyze_calls = {"n": 0}
    sample_calls = {"n": 0}

    real_sample_frames = qa_mod.sample_frames

    def counting_analyze(video_bytes, keyframe_rgb):
        analyze_calls["n"] += 1
        return {"version": breathing_temporal_qa.BREATHING_TEMPORAL_QA_VERSION,
                "verdict": "unmeasurable", "reason": "test_stub"}

    def counting_sample_frames(video_bytes, fractions=None):
        sample_calls["n"] += 1
        return None

    monkeypatch.setattr(breathing_temporal_qa, "analyze", counting_analyze)
    monkeypatch.setattr(qa_mod, "sample_frames", counting_sample_frames)

    provider = FakeVideoProvider("seedance", [GOOD()])
    v = _build_motion(h, "BREATHING", [provider])

    assert v.status in (mv.STATUS_COMPLETE, mv.STATUS_REVIEW, mv.STATUS_FAILED)
    assert provider.calls == 1
    assert analyze_calls["n"] == 1
    assert sample_calls["n"] == 1
