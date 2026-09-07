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

    blink, w = _spec_for("BLINKING")           # MICRO — 고정 4.0s (motion-spec-v6)
    assert blink["resolution"] == "480p"
    assert blink["duration_sec"] == 4
    assert blink["profile"] == "test"
    assert w == []

    pet_head, _ = _spec_for("PET_HEAD")        # INTERACTION (3.0~5.0) — 목표 3~4s
    assert 3 <= pet_head["duration_sec"] <= 4

    lie_down, _ = _spec_for("LIE_DOWN")        # TRANSITION (2.5~5.0)
    assert lie_down["duration_sec"] == 4

    run, _ = _spec_for("RUN")                  # LOCOMOTION (3.0~6.0)
    assert run["duration_sec"] == 4


def test_profile_never_shortens_below_spec_minimum(monkeypatch):
    """프로파일 목표 < 스펙 최소 → 최소 유지 + 보고 (강제 단축 없음).

    v6 이후 등록된 모션 중엔 이 클램프에 걸리는 조합이 없다 (test MICRO 목표 4 =
    스펙 고정 4). 가드 자체는 남는다 — 미래 스펙이 최소를 올리면 그대로 지켜야
    하므로 합성 범위로 계약을 계속 못박는다.
    """
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    spec, warnings = mv.build_output_spec(duration_range=(5.0, 7.0), motion_class="MICRO")
    assert spec["duration_sec"] == 5  # 목표 4 가 아니라 스펙 최소
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
    assert breath["duration_sec"] == 4  # 고정 범위 (4,4)의 중앙값 = 4 (v6)
    assert w == []
    # 중앙값 규칙 자체는 불변이다 — 범위가 있는 클래스에서 그대로 동작한다.
    lie_down, _ = _spec_for("LIE_DOWN")  # TRANSITION (2.5~5.0)
    assert lie_down["duration_sec"] == 4  # round(3.75)


def test_default_and_production_match_current_behavior():
    # env 미설정(기본) = benchmark = 기존 동작. production 도 현재는 동일 값이다.
    default, _ = _spec_for("BREATHING")
    assert default["profile"] == "benchmark"
    assert default["resolution"] == "720p" and default["duration_sec"] == 4

    import os

    os.environ["PHASE6_GENERATION_PROFILE"] = "production"
    try:
        prod, _ = _spec_for("BREATHING")
    finally:
        del os.environ["PHASE6_GENERATION_PROFILE"]
    assert prod["resolution"] == "720p" and prod["duration_sec"] == 4


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
    assert req.output_spec["duration_sec"] == 4  # MICRO 고정 4.0s (v6)
    assert req.output_spec["aspect_ratio"] == "9:16"
    assert req.output_spec["audio"] is False
    assert v.output_spec["profile"] == "test"  # 버전 행에 프로파일이 박제된다


