"""
모션 비디오 생성 (Phase 6) 계약 테스트.

프로바이더/샘플러/VLM 전부 주입 — 실 결제 호출 없음. QA/라우팅/버전/근거는 실제 코드.
"""

from __future__ import annotations

import shutil

import anyio
import numpy as np
import pytest
from fastapi import FastAPI

from backend.routers import motion_videos_v1
from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import durable_provider_jobs
from backend.services import motion_video_qa as qa_mod
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry, vlm_identity
from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    MotionVideoResult,
    VideoGenerationProvider,
    VideoProviderError,
)

from .conftest import ASGITestClient
from .test_canonical_pet_builder import GOOD, FakeProvider
from .test_action_keyframes import VLM_KF_OK, _build_kf, _prepare_canonical, install_kf_vlm
from .test_pet_reference_sets import PET, USER


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")  # 개별 테스트가 되돌려 검증한다
    monkeypatch.delenv("VIDEO_GENERATION_MOCK", raising=False)
    # 레거시 하네스는 200×150 가짜 프레임을 그대로 공급한다 — 앵커는 전용
    # 테스트(test_video_anchor_*)에서 켜서 검증한다.
    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "0")
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


def _run(coro):
    return anyio.run(lambda: coro)


VLM_MV_OK = {
    "same_pet_all_frames": "yes",
    "anatomy_plausible_all_frames": "yes",
    "requested_motion_occurs": "yes",
    "unintended_large_motion": "no",
    "single_pet": "yes",
    "duplicated_pet": "no",
    "human_present": "no",
    "scene_cut": "no",
    "major_flicker": "no",
    "camera_stable": "yes",
    "background_neutral": "yes",
    "ends_in_target_pose": "yes",
    "notes": "",
    "source": vlm_identity.VLM_MOTION_QA_VERSION,
    "model": "test-stub",
}


def install_mv_vlm(monkeypatch, result):
    monkeypatch.setattr(
        vlm_identity, "qa_motion_video", lambda *a, **kw: result
    )


class FakeVideoProvider(VideoGenerationProvider):
    def __init__(self, name: str, results: list, *, end_frame: bool = True, model: str = "fake-v"):
        self.name = name
        self.supports_end_frame = end_frame
        self._results = list(results)
        self._model = model
        self.calls = 0
        self.requests: list = []

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return self._model

    def generate(self, request):
        self.calls += 1
        self.requests.append(request)
        if not self._results:
            raise VideoProviderError("PROVIDER_FAILED", "no more fake videos")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return MotionVideoResult(
            video_bytes=item, provider=self.name, model=self._model,
            external_job_id=f"{self.name}-job-{self.calls}",
        )


def sampler_identical(video_bytes: bytes):
    """"영상"(실은 시작 키프레임 PNG) → 동일 프레임 5장 — 완벽한 안정 클립."""
    rgb = mv._rgb_from_bytes(video_bytes)
    return [rgb] * 5 if rgb is not None else None


def white_frame() -> np.ndarray:
    return np.full((150, 200, 3), 255, dtype=np.uint8)


def _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE",)):
    h, canonical = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, VLM_KF_OK)
    for role in roles:
        built = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])], role=role)
        assert built.status == kf.STATUS_COMPLETE
    install_mv_vlm(monkeypatch, VLM_MV_OK)
    return h, canonical


def conformance_ok(video_bytes, output_spec):
    return {
        "version": qa_mod.OUTPUT_CONFORMANCE_VERSION,
        "status": "PASS",
        "checks": {"aspect_ratio": "PASS", "resolution": "PASS", "duration": "PASS", "audio_disabled": "PASS"},
        "reasons": [],
        "probe": {"width": 720, "height": 1280, "duration": 5.0, "has_audio": False},
    }


def _build_motion(h, motion_id, providers, sampler=sampler_identical, conformance=conformance_ok, **kw):
    return _run(
        mv.build_motion_video(
            user_id=USER, pet_id=PET, motion_id=motion_id,
            fetch_bytes=h.kf_fetch, providers=providers, frame_sampler=sampler,
            conformance_fn=conformance, **kw,
        )
    )


