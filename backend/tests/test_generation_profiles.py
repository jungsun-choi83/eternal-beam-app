"""
생성 프로파일 (Phase 6.5 비용 절약) 테스트 — 실 프로바이더 호출 없음.

계약: 해상도/길이만 프로파일이 정한다. 종횡비·오디오·카메라·신원 입력·QA·후보
한도는 프로파일과 무관하게 불변이며, 길이는 모션 스펙 최소 아래로 내려가지 않는다.
"""

from __future__ import annotations

import pytest

from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import motion_spec as ms
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry

from .test_canonical_pet_builder import GOOD
from .test_motion_video_generation import FakeVideoProvider, _build_motion, _prepare_pipeline


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    monkeypatch.delenv("PHASE6_GENERATION_PROFILE", raising=False)
    monkeypatch.delenv("PHASE6_RESOLUTION", raising=False)
    for m in (refs, pet_registry, ids, sets, canon, kf, mv):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf, mv):
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


def _spec_for(motion_id: str, **env):
    spec = ms.MOTIONS[motion_id]
    return mv.build_output_spec(
        duration_range=spec.duration_range_sec, motion_class=spec.motion_class
    )


# ══════════════════════════════════════════════════════════════════════════
# 프로파일 단위
# ══════════════════════════════════════════════════════════════════════════


def test_test_profile_uses_480p_and_short_durations(monkeypatch):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")

    blink, w = _spec_for("BLINKING")           # MICRO (3.0~6.0)
    assert blink["resolution"] == "480p"
    assert blink["duration_sec"] == 3
    assert blink["profile"] == "test"
    assert w == []

    pet_head, _ = _spec_for("PET_HEAD")        # INTERACTION (3.0~5.0) — 목표 3~4s
    assert 3 <= pet_head["duration_sec"] <= 4

    lie_down, _ = _spec_for("LIE_DOWN")        # TRANSITION (2.5~5.0)
    assert lie_down["duration_sec"] == 4

    run, _ = _spec_for("RUN")                  # LOCOMOTION (3.0~6.0)
    assert run["duration_sec"] == 4


def test_profile_never_shortens_below_spec_minimum(monkeypatch):
    """BREATHING 스펙 최소 4s < test 목표 3s → 최소 유지 + 보고 (강제 단축 없음)."""
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    breath, warnings = _spec_for("BREATHING")  # MICRO 이지만 (4.0~6.0)
    assert breath["duration_sec"] == 4  # 3 이 아니라 스펙 최소
    assert any("spec minimum preserved" in w for w in warnings)


def test_profile_invariants_hold_in_test_mode(monkeypatch):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    spec, _ = _spec_for("BLINKING")
    # 프로파일이 바꿀 수 없는 것들: 종횡비·오디오·카메라.
    assert spec["aspect_ratio"] == "9:16"
    assert spec["audio"] is False
    assert spec["camera_fixed"] is True


def test_benchmark_profile_restores_720p_and_midpoints(monkeypatch):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "benchmark")
    breath, w = _spec_for("BREATHING")
    assert breath["resolution"] == "720p"
    assert breath["duration_sec"] == 5  # (4+6)/2 — 기존 동작 그대로
    assert w == []


def test_default_and_production_match_current_behavior():
    # env 미설정(기본) = benchmark = 기존 동작. production 도 현재는 동일 값이다.
    default, _ = _spec_for("BREATHING")
    assert default["profile"] == "benchmark"
    assert default["resolution"] == "720p" and default["duration_sec"] == 5

    import os

    os.environ["PHASE6_GENERATION_PROFILE"] = "production"
    try:
        prod, _ = _spec_for("BREATHING")
    finally:
        del os.environ["PHASE6_GENERATION_PROFILE"]
    assert prod["resolution"] == "720p" and prod["duration_sec"] == 5


def test_explicit_resolution_env_overrides_profile(monkeypatch):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    monkeypatch.setenv("PHASE6_RESOLUTION", "1080p")
    spec, _ = _spec_for("BLINKING")
    assert spec["resolution"] == "1080p"  # 운영자 명시 오버라이드가 우선


def test_unknown_profile_falls_back_to_benchmark_with_warning(monkeypatch):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "ultra-cheap")
    spec, warnings = _spec_for("BLINKING")
    assert spec["profile"] == "benchmark"
    assert any("unknown PHASE6_GENERATION_PROFILE" in w for w in warnings)


# ══════════════════════════════════════════════════════════════════════════
# 빌드 통합 — 프로바이더 요청까지 명시적으로 전달된다
# ══════════════════════════════════════════════════════════════════════════


def test_build_under_test_profile_sends_explicit_params(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    provider = FakeVideoProvider("seedance", [GOOD()])

    v = _build_motion(h, "BLINKING", [provider])
    req = provider.requests[0]
    # 프로바이더가 스스로 해상도/길이를 고를 수 없다 — 요청에 명시된다.
    assert req.output_spec["resolution"] == "480p"
    assert req.output_spec["duration_sec"] == 3
    assert req.output_spec["aspect_ratio"] == "9:16"
    assert req.output_spec["audio"] is False
    assert v.output_spec["profile"] == "test"  # 버전 행에 프로파일이 박제된다


def test_breathing_build_reports_minimum_preservation(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.output_spec["duration_sec"] == 4  # 스펙 최소 유지
    assert any("spec minimum preserved" in w for w in v.warnings)