def test_breathing_build_uses_fixed_micro_duration(storage, monkeypatch):
    """v6: test 목표 4 = 스펙 고정 4 — 클램프도 경고도 없이 정확히 4s 다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.output_spec["duration_sec"] == 4
    assert not any("spec minimum preserved" in w for w in v.warnings)


# ══════════════════════════════════════════════════════════════════════════
# MICRO 고정 4.0s (motion-spec-v6) — 3초 Seedance 요청은 만들어질 수 없다
# ══════════════════════════════════════════════════════════════════════════

#: 상용/런타임 MICRO 5종 — 요구사항이 명시한 대상. 레지스트리의 나머지 MICRO
#: (LOOK_UP/HAPPY/LIE_IDLE/SLEEP_BREATH)도 같은 정책이며 아래 전수 검사가 덮는다.
_RUNTIME_MICRO = ("BREATHING", "BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING")


def test_runtime_micro_motions_are_micro_class():
    for mid in _RUNTIME_MICRO:
        assert ms.MOTIONS[mid].motion_class == ms.CLASS_MICRO


@pytest.mark.parametrize("profile", ["test", "benchmark", "production"])
def test_all_micro_motions_request_exactly_4s_in_every_profile(monkeypatch, profile):
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", profile)
    micro = [m for m in ms.MOTIONS.values() if m.motion_class == ms.CLASS_MICRO]
    assert {m.motion_id for m in micro} >= set(_RUNTIME_MICRO)
    for spec in micro:
        assert spec.duration_range_sec == (4.0, 4.0), spec.motion_id
        out, warnings = mv.build_output_spec(
            duration_range=spec.duration_range_sec, motion_class=spec.motion_class
        )
        assert out["duration_sec"] == 4, f"{spec.motion_id} ({profile})"
        assert warnings == [], f"{spec.motion_id} ({profile})"


def test_other_classes_are_unchanged_by_micro_policy():
    """MICRO 만 고정이다 — 다른 클래스의 스펙 범위는 v5 값 그대로다."""
    assert ms.MOTIONS["LIE_DOWN"].duration_range_sec == (2.5, 5.0)     # TRANSITION
    assert ms.MOTIONS["STAND_UP"].duration_range_sec == (2.0, 4.0)     # TRANSITION
    assert ms.MOTIONS["COME_CLOSER"].duration_range_sec == (3.0, 6.0)  # LOCOMOTION
    assert ms.MOTIONS["PET_HEAD"].duration_range_sec == (3.0, 5.0)     # INTERACTION


@pytest.mark.parametrize("profile", ["test", "benchmark", "production"])
def test_micro_output_spec_passes_seedance_preflight_on_both_transports(monkeypatch, profile):
    """실제 Seedance 어댑터의 계약 검증(build_payload, HTTP 이전)을 통과한다.

    TAIL_WAGGING 라이브 실패(run ebbc11f5)의 역: MICRO 출력 사양이 Runway 의
    4..30s / fal 의 ≥4s 하한을 위반하는 조합은 이제 존재하지 않는다.
    """
    from backend.services.video_motion_providers import (
        FalSeedanceProvider,
        MotionVideoRequest,
        RunwaySeedanceProvider,
    )

    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", profile)
    runway, fal = RunwaySeedanceProvider(), FalSeedanceProvider()
    for spec in ms.MOTIONS.values():
        if spec.motion_class != ms.CLASS_MICRO:
            continue
        out, _ = mv.build_output_spec(
            duration_range=spec.duration_range_sec, motion_class=spec.motion_class
        )
        req = MotionVideoRequest(
            prompt="x", start_image_url="https://cdn.test/start.png",
            start_image_bytes=None, output_spec=out,
        )
        assert runway.build_payload(req)["duration"] == 4, spec.motion_id
        assert fal.build_payload(req)["duration"] == "4", spec.motion_id


def test_three_second_seedance_request_is_still_refused_preflight():
    """가드 회귀 방지: 3s 사양이 어떻게든 만들어지면 HTTP 이전에 거절된다."""
    from backend.services.video_motion_providers import (
        MotionVideoRequest,
        RunwaySeedanceProvider,
        VideoProviderError,
    )

    req = MotionVideoRequest(
        prompt="x", start_image_url="https://cdn.test/start.png",
        start_image_bytes=None,
        output_spec={"duration_sec": 3, "aspect_ratio": "9:16", "resolution": "480p"},
    )
    with pytest.raises(VideoProviderError) as e:
        RunwaySeedanceProvider().build_payload(req)
    assert e.value.code == "PROVIDER_CONTRACT"


def test_tail_wagging_build_sends_4s_to_seedance(storage, monkeypatch):
    """라이브 실패를 그대로 뒤집은 통합 검증 — TAIL_WAGGING 제출 사양이 4s 다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    provider = FakeVideoProvider("seedance", [GOOD()])
    _build_motion(h, "TAIL_WAGGING", [provider])
    assert provider.requests[0].output_spec["duration_sec"] == 4