def test_durable_motion_resumes_one_building_version(storage, monkeypatch):
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "1")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")

    class YieldOnce(FakeVideoProvider):
        durable_execution = True

        def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise durable_provider_jobs.ProviderWorkPending("operation-1", "PENDING")
            return MotionVideoResult(
                video_bytes=GOOD(), provider=self.name, model=self.model_name(),
                external_job_id="job-1",
            )

    provider = YieldOnce("seedance", [])
    with pytest.raises(durable_provider_jobs.ProviderWorkPending):
        _build_motion(h, "BREATHING", [provider])
    assert len(_run(mv._version_rows(PET, "BREATHING"))) == 1

    completed = _build_motion(h, "BREATHING", [provider])
    assert completed.status == mv.STATUS_COMPLETE
    assert len(_run(mv._version_rows(PET, "BREATHING"))) == 1
    assert len(completed.candidates) == 1


# ══════════════════════════════════════════════════════════════════════════
# 라우팅 정책
# ══════════════════════════════════════════════════════════════════════════


def test_routing_by_motion_class(monkeypatch):
    assert [p.name for p in vp.routing_for_class("MICRO")] == ["seedance", "kling"]
    assert [p.name for p in vp.routing_for_class("TRANSITION")] == ["kling"]
    assert [p.name for p in vp.routing_for_class("LOCOMOTION")] == ["seedance", "kling"]
    assert [p.name for p in vp.routing_for_class("INTERACTION")] == ["kling", "seedance"]
    monkeypatch.setenv("PHASE6_PROVIDER_MICRO", "kling")
    assert vp.routing_for_class("MICRO")[0].name == "kling"
    monkeypatch.setenv("VIDEO_GENERATION_MOCK", "1")
    assert [p.name for p in vp.routing_for_class("MICRO")] == ["mock"]


# ══════════════════════════════════════════════════════════════════════════
# MICRO — BREATHING
# ══════════════════════════════════════════════════════════════════════════


def test_micro_breathing_success(storage, monkeypatch):
    h, canonical = _prepare_pipeline(monkeypatch, storage)
    primary = FakeVideoProvider("seedance", [GOOD(), GOOD(), GOOD()])
    fallback = FakeVideoProvider("kling", [GOOD()])

    v = _build_motion(h, "BREATHING", [primary, fallback])
    assert v.status == mv.STATUS_COMPLETE and v.version == 1
    assert v.motion_class == "MICRO" and v.video_strategy == "IMAGE_TO_VIDEO"
    assert v.motion_spec_version == __import__("backend.services.motion_spec", fromlist=["x"]).MOTION_SPEC_VERSION
    assert fallback.calls == 0
    assert primary.calls == 1  # stop_after_passes 기본 1 — 비디오는 비싸다

    # 계약 소비: 시작 키프레임만, end 프레임 없음.
    req = primary.requests[0]
    assert req.start_image_bytes and req.end_image_bytes is None
    # 명시적 출력 사양 — 프로바이더 기본값에 기대지 않는다.
    assert req.output_spec["aspect_ratio"] == "9:16"
    assert req.output_spec["resolution"] == "720p"
    assert 4 <= req.output_spec["duration_sec"] <= 6
    assert req.output_spec["audio"] is False
    assert req.output_spec["camera_fixed"] is True

    sel = next(c for c in v.candidates if c.selected)
    assert sel.qa_result["decision"] == "PASS"
    assert sel.qa_result["checks"]["loop_return"] == "PASS"  # returns_to_start_pose
    assert sel.qa_result["identity_similarity"] and sel.qa_result["identity_similarity"] > 0.9
    assert v.canonical_version_id == canonical.id


def test_prompts_by_class_and_no_themes(storage, monkeypatch):
    from backend.services.motion_video_prompts import build_motion_video_prompt
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "LIE"))
    breath = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    lie = _build_motion(h, "LIE_DOWN", [FakeVideoProvider("kling", [GOOD()])])

    assert "returned to exactly the starting pose" in breath.prompt
    assert "End exactly in the supplied target pose" in lie.prompt
    assert breath.prompt_version == "motion-video-prompt-v1"
    for p in (breath.prompt, lie.prompt):
        low = p.lower()
        for key in ALL_THEME_KEYS:
            assert key not in low and key.replace("_", " ") not in low

    pet_head_prompt = build_motion_video_prompt(
        {"motion_class": "INTERACTION",
         "video_compat": {"allow_generated_hand": True, "returns_to_start_pose": True}},
        "머리 쓰다듬기 반응",
    )
    assert "hand MAY enter" in pet_head_prompt


# ══════════════════════════════════════════════════════════════════════════
# TRANSITION — LIE_DOWN
# ══════════════════════════════════════════════════════════════════════════


def test_transition_sends_both_frames(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "LIE"))
    provider = FakeVideoProvider("kling", [GOOD()])

    v = _build_motion(h, "LIE_DOWN", [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "START_END_FRAME"
    req = provider.requests[0]
    assert req.start_image_bytes is not None
    assert req.end_image_bytes is not None  # 목표 프레임을 버리지 않는다
    sel = next(c for c in v.candidates if c.selected)
    assert sel.start_keyframe_id and sel.target_keyframe_id
    assert sel.qa_result["checks"]["reaches_target_pose"] == "PASS"


def test_transition_without_end_capable_provider_fails_safely(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "LIE"))
    incapable = FakeVideoProvider("seedance", [GOOD()], end_frame=False)

    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "LIE_DOWN", [incapable])
    assert e.value.code == "ROUTING_UNSUPPORTED" and e.value.status == 503
    assert incapable.calls == 0  # start-only 강등 시도조차 없다


def test_transition_missing_target_keyframe_safe(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)  # NEUTRAL_IDLE 만
    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "LIE_DOWN", [FakeVideoProvider("kling", [GOOD()])])
    assert e.value.code == "TARGET_KEYFRAME_REQUIRED"


# ══════════════════════════════════════════════════════════════════════════
# LOCOMOTION / INTERACTION
# ══════════════════════════════════════════════════════════════════════════


def test_locomotion_fallback_warning_persisted(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "COME_CLOSER", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO"  # 레퍼런스 라이브러리 없음 → 선언된 폴백
    assert any("DOG_APPROACH" in w for w in v.warnings)
    sel = next(c for c in v.candidates if c.selected)
    assert sel.motion_reference_id == "DOG_APPROACH"  # 메타데이터는 보존된다


def test_interaction_pet_head(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "PET_HEAD", [FakeVideoProvider("kling", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE
    assert v.motion_class == "INTERACTION"
    assert "hand MAY enter" in v.prompt  # 스펙이 허용할 때만


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 게이트 / 라이브 안전
# ══════════════════════════════════════════════════════════════════════════


def test_review_keyframe_rejected(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # 키프레임 REVIEW
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert k.status == kf.STATUS_REVIEW
    install_mv_vlm(monkeypatch, VLM_MV_OK)

    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert e.value.code == "KEYFRAME_REQUIRED"


def test_live_safety_blocks_before_any_row(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_LIVE_MODE", "off")
    provider = FakeVideoProvider("seedance", [GOOD()])

    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "BREATHING", [provider])
    assert e.value.code == "LIVE_GENERATION_BLOCKED" and e.value.status == 403
    assert provider.calls == 0
    assert _run(mv._version_rows(PET)) == []  # 행도, 과금도 없다

    monkeypatch.setenv("PHASE6_LIVE_MODE", "allowlist")
    monkeypatch.setenv("PHASE6_LIVE_ALLOWLIST", f"other_pet,{PET}")
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE


# ══════════════════════════════════════════════════════════════════════════
# 후보 정책 / 실패 구분
# ══════════════════════════════════════════════════════════════════════════


def test_provider_error_vs_qa_failure_and_fallback(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    err = VideoProviderError("PROVIDER_FAILED", "boom")
    primary = FakeVideoProvider("seedance", [err, err, err])
    fallback = FakeVideoProvider("kling", [GOOD()])

    v = _build_motion(h, "BREATHING", [primary, fallback])
    assert v.status == mv.STATUS_COMPLETE
    errors = [c for c in v.candidates if c.provider == "seedance"]
    # 클래스 인지 상한: BREATHING(MICRO) PRIMARY 기본 2 — 실패가 이어져도 그 이상
    # 과금하지 않고 폴백으로 넘어간다.
    assert len(errors) == 2 and all(c.decision == "ERROR" and c.error for c in errors)
    assert next(c for c in v.candidates if c.selected).provider == "kling"


def test_contract_violation_consumes_no_retries_and_no_fallback(storage, monkeypatch):
    """어댑터/스키마 계약 실패 (PROVIDER_SCHEMA/CONTRACT) — QA 실패가 아니다.

    같은 잘못된 요청을 반복하지 않고, 폴백 프로바이더 과금도 태우지 않는다.
    """
    h, _ = _prepare_pipeline(monkeypatch, storage)
    err = VideoProviderError("PROVIDER_SCHEMA", "fal 결과가 문서화된 스키마와 다릅니다")
    primary = FakeVideoProvider("seedance", [err, GOOD(), GOOD()])
    fallback = FakeVideoProvider("kling", [GOOD()])

    v = _build_motion(h, "BREATHING", [primary, fallback])
    assert primary.calls == 1        # 반복 없음
    assert fallback.calls == 0       # 폴백 금지
    assert v.status == mv.STATUS_FAILED
    assert "contract" in (v.selection_reason or "")
    errors = [c for c in v.candidates if c.decision == "ERROR"]
    assert len(errors) == 1
    assert errors[0].generation_metadata.get("contract_violation") is True


def test_video_anchor_applied_for_non_916_keyframe(storage, monkeypatch):
    """1:1 키프레임 → 결정론적 9:16 DERIVED 앵커가 프로바이더 시작 이미지가 된다."""
    import io as _io

    from PIL import Image

    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "1")
    h, _ = _prepare_pipeline(monkeypatch, storage)

    class EchoProvider(FakeVideoProvider):
        """완벽한 프로바이더 — 시작 이미지(앵커) 그대로의 정지 클립을 돌려준다."""

        def generate(self, request):
            self.calls += 1
            self.requests.append(request)
            from backend.services.video_motion_providers import MotionVideoResult

            return MotionVideoResult(
                video_bytes=request.start_image_bytes, provider=self.name,
                model=self._model, external_job_id="echo-1",
            )

    primary = EchoProvider("seedance", [])
    v = _build_motion(h, "BREATHING", [primary])
    assert v.status == mv.STATUS_COMPLETE

    req = primary.requests[0]
    with Image.open(_io.BytesIO(req.start_image_bytes)) as im:
        w, hh = im.size
    assert w * 16 == hh * 9  # 시작 이미지가 정확한 9:16 앵커다
    assert req.start_image_url and req.start_image_url.endswith("_anchor9x16.png")

    # 앵커는 DERIVED 로 대장에 기록된다 (원본 키프레임 근거 포함).
    anchors = [
        r for r in _run(refs.list_references(user_id=USER, pet_id=PET))
        if r.derived_kind == "video_anchor"
    ]
    assert len(anchors) == 1

    sel = next(c for c in v.candidates if c.selected)
    anchor_meta = sel.generation_metadata["video_anchor"]["start"]
    assert anchor_meta["canvas_size"][0] * 16 == anchor_meta["canvas_size"][1] * 9
    assert any(r["kind"] == "video_anchor_start" for r in sel.input_references)
    assert any(p.endswith("_anchor9x16.png") for p in storage)


def test_progressive_generation_qa_gates_each_next_attempt(storage, monkeypatch):
    """점진적 생성: 후보 1 FAIL → 그때서야 후보 2 → PASS → 즉시 중단.

    "3개를 만들어 놓고 평가"가 아니라 시도마다 QA 판정이 다음 제출을 게이트한다 —
    제출·QA 의 인터리브 순서를 실제로 기록해 검증한다.
    """
    h, _ = _prepare_pipeline(monkeypatch, storage)
    order: list[str] = []

    calls = {"n": 0}

    def first_fails_sampler(video_bytes):
        calls["n"] += 1
        order.append(f"qa{calls['n']}")
        rgb = mv._rgb_from_bytes(video_bytes)
        if calls["n"] == 1:  # 후보 1 만 정체성 붕괴 → FAIL
            return [rgb, rgb, white_frame(), white_frame(), white_frame()]
        return [rgb] * 5

    primary = FakeVideoProvider("seedance", [GOOD(), GOOD(), GOOD()])
    original_generate = primary.generate

    def tracking_generate(request):
        order.append(f"gen{primary.calls + 1}")
        return original_generate(request)

    primary.generate = tracking_generate

    v = _build_motion(h, "BREATHING", [primary], sampler=first_fails_sampler)
    assert v.status == mv.STATUS_COMPLETE
    # 제출과 QA 가 엄격히 교대한다 — 후보 2 는 후보 1 의 판정 뒤에만 나간다.
    assert order == ["gen1", "qa1", "gen2", "qa2"]
    assert primary.calls == 2  # MICRO 상한(2) 안에서 두 번째가 PASS → 중단
    decisions = [c.decision for c in sorted(v.candidates, key=lambda c: c.attempt)]
    assert decisions == ["FAIL", "PASS"]
    assert next(c for c in v.candidates if c.selected).attempt == 2


def test_qa_failure_triggers_fallback(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)

    calls = {"n": 0}

    def drifting_sampler(video_bytes):
        # 첫 프로바이더의 클립은 정체성이 무너진다(흰 프레임으로 드리프트).
        # 클래스 인지 상한(MICRO 기본 2)과 무관하게 "seedance 는 전부 FAIL,
        # kling 은 PASS" 를 유지하려고 호출 횟수가 아니라 상한을 기준으로 센다.
        calls["n"] += 1
        rgb = mv._rgb_from_bytes(video_bytes)
        if calls["n"] <= mv.candidate_policy("MICRO")["max_primary"]:
            return [rgb, rgb, white_frame(), white_frame(), white_frame()]
        return [rgb] * 5

    primary = FakeVideoProvider("seedance", [GOOD(), GOOD(), GOOD()])
    fallback = FakeVideoProvider("kling", [GOOD()])
    v = _build_motion(h, "BREATHING", [primary, fallback], sampler=drifting_sampler)

    assert v.status == mv.STATUS_COMPLETE
    assert all(c.decision == "FAIL" for c in v.candidates if c.provider == "seedance")
    assert next(c for c in v.candidates if c.selected).provider == "kling"


def test_candidate_limits_and_persistence(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, None)  # VLM 없음 → 전부 REVIEW → 상한까지
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_MAX_FALLBACK", "1")
    primary = FakeVideoProvider("seedance", [GOOD()] * 10)
    fallback = FakeVideoProvider("kling", [GOOD()] * 10)

    v = _build_motion(h, "BREATHING", [primary, fallback])
    assert primary.calls == 2 and fallback.calls == 1
    assert v.status == mv.STATUS_REVIEW and v.selected_candidate_id is None
    # 후보는 QA 이전에 저장된다 — raw 가 전부 스토리지에 있다.
    for c in v.candidates:
        assert c.raw_video_path in storage


# ══════════════════════════════════════════════════════════════════════════
# QA 단위 — 프레임 샘플링 기반
# ══════════════════════════════════════════════════════════════════════════


def _good_frame() -> np.ndarray:
    return mv._rgb_from_bytes(GOOD())


def _eval(frames, *, contract=None, target=None, vlm=VLM_MV_OK):
    return qa_mod.evaluate_motion_video(
        frames=frames,
        spec_contract=contract
        or {"motion_class": "MICRO", "video_compat": {"returns_to_start_pose": True}},
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=target,
        vlm_qa=vlm,
    )


def test_qa_pass_on_stable_identical_frames():
    r = _eval([_good_frame()] * 5)
    assert r["decision"] == "PASS"
    assert r["checks"]["identity_over_time"] == "PASS"
    assert r["checks"]["temporal_stability"] == "PASS"
    assert len(r["frame_similarities"]) == 5


def test_qa_identity_drift_fails():
    r = _eval([_good_frame(), _good_frame(), white_frame(), white_frame(), white_frame()])
    assert r["checks"]["identity_over_time"] == "FAIL"
    assert r["decision"] == "FAIL"  # FAIL 은 절대 fail-open 되지 않는다


def test_qa_scene_cut_fails_temporal():
    r = _eval([_good_frame(), white_frame(), _good_frame(), _good_frame(), _good_frame()])
    assert r["checks"]["temporal_stability"] == "FAIL"
    assert r["decision"] == "FAIL"


def test_qa_loop_return_review_when_end_pose_differs():
    from .test_pet_identity_profile import make_striped_cutout_png

    striped = mv._rgb_from_bytes(make_striped_cutout_png())
    g = _good_frame()
    r = _eval([g, g, g, g, striped])
    assert r["checks"]["loop_return"] == "REVIEW"
    assert r["decision"] in ("REVIEW", "FAIL")


def test_qa_loop_return_uses_structure_not_hsv_bin_cliff():
    # 60% of pixels move only two gray levels across the coarse HSV V-bin
    # boundary. v1's histogram says the frames are only 0.4 similar even
    # though their structure/pixels are perceptually the same.
    first = np.full((150, 200, 3), 90, dtype=np.uint8)
    first[:, :120] = 127
    last = first.copy()
    last[:, :120] = 129
    r = qa_mod.evaluate_motion_video(
        frames=[first, first, first, first, last],
        spec_contract={"motion_class": "MICRO", "video_compat": {"returns_to_start_pose": True}},
        start_keyframe_rgb=first,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["loop_metrics"]["legacy_hist_intersection"] < 0.85
    assert r["loop_metrics"]["ssim_first_vs_decoded_last"] > 0.99
    assert r["checks"]["loop_return"] == "PASS"


def test_qa_transition_endpoint_checks():
    g = _good_frame()
    contract = {"motion_class": "TRANSITION", "video_compat": {}}
    ok = _eval([g] * 5, contract=contract, target=g)
    assert ok["checks"]["reaches_target_pose"] == "PASS"

    bad = qa_mod.evaluate_motion_video(
        frames=[g, g, g, g, white_frame()],
        spec_contract=contract,
        start_keyframe_rgb=g,
        target_keyframe_rgb=g,
        vlm_qa=VLM_MV_OK,
    )
    assert bad["checks"]["reaches_target_pose"] == "FAIL"
    assert bad["decision"] == "FAIL"


def test_qa_without_vlm_caps_at_review():
    r = _eval([_good_frame()] * 5, vlm=None)
    assert r["decision"] == "REVIEW"
    assert "vlm_qa_unavailable" in r["reasons"]


def test_qa_vlm_anatomy_failure_fails():
    r = _eval([_good_frame()] * 5, vlm={**VLM_MV_OK, "anatomy_plausible_all_frames": "no"})
    assert r["decision"] == "FAIL"


def test_qa_sampling_unavailable_is_review_not_pass():
    r = _eval(None)
    assert r["checks"]["identity_over_time"] == "unknown"
    assert r["decision"] == "REVIEW"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg 필요")
def test_real_frame_sampling_from_mp4(tmp_path):
    import subprocess

    png = tmp_path / "f.png"
    png.write_bytes(GOOD())
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-loop", "1", "-i", str(png),
         "-t", "1", "-pix_fmt", "yuv420p", "-vf", "scale=200:150", str(out)],
        check=True, timeout=60,
    )
    frames = qa_mod.sample_frames(out.read_bytes())
    assert frames is not None and len(frames) == len(qa_mod.SAMPLE_FRACTIONS)
    assert len(frames) == 9
    assert sum(1 for f in frames if f is not None) >= 7


def test_versioned_qa_rerun_reuses_asset_and_is_idempotent(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "1")
    old_unknown = {
        **VLM_MV_OK,
        "requested_motion_occurs": "unknown",
        "source": "vlm-motion-qa-v1",
        "notes": "still frames insufficient",
    }
    install_mv_vlm(monkeypatch, old_unknown)
    provider = FakeVideoProvider("seedance", [GOOD()])
    version = _build_motion(h, "BREATHING", [provider])
    assert version.status == mv.STATUS_REVIEW
    candidate = version.candidates[0]

    calls = {"vlm": 0}

    def corrected_vlm(*args, **kwargs):
        calls["vlm"] += 1
        return VLM_MV_OK

    rerun = _run(
        mv.reevaluate_motion_candidate(
            user_id=USER,
            pet_id=PET,
            motion_id="BREATHING",
            motion_version_id=version.id,
            candidate_id=candidate.id,
            video_bytes=GOOD(),
            fetch_bytes=h.kf_fetch,
            frame_sampler=sampler_identical,
            vlm_qa_fn=corrected_vlm,
            conformance_fn=conformance_ok,
        )
    )
    assert rerun.status == mv.STATUS_COMPLETE
    assert rerun.selected_candidate_id == candidate.id
    selected = next(c for c in rerun.candidates if c.selected)
    assert selected.decision == "PASS"
    assert selected.qa_result["qa_version"] == qa_mod.MOTION_VIDEO_QA_VERSION
    assert selected.generation_metadata["qa_history"][0]["decision"] == "REVIEW"
    assert provider.calls == 1  # QA retry made no generation-provider call.

    duplicate = _run(
        mv.reevaluate_motion_candidate(
            user_id=USER,
            pet_id=PET,
            motion_id="BREATHING",
            motion_version_id=version.id,
            candidate_id=candidate.id,
            video_bytes=GOOD(),
            fetch_bytes=h.kf_fetch,
            frame_sampler=sampler_identical,
            vlm_qa_fn=corrected_vlm,
            conformance_fn=conformance_ok,
        )
    )
    assert duplicate.deduplicated is True
    assert calls["vlm"] == 1
    assert provider.calls == 1


def test_qa_rerun_rejects_wrong_user(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    version = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    with pytest.raises(mv.MotionVideoError) as exc:
        _run(
            mv.reevaluate_motion_candidate(
                user_id="mallory@test",
                pet_id=PET,
                motion_id="BREATHING",
                motion_version_id=version.id,
                candidate_id=version.candidates[0].id,
                video_bytes=GOOD(),
            )
        )
    assert exc.value.code == "PET_NOT_OWNED"


# ══════════════════════════════════════════════════════════════════════════
# 버전 / 근거 / 결정론 / 소유권
# ══════════════════════════════════════════════════════════════════════════


def test_versioning_idempotency_and_no_repay(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()] * 10)

    v1 = _build_motion(h, "BREATHING", [provider])
    calls = provider.calls
    again = _build_motion(h, "BREATHING", [provider])
    assert again.deduplicated is True and again.version == 1
    assert provider.calls == calls

    v2 = _build_motion(h, "BREATHING", [provider], skip_if_unchanged=False)
    assert v2.version == 2
    old = _run(mv.get_motion_version(user_id=USER, pet_id=PET, motion_id="BREATHING", version=1))
    assert old.id == v1.id and old.selected_candidate_id == v1.selected_candidate_id


def test_provenance_chain(storage, monkeypatch):
    h, canonical = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])

    kf_row = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="NEUTRAL_IDLE"))
    assert v.start_keyframe_id == kf_row.id
    assert v.start_keyframe_version == kf_row.version
    assert v.canonical_version_id == canonical.id

    ledger = _run(refs.list_references(user_id=USER, pet_id=PET))
    motion_assets = [r for r in ledger if r.role == refs.ROLE_GENERATED and r.derived_kind == "motion_raw"]
    assert len(motion_assets) == 1
    prov = motion_assets[0].diagnostics
    assert prov["motion_spec_version"] == __import__("backend.services.motion_spec", fromlist=["x"]).MOTION_SPEC_VERSION
    assert prov["start_keyframe_id"] == kf_row.id
    assert prov["canonical_version_id"] == canonical.id


def test_deterministic_ranking(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "3")
    a = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()] * 3)], skip_if_unchanged=False)
    b = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()] * 3)], skip_if_unchanged=False)
    sa = next(c for c in a.candidates if c.selected)
    sb = next(c for c in b.candidates if c.selected)
    assert (sa.provider, sa.attempt, sa.decision) == (sb.provider, sb.attempt, sb.decision)


def test_ownership_isolation(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    with pytest.raises(mv.MotionVideoError) as e:
        _run(
            mv.build_motion_video(
                user_id="mallory@test", pet_id=PET, motion_id="BREATHING",
                providers=[FakeVideoProvider("seedance", [GOOD()])],
            )
        )
    assert e.value.code == "PET_NOT_OWNED"


# ══════════════════════════════════════════════════════════════════════════
# 라우터 / 평가 하네스
# ══════════════════════════════════════════════════════════════════════════


AUTH = {"Authorization": "Bearer test:alice@test"}


@pytest.fixture
def client(monkeypatch) -> ASGITestClient:
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    app = FastAPI()
    app.include_router(motion_videos_v1.router, prefix="/api")
    return ASGITestClient(app)


def test_router_build_get_list(client, storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setattr(ids, "_default_fetch_bytes", h.kf_fetch)
    monkeypatch.setattr(
        vp, "routing_for_class", lambda cls: [FakeVideoProvider("seedance", [GOOD()] * 3)]
    )
    monkeypatch.setattr(mv, "sampler_identical", sampler_identical, raising=False)
    monkeypatch.setattr(qa_mod, "sample_frames", sampler_identical)
    monkeypatch.setattr(qa_mod, "verify_output_conformance", conformance_ok)

    res = client.post(f"/api/v1/pet/motions/{PET}/BREATHING/build", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "complete" and body["selected_candidate_id"]
    assert body["output_spec"]["audio"] is False

    res = client.get(f"/api/v1/pet/motions/{PET}", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["motions"][0]["motion_id"] == "BREATHING"

    res = client.get(f"/api/v1/pet/motions/{PET}/BREATHING", headers=AUTH)
    assert res.status_code == 200
    assert res.json()["candidates"]

    res = client.get(f"/api/v1/pet/motions/{PET}/LIE_DOWN", headers=AUTH)
    assert res.status_code == 404

    res = client.get(
        f"/api/v1/pet/motions/{PET}/BREATHING",
        headers={"Authorization": "Bearer test:mallory@test"},
    )
    assert res.status_code == 403


def test_motion_evaluation_harness(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    sel = next(c for c in v.candidates if c.selected)

    _run(
        mv.record_motion_evaluation(
            user_id=USER, pet_id=PET, motion_version_id=v.id, candidate_id=sel.id,
            scores={"identity_fidelity": 9, "markings": 8, "anatomy": 9,
                    "motion_correctness": 8, "temporal_stability": 9,
                    "naturalness": 8, "start_end_quality": 9},
            verdict="PASS", overall_usable=True,
        )
    )
    summary = _run(canon.evaluation_summary(user_id=USER))
    assert summary["providers"]["seedance"]["count"] == 1
    assert summary["providers"]["seedance"]["mean_scores"]["motion_correctness"] == 8.0
