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
from backend.services import breathing_temporal_qa as breathing_qa
from backend.services import business_qa
from backend.services import canonical_pet_service as canon
from backend.services import durable_provider_jobs
from backend.services import motion_spec as ms
from backend.services import motion_video_qa as qa_mod
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_morphology_service as morph
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


VLM_MV_OK = {
    "same_pet_all_frames": "yes",
    "anatomy_plausible_all_frames": "yes",
    "requested_motion_occurs": "yes",
    "locomotion_form_correct": "yes",
    "direction_travel_correct": "yes",
    "interaction_correct": "yes",
    "human_hand_policy_ok": "yes",
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
    """"영상"(실은 시작 키프레임 PNG) → 동일 프레임 5장 — 완벽한 안정 클립.

    프로바이더에 실제로 보내는 입력은 **클린 플레이트**(누끼 + 고정 중립 배경)
    이므로, 돌아오는 프레임도 같은 중립 배경 위에 있다. 테스트의 가짜 "영상"은
    RGBA 누끼 PNG 라서 여기서 같은 규칙으로 합성해 준다 — 안 하면 QA 가 비교
    하는 두 이미지의 배경이 서로 달라 테스트만 어긋난다.
    """
    from backend.services import clean_plate_service as _cp

    try:
        plate, _meta = _cp.build_clean_plate(video_bytes)
    except _cp.CleanPlateError:
        plate = video_bytes
    rgb = mv._rgb_from_bytes(plate)
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


class _DurableJobVideoProvider(FakeVideoProvider):
    """유료 생성은 attempt 당 한 번만 — 재호출은 같은 영수증을 재사용한다.

    calls = generate() 호출 수, submissions = 실제 신규 결제(=신규 external
    job id 발급) 수. 실제 DurableVideoProvider 는 (phase_version_id, attempt,
    fingerprint) 로 중복 제출을 막는다 — 이 더블은 그 계약을 흉내 낸다.
    """

    durable_execution = True

    def __init__(self, name: str, video_bytes: bytes):
        super().__init__(name, [])
        self._video_bytes = video_bytes
        self.submissions = 0
        self._job_ids: dict[int, str] = {}

    def generate(self, request):
        self.calls += 1
        self.requests.append(request)
        attempt = request.metadata.get("attempt")
        if attempt not in self._job_ids:
            self.submissions += 1
            self._job_ids[attempt] = f"{self.name}-job-{attempt}"
        return MotionVideoResult(
            video_bytes=self._video_bytes, provider=self.name, model=self.model_name(),
            external_job_id=self._job_ids[attempt],
        )


def test_motion_storage_failure_recovers_same_candidate_no_resubmission(storage, monkeypatch):
    """유료 모션 생성 후 raw 저장만 실패해도 같은 후보가 재사용된다 — 재과금 없음."""
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_STOP_AFTER_PASSES", "1")

    from backend.services import supabase_assets

    upload_calls = {"n": 0}

    async def flaky_upload(path, data, content_type):
        upload_calls["n"] += 1
        if upload_calls["n"] == 1:
            raise RuntimeError("storage down")
        storage[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", flaky_upload)

    provider = _DurableJobVideoProvider("seedance", GOOD())

    with pytest.raises(durable_provider_jobs.ProviderRecoveryRequired):
        _build_motion(h, "BREATHING", [provider])

    # 1) 유료 생성은 정확히 한 번 — 저장 실패는 같은 후보에 기록되고, 버전은
    #    BUILDING 으로 남아 재개 가능하다 (다음 유료 후보로 넘어가지 않는다).
    assert provider.calls == 1
    assert provider.submissions == 1
    rows = _run(mv._version_rows(PET, "BREATHING"))
    assert len(rows) == 1 and rows[0]["status"] == mv.STATUS_BUILDING
    cands = _run(mv._candidate_rows(str(rows[0]["id"])))
    assert len(cands) == 1
    assert cands[0]["decision"] == "ERROR"
    assert cands[0]["error"] == "RAW_STORE_FAILED"
    assert cands[0]["provider_job_id"] == "seedance-job-1"  # provider job id 보존
    assert not cands[0].get("raw_video_path")

    # 2) 재시작/재개 — 같은 candidate/영수증을 재사용해 저장만 재시도한다.
    completed = _build_motion(h, "BREATHING", [provider])
    assert completed.status == mv.STATUS_COMPLETE
    assert provider.calls == 2        # 영수증 재사용을 위한 재호출
    assert provider.submissions == 1  # 새 결제는 없다 — 재제출 없음 증거
    assert len(completed.candidates) == 1
    assert completed.candidates[0].provider_job_id == "seedance-job-1"
    assert len(_run(mv._version_rows(PET, "BREATHING"))) == 1  # 새 버전/후보 없음


def test_motion_repeated_storage_failure_ends_recoverable_without_extra_paid_generation(storage, monkeypatch):
    """저장이 계속 실패해도 재시도마다 유료 후보가 늘지 않고 recoverable 상태에 머문다."""
    h, _canonical = _prepare_pipeline(monkeypatch, storage)
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")

    from backend.services import supabase_assets

    async def always_fails(path, data, content_type):
        raise RuntimeError("storage down")

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", always_fails)

    provider = _DurableJobVideoProvider("seedance", GOOD())

    for _ in range(3):
        with pytest.raises(durable_provider_jobs.ProviderRecoveryRequired):
            _build_motion(h, "BREATHING", [provider])

    assert provider.submissions == 1  # 반복 실패해도 새 유료 후보로 넘어가지 않는다
    rows = _run(mv._version_rows(PET, "BREATHING"))
    assert len(rows) == 1 and rows[0]["status"] == mv.STATUS_BUILDING
    cands = _run(mv._candidate_rows(str(rows[0]["id"])))
    assert len(cands) == 1  # 후보가 늘어나지 않았다


# ══════════════════════════════════════════════════════════════════════════
# 라우팅 정책
# ══════════════════════════════════════════════════════════════════════════


def test_routing_by_motion_class(monkeypatch):
    assert [p.name for p in vp.routing_for_class("MICRO")] == ["wan_3_standard", "seedance"]
    assert [p.name for p in vp.routing_for_class("TRANSITION")] == ["kling_3", "wan_3_standard"]
    assert [p.name for p in vp.routing_for_class("LOCOMOTION")] == ["kling_3", "wan_3_standard"]
    assert [p.name for p in vp.routing_for_class("INTERACTION")] == ["seedance", "kling_3"]
    monkeypatch.setenv("VIDEO_GENERATION_MOCK", "1")
    assert [p.name for p in vp.routing_for_class("MICRO")] == ["mock"]


def test_legacy_wan_is_explicit_registry_only_test_adapter(monkeypatch):
    """Wan(fal turbo)은 저비용 프롬프트 실험 전용 — motion_spec 기본 라우팅의
    wan_3_standard 와 별개이며, 명시 registry 해석으로만 선택된다."""
    from backend.services import durable_provider_jobs

    # 기본 라우팅에는 Wan 3 standard 만 있고 레거시 "wan" 은 없다.
    for cls in ("MICRO", "TRANSITION", "LOCOMOTION", "INTERACTION"):
        assert "wan" not in [p.name for p in vp.routing_for_class(cls)]

    # 명시 registry id 로만 온다 (트랜스포트는 fal 하나뿐).
    routed = vp.resolve_provider_order(["wan"])
    assert [p.name for p in routed] == ["wan"]
    assert isinstance(routed[0], vp.FalWanProvider)

    # 라이브 게이트는 실 프로바이더로 취급한다 — 허용 목록 없이는 막힌다.
    monkeypatch.setenv("PHASE6_LIVE_MODE", "off")
    allowed, reason = vp.live_generation_allowed("pet_x", routed)
    assert not allowed and reason == "live_mode_off"

    # durable(실행) 경로에는 절대 못 들어온다 — 벤치/스모크 전용이라는 계약.
    assert durable_provider_jobs.durable_video_providers(
        routed, run_id="r", user_id="u", pet_id="p"
    ) == []


def test_wan_payload_matches_live_proven_legacy_contract():
    """페이로드는 wan_service.py 에서 라이브 검증된 스키마 그대로 —
    {prompt, image_url, resolution, aspect_ratio} 뿐, duration/audio 키 없음
    (turbo 변형은 노출하지 않는다). end frame 은 계약상 거부된다."""
    provider = vp.FalWanProvider()
    req = vp.MotionVideoRequest(
        prompt="calm breathing",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=None,
        output_spec={"resolution": "480p", "aspect_ratio": "9:16",
                     "duration_sec": 4, "audio": False},
    )
    payload = provider.build_payload(req)
    assert payload == {
        "prompt": "calm breathing",
        "image_url": "https://cdn.test/start.png",
        "resolution": "480p",
        "aspect_ratio": "9:16",
    }
    assert provider.supports_end_frame is False
    assert provider.model_name() == "fal-ai/wan/v2.2-a14b/image-to-video/turbo"


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


def test_candidate_persists_logical_model_vendor_adapter_provenance(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()], model="seedance2_5")
    provider.logical_model_id = "seedance"
    provider.vendor_id = "runway"
    provider.adapter_id = "RunwaySeedanceProvider"

    version = _build_motion(h, "BREATHING", [provider])
    candidate = next(item for item in version.candidates if item.selected)
    assert candidate.provider == "seedance"
    assert candidate.model == "seedance2_5"
    assert candidate.generation_metadata["provider_identity"] == {
        "logical_model": "seedance",
        "vendor": "runway",
        "adapter": "RunwaySeedanceProvider",
        "vendor_model": "seedance2_5",
    }


def test_prompts_by_class_and_no_themes(storage, monkeypatch):
    from backend.services.motion_video_prompts import build_motion_video_prompt
    from backend.services.theme_catalog import ALL_THEME_KEYS

    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "STAND_READY", "LIE"))
    breath = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    lie = _build_motion(h, "LIE_DOWN", [FakeVideoProvider("kling", [GOOD()])])

    assert "returned to exactly the starting pose" in breath.prompt
    assert "End exactly in the supplied target pose" in lie.prompt
    assert breath.prompt_version == "motion-video-prompt-v2"
    for p_ in (breath.prompt, lie.prompt):
        assert "no contact shadow under the pet" in p_
        assert "no cast shadow on the background" in p_
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


def test_provider_receives_the_keyframe_clean_plate_not_the_raw_keyframe(storage, monkeypatch):
    """그림자 잔재의 경로를 끊는 핵심 계약 — I2V 입력은 plate 다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()])

    v = _build_motion(h, "BREATHING", [provider])
    assert v.status == mv.STATUS_COMPLETE

    keyframe = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="NEUTRAL_IDLE"))
    sel = next(c for c in keyframe.candidates if c.selected)
    assert sel.plate_object_path and sel.plate_object_path in storage

    sent = provider.requests[0].start_image_bytes
    assert sent == storage[sel.plate_object_path]
    assert sent != storage[sel.raw_object_path]   # raw 는 생성에 들어가지 않는다
    assert storage[sel.raw_object_path]           # …그러나 증거로 보존된다

    # 계보에도 "무엇을 보냈는가"가 적힌다.
    cand = next(c for c in v.candidates if c.selected)
    start_ref = next(r for r in cand.input_references if r["kind"] == "start_keyframe")
    assert start_ref["sent_input"]["kind"] == "clean_plate"
    assert start_ref["sent_input"]["object_path"] == sel.plate_object_path


def test_motion_fails_closed_when_no_keyframe_plate_can_be_built(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    for row in kf._MOCK_CANDIDATES:
        row["plate_bucket"] = row["plate_object_path"] = None
        row["cutout_bucket"] = row["cutout_object_path"] = None

    provider = FakeVideoProvider("seedance", [GOOD()])
    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "BREATHING", [provider])
    assert e.value.code == "KEYFRAME_PLATE_UNAVAILABLE"
    assert e.value.status == 503
    assert provider.calls == 0  # 과금 호출 0회


def test_transition_sends_both_frames(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "STAND_READY", "LIE"))
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
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("NEUTRAL_IDLE", "STAND_READY", "LIE"))
    incapable = FakeVideoProvider("seedance", [GOOD()], end_frame=False)

    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "LIE_DOWN", [incapable])
    assert e.value.code == "ROUTING_UNSUPPORTED" and e.value.status == 503
    assert incapable.calls == 0  # start-only 강등 시도조차 없다


def test_transition_missing_target_keyframe_safe(storage, monkeypatch):
    # 시작(STAND_READY)만 있고 목표(LIE)가 없다 — Phase 4 라우팅 반영.
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("STAND_READY",))
    with pytest.raises(mv.MotionVideoError) as e:
        _build_motion(h, "LIE_DOWN", [FakeVideoProvider("kling", [GOOD()])])
    assert e.value.code == "TARGET_KEYFRAME_REQUIRED"


# ══════════════════════════════════════════════════════════════════════════
# LOCOMOTION / INTERACTION
# ══════════════════════════════════════════════════════════════════════════


def test_locomotion_fallback_warning_persisted(storage, monkeypatch):
    # Phase 4: 이동은 STAND_READY 시작.
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("STAND_READY",))
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


def test_legacy_review_keyframe_is_business_deliverable(storage, monkeypatch):
    h, _ = _prepare_canonical(monkeypatch, storage)
    install_kf_vlm(monkeypatch, None)  # 키프레임 REVIEW
    k = _build_kf(h, [FakeProvider("runway", [GOOD(), GOOD(), GOOD()])])
    assert k.status == kf.STATUS_COMPLETE
    assert k.candidates[0].decision == "REVIEW"
    install_mv_vlm(monkeypatch, VLM_MV_OK)
    motion = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert motion.status == mv.STATUS_COMPLETE


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


def test_clean_plate_available_skips_raw_keyframe_download(storage, monkeypatch):
    """
    확인된 지연 병목의 회귀 가드: 키프레임 클린 플레이트가 이미 있으면(정상
    경로) raw 키프레임 바이트는 한 번도 내려받지 않는다 — 예전에는 존재 확인
    용도로만 raw 전체를 받고 버렸다.
    """
    h, _ = _prepare_pipeline(monkeypatch, storage)

    keyframe = _run(kf.get_keyframe(user_id=USER, pet_id=PET, keyframe_role="NEUTRAL_IDLE"))
    selected = next(c for c in keyframe.candidates if c.id == keyframe.selected_candidate_id)
    assert selected.plate_object_path  # 전제: 정상 빌드는 플레이트를 만들어 둔다

    fetch_counts: dict[str, int] = {}
    real_fetch = h.kf_fetch

    def counting_fetch(ref):
        fetch_counts[ref.object_path] = fetch_counts.get(ref.object_path, 0) + 1
        return real_fetch(ref)

    primary = FakeVideoProvider("seedance", [GOOD()])
    v = _run(
        mv.build_motion_video(
            user_id=USER, pet_id=PET, motion_id="BREATHING",
            fetch_bytes=counting_fetch, providers=[primary],
            frame_sampler=sampler_identical, conformance_fn=conformance_ok,
        )
    )
    assert v.status == mv.STATUS_COMPLETE
    assert fetch_counts.get(selected.raw_object_path, 0) == 0
    assert fetch_counts.get(selected.plate_object_path, 0) >= 1


def test_video_anchor_reused_without_rebuild_or_reupload(storage, monkeypatch):
    """
    확인된 지연 병목의 회귀 가드: 같은 키프레임+종횡비로 두 번째 모션 버전을
    만들 때, 9:16 앵커는 결정론적 object_path 로 재사용된다 — PIL 재인코딩도
    스토리지 재업로드도 없다. 프로바이더가 받는 시작 이미지 바이트는 그대로다.
    """
    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "1")
    monkeypatch.setenv("PHASE6_ASPECT_RATIO", "9:16")
    h, _ = _prepare_pipeline(monkeypatch, storage)

    from backend.services import supabase_assets

    orig_upload = supabase_assets.upload_asset_to_storage
    upload_calls = {"n": 0}

    async def counting_upload(path, data, content_type):
        upload_calls["n"] += 1
        return await orig_upload(path, data, content_type)

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", counting_upload)

    primary1 = FakeVideoProvider("seedance", [GOOD()])
    v1 = _build_motion(h, "BREATHING", [primary1])
    assert v1.status == mv.STATUS_COMPLETE
    n_after_first = upload_calls["n"]

    anchor_path = next(p for p in storage if p.endswith("_anchor9x16.png"))
    start_bytes_first = primary1.requests[0].start_image_bytes

    primary2 = FakeVideoProvider("seedance", [GOOD()])
    v2 = _build_motion(h, "BREATHING", [primary2], skip_if_unchanged=False)
    assert v2.status == mv.STATUS_COMPLETE
    assert v2.version == 2

    # 두 번째 빌드는 같은 앵커 경로를 그대로 재사용한다 — 업로드 횟수는 새
    # raw 영상 하나만큼만 늘어난다 (앵커 재빌드/재업로드 없음).
    assert upload_calls["n"] - n_after_first == 1
    anchor_paths = [p for p in storage if p.endswith("_anchor9x16.png")]
    assert anchor_paths == [anchor_path]  # 두 번째 앵커 경로가 새로 생기지 않았다
    # 프로바이더가 받는 시작 이미지는 재사용된 앵커와 바이트 단위로 동일하다.
    assert primary2.requests[0].start_image_bytes == start_bytes_first


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


def test_two_hard_qa_failures_stop_paid_generation_and_request_fallback(storage, monkeypatch):
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

    assert v.status == mv.STATUS_REVIEW
    assert all(c.decision == "FAIL" for c in v.candidates if c.provider == "seedance")
    assert primary.calls == 2 and fallback.calls == 0
    assert v.selected_candidate_id is None
    assert v.qa_summary["business_qa"]["selected"]["retry_action"] == "FALLBACK"
    assert v.qa_summary["business_qa"]["selected"]["terminal_state"] == "DELIVERED_FALLBACK"


def test_cosmetic_review_stops_after_one_and_persists_both_decisions(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, None)  # legacy REVIEW → deliver with advisory
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "2")
    monkeypatch.setenv("PHASE6_MAX_FALLBACK", "1")
    primary = FakeVideoProvider("seedance", [GOOD()] * 10)
    fallback = FakeVideoProvider("kling", [GOOD()] * 10)

    v = _build_motion(h, "BREATHING", [primary, fallback])
    assert primary.calls == 1 and fallback.calls == 0
    assert v.status == mv.STATUS_COMPLETE and v.selected_candidate_id is not None
    assert v.candidates[0].decision == "REVIEW"
    assert v.candidates[0].qa_result["business_qa"]["retry_action"] == "STOP"
    # 후보는 QA 이전에 저장된다 — raw 가 전부 스토리지에 있다.
    for c in v.candidates:
        assert c.raw_video_path in storage


def test_breathing_temporal_pass_stops_after_candidate_one_despite_vlm_motion_no(
    storage, monkeypatch
):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(
        monkeypatch,
        {**VLM_MV_OK, "requested_motion_occurs": "no"},
    )

    async def temporal_pass(motion_id, video_bytes, start_rgb, frames):
        return (
            {
                "version": breathing_qa.BREATHING_TEMPORAL_QA_VERSION,
                "verdict": breathing_qa.VERDICT_BREATHING,
                "reason": None,
                "advisories": [],
            },
            list(frames or []),
            tuple(qa_mod.SAMPLE_FRACTIONS),
        )

    monkeypatch.setattr(mv, "_breathing_evidence", temporal_pass)
    primary = FakeVideoProvider("seedance", [GOOD(), GOOD()])
    version = _build_motion(h, "BREATHING", [primary])

    assert primary.calls == 1
    assert len(version.candidates) == 1
    assert version.status == mv.STATUS_COMPLETE
    candidate = version.candidates[0]
    assert candidate.selected is True
    # Temporal authority settles the motion question; the VLM is still asked
    # the combined identity+anatomy question. Its motion "no" stays advisory:
    # the legacy decision records it, Business QA delivers.
    assert candidate.decision == "FAIL"
    assert candidate.qa_result["vlm_escalation"]["decision"] == "CALL"
    assert candidate.qa_result["vlm_escalation"]["requested_tasks"] == ["IDENTITY_ANATOMY_VLM"]
    assert candidate.qa_result["vlm_escalation"]["reason_codes"] == [
        "breathing_temporal_authority_clear",
        "breathing_identity_anatomy_required",
    ]
    assert candidate.qa_result["business_qa"]["integrity_status"] == "PASS"
    assert candidate.qa_result["business_qa"]["authority_profile"] == "breathing-v2"
    assert candidate.qa_result["business_qa"]["delivery_action"] == "DELIVER"
    assert candidate.qa_result["business_qa"]["retry_action"] == "STOP"


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


def test_registry_contract_promotes_only_severe_temporal_failure_to_integrity():
    contract = ms.motion_snapshot(ms.MOTIONS["BLINKING"])
    result = _eval(
        [_good_frame(), white_frame(), _good_frame(), _good_frame(), _good_frame()],
        contract=contract,
    )
    business_qa.attach_business_result(
        result, attempt_number=1, request_kind="MICRO"
    )

    assert result["checks"]["temporal_stability"] == "FAIL"
    assert result["motion_business_contract"]["check_authority"]["temporal_stability"] == "QUALITY_ADVISORY"
    assert result["business_signals"]["motion_temporal_integrity"] == "FAIL"
    assert result["business_qa"]["integrity_status"] == "FAIL"
    assert result["business_qa"]["retry_action"] == "REGENERATE"


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


def _structural_contract(*, expected_profile: dict[str, str], min_support: int = 2):
    return {
        "motion_class": "LOCOMOTION",
        "video_compat": {},
        "pet_motion_profile": expected_profile,
        "requirements": {
            "morphology": {"confidence_floor": "medium"},
            "qa": {
                "structural_anatomy": {
                    "required_checks": ["vlm_anatomy", "structural_morphology_consistency"],
                    "morphology_consistency": {
                        "check": "structural_morphology_consistency",
                        "compare_fields": [
                            "body_length_class",
                            "leg_length_class",
                            "head_proportion_class",
                            "muzzle_proportion_class",
                            "ear_form",
                            "tail_form",
                        ],
                        "minimum_support_frames": min_support,
                        "strong_contradiction_ratio": 0.7,
                        "pose_dependent_fields": [
                            "body_length_class",
                            "leg_length_class",
                            "body_build_class",
                        ],
                        "pose_dependent_policy": "review_never_fail",
                    },
                },
                "identity": {"required_checks": ["identity_over_time", "vlm_same_pet"]},
                "motion_specific": {
                    "required_checks": ["vlm_motion", "temporal_stability", "vlm_composition"]
                },
            },
        },
    }


def _obs(*, body="LONG", leg="LONG", head="STANDARD", muzzle="STANDARD", head_vis=True,
         tail_vis=True, limb_bad=False, joint_bad=False, joint_samples=3, aspect=1.7):
    """limb_bad=None 은 '그 프레임에서 사지를 잴 수 없었다'를 뜻한다(붕괴가 아니다)."""
    return {
        "measurable": True,
        "morphology": {
            "body_length_class": body,
            "leg_length_class": leg,
            "head_proportion_class": head,
            "muzzle_proportion_class": muzzle,
            "ear_form": "ERECT",
            "tail_form": "CURLED",
            "body_build_class": "BALANCED",
            "body_size_class": "MEDIUM",
        },
        "visibility": {"head_visible": head_vis, "tail_visible": tail_vis},
        "silhouette": {"bbox_aspect_ratio": aspect, "area_fraction": 0.2},
        "anatomy": {
            "joint_samples": int(joint_samples),
            "joint_implausible": bool(joint_bad),
            "limb_count_contradiction": None if limb_bad is None else bool(limb_bad),
            "limb_points_visible": 0 if limb_bad is None else 4,
        },
    }


def test_qa_structural_domain_separated_and_passes_when_consistent(monkeypatch):
    contract = _structural_contract(
        expected_profile={
            "body_length_class": "LONG",
            "leg_length_class": "LONG",
            "head_proportion_class": "STANDARD",
            "muzzle_proportion_class": "STANDARD",
            "ear_form": "ERECT",
            "tail_form": "CURLED",
            "sources": {
                "body_length": "morphology_profile:high",
                "leg_length": "morphology_profile:high",
                "head_proportion": "morphology_profile:high",
                "muzzle_proportion": "morphology_profile:high",
                "ear_form": "morphology_profile:high",
                "tail_form": "morphology_profile:high",
            },
        }
    )
    queue = [_obs() for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    frames = [_approach_frame(s) for s in _APPROACH_SIZES[:5]]
    r = qa_mod.evaluate_motion_video(
        frames=frames,
        spec_contract=contract,
        start_keyframe_rgb=_approach_frame(20),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["structural_morphology_consistency"] == "PASS"
    assert r["domains"]["identity"]["status"] == "PASS"
    assert r["domains"]["structural_anatomy"]["status"] == "PASS"
    assert r["domains"]["motion_execution"]["status"] == "PASS"
    assert r["decision"] == "PASS"


def test_qa_structural_missing_evidence_is_review_unknown_not_fail(monkeypatch):
    contract = _structural_contract(
        expected_profile={
            "body_length_class": "LONG",
            "head_proportion_class": "STANDARD",
            "ear_form": "ERECT",
            "sources": {
                "body_length": "morphology_profile:high",
                "head_proportion": "morphology_profile:high",
                "ear_form": "morphology_profile:high",
            },
        },
        min_support=3,
    )
    queue = [
        {"measurable": False, "reason": "foreground_unmeasurable"},
        _obs(head_vis=False, tail_vis=False),
        _obs(head_vis=False, tail_vis=False),
        {"measurable": False, "reason": "foreground_unmeasurable"},
        _obs(head_vis=False, tail_vis=False),
    ]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    frames = [_good_frame()] * 5
    r = qa_mod.evaluate_motion_video(
        frames=frames,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert r["checks"]["anatomy_limb_count_placement"] == "PASS"
    assert r["checks"]["anatomy_joint_plausibility"] == "PASS"
    assert r["decision"] == "REVIEW"
    assert any("insufficient_visibility" in reason for reason in r["reasons"])


def test_qa_non_pose_structural_contradiction_still_fails(monkeypatch):
    contract = _structural_contract(
        expected_profile={
            "body_length_class": "LONG",
            "leg_length_class": "LONG",
            "head_proportion_class": "LARGE",
            "sources": {
                "body_length": "morphology_profile:high",
                "leg_length": "morphology_profile:high",
                "head_proportion": "morphology_profile:high",
            },
        }
    )
    queue = [
        _obs(body="COMPACT", leg="SHORT", head="SMALL"),
        _obs(body="COMPACT", leg="SHORT", head="SMALL"),
        _obs(body="COMPACT", leg="SHORT", head="SMALL"),
        _obs(body="LONG", leg="LONG", head="LARGE"),
        _obs(body="COMPACT", leg="SHORT", head="SMALL"),
    ]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    frames = [_good_frame()] * 5
    r = qa_mod.evaluate_motion_video(
        frames=frames,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["structural_morphology_consistency"] == "FAIL"
    summary = r["domains"]["structural_anatomy"]["morphology_consistency"]["trait_summary"]
    assert summary["body_length_class"]["status"] == "REVIEW"
    assert summary["leg_length_class"]["status"] == "REVIEW"
    assert summary["head_proportion_class"]["status"] == "FAIL"
    assert r["domains"]["structural_anatomy"]["status"] == "FAIL"
    assert r["decision"] == "FAIL"


def _transition_structural_contract(expected_profile: dict[str, str]):
    contract = _structural_contract(expected_profile=expected_profile)
    contract["motion_class"] = "TRANSITION"
    morph_req = contract["requirements"]["qa"]["structural_anatomy"]["morphology_consistency"]
    morph_req["pose_dependent_fields"] = [
        "body_length_class",
        "leg_length_class",
        "body_build_class",
    ]
    morph_req["pose_dependent_policy"] = "review_never_fail"
    contract["requirements"]["qa"]["structural_anatomy"]["required_checks"] = [
        "starts_at_start_pose",
        "reaches_target_pose",
        "vlm_anatomy",
        "structural_morphology_consistency",
    ]
    contract["requirements"]["qa"]["motion_specific"]["required_checks"] = [
        "vlm_motion",
        "vlm_target_pose",
        "temporal_stability",
        "vlm_composition",
    ]
    return contract


def test_qa_transition_pose_change_is_pass_with_advisory(monkeypatch):
    """
    앉기/서기/눕기 전환에서는 몸통 비율·다리 길이가 자세와 함께 바뀌는 게
    정상이다 — 요청한 동작 자체를 구조 붕괴로 하드 FAIL 하지 않는다.
    """
    expected = {
        "body_length_class": "LONG",
        "leg_length_class": "LONG",
        "sources": {
            "body_length": "morphology_profile:high",
            "leg_length": "morphology_profile:high",
        },
    }
    queue = [_obs(body="COMPACT", leg="SHORT") for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    g = _good_frame()
    r = qa_mod.evaluate_motion_video(
        frames=[g] * 5,
        spec_contract=_transition_structural_contract(expected),
        start_keyframe_rgb=g,
        target_keyframe_rgb=g,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert r["domains"]["structural_anatomy"]["status"] == "PASS"
    assert r["decision"] == "PASS"
    assert "structural_morphology_consistency" in r["advisories"]["checks"]
    assert any("pose_dependent_change_advisory" in reason for reason in r["reasons"])

    # LOCOMOTION 도 bbox/실루엣 기반 pose-dependent 축만으로 하드 FAIL 하지 않는다.
    queue2 = [_obs(body="COMPACT", leg="SHORT") for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue2.pop(0))
    loco = qa_mod.evaluate_motion_video(
        frames=[g] * 5,
        spec_contract=_structural_contract(expected_profile=expected),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert loco["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert loco["decision"] == "PASS"


def _registry_qa_contract(motion_id: str, expected_profile: dict[str, object]):
    return {
        **ms.motion_snapshot(ms.MOTIONS[motion_id]),
        "pet_motion_profile": expected_profile,
    }


def _compact_profile():
    return {
        "body_length_class": "COMPACT",
        "sources": {"body_length": "morphology_profile:high"},
    }


def _seven_of_nine_body_mismatches():
    return [
        *[_obs(body="LONG") for _ in range(7)],
        *[_obs(body="COMPACT") for _ in range(2)],
    ]


def test_qa_pet_head_body_length_contradiction_passes_with_advisory(monkeypatch):
    queue = _seven_of_nine_body_mismatches()
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=_registry_qa_contract("PET_HEAD", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )

    summary = result["domains"]["structural_anatomy"]["morphology_consistency"][
        "trait_summary"
    ]
    assert summary["body_length_class"]["mismatch_frames"] == 7
    assert summary["body_length_class"]["status"] == "REVIEW"
    assert result["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert result["domains"]["structural_anatomy"]["status"] == "PASS"
    assert result["decision"] == "PASS"
    assert result["advisories"]["checks"] == ["structural_morphology_consistency"]
    assert result["advisories"]["findings"][0]["trait"] == "body_length_class"
    assert any(
        reason.startswith("advisory_checks_not_blocking:")
        for reason in result["reasons"]
    )


def test_qa_kling_one_of_nine_body_length_drift_passes_with_advisory(monkeypatch):
    queue = [_obs(body="LONG"), *[_obs(body="COMPACT") for _ in range(8)]]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=_registry_qa_contract("PET_HEAD", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )

    summary = result["domains"]["structural_anatomy"]["morphology_consistency"][
        "trait_summary"
    ]["body_length_class"]
    assert summary["mismatch_frames"] == 1
    assert summary["status"] == "REVIEW"
    assert result["decision"] == "PASS"
    assert result["advisories"]["findings"][0]["reason"] == "pose_dependent_drift"


def test_qa_pet_head_pose_deformation_passes_with_advisory(monkeypatch):
    aspects = [1.0, 2.2, 1.0, 2.2, 1.0]
    queue = [_obs(body="COMPACT", aspect=aspect) for aspect in aspects]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * len(aspects),
        spec_contract=_registry_qa_contract("PET_HEAD", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )

    assert result["checks"]["structural_morphology_consistency"] == "PASS"
    assert result["checks"]["anatomy_body_deformation"] == "REVIEW"
    assert result["decision"] == "PASS"
    assert "anatomy_body_deformation" in result["advisories"]["checks"]
    assert any(
        finding["reason"] == "pose_dependent_deformation"
        for finding in result["advisories"]["findings"]
    )


def test_qa_look_up_body_length_profile_cannot_hard_fail(monkeypatch):
    # MICRO does not require structural_morphology_consistency. The shared
    # registry policy is still advisory if that check is enabled in the future.
    queue = _seven_of_nine_body_mismatches()
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()
    contract = _registry_qa_contract("LOOK_UP", _compact_profile())

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=contract,
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )

    policy = contract["requirements"]["qa"]["structural_anatomy"][
        "morphology_consistency"
    ]["pose_dependent_policy"]
    assert policy == "review_never_fail"
    assert "structural_morphology_consistency" not in result["checks"]
    assert result["decision"] == "PASS"


def test_qa_locomotion_pose_dependent_contradiction_passes_with_advisory(monkeypatch):
    queue = _seven_of_nine_body_mismatches()
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=_registry_qa_contract("COME_CLOSER", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )

    assert result["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert result["decision"] == "PASS"


def test_qa_pose_advisory_does_not_mask_vlm_anatomy_failure(monkeypatch):
    queue = _seven_of_nine_body_mismatches()
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=_registry_qa_contract("PET_HEAD", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa={**VLM_MV_OK, "anatomy_plausible_all_frames": "no"},
    )

    assert result["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert result["checks"]["vlm_anatomy"] == "FAIL"
    assert result["decision"] == "FAIL"


def test_qa_pose_advisory_does_not_mask_identity_failure(monkeypatch):
    queue = _seven_of_nine_body_mismatches()
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))
    g = _good_frame()

    result = qa_mod.evaluate_motion_video(
        frames=[g] * 9,
        spec_contract=_registry_qa_contract("PET_HEAD", _compact_profile()),
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa={**VLM_MV_OK, "same_pet_all_frames": "no"},
    )

    assert result["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert result["checks"]["vlm_same_pet"] == "FAIL"
    assert result["decision"] == "FAIL"


def test_qa_real_limb_and_joint_corruption_fails_even_when_not_required(monkeypatch):
    """
    advisory 는 REVIEW 에만 적용된다. 실제 측정된 사지 FAIL 은 required 목록에
    명시되지 않았어도 후보를 차단한다.
    """
    expected = {
        "body_length_class": "LONG",
        "sources": {"body_length": "morphology_profile:high"},
    }
    queue = [_obs(limb_bad=True, joint_bad=True) for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=_structural_contract(expected_profile=expected),
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["anatomy_limb_count_placement"] == "FAIL"
    assert r["checks"]["anatomy_joint_plausibility"] == "FAIL"
    assert r["checks"]["structural_morphology_consistency"] == "PASS"
    assert r["domains"]["structural_anatomy"]["status"] == "FAIL"
    assert r["decision"] == "FAIL"
    assert not any("advisory_structural_fail_not_blocking" in reason for reason in r["reasons"])

    queue2 = [_obs(limb_bad=True, joint_bad=True) for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue2.pop(0))
    hard = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=_structural_contract(expected_profile=expected),
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa={**VLM_MV_OK, "anatomy_plausible_all_frames": "no"},
    )
    assert hard["decision"] == "FAIL"


def test_qa_frame_occupancy_is_not_a_structural_comparison_axis(monkeypatch):
    """레거시 계약이 body_size_class 를 남겨 두어도 비교 축이 되지 않는다."""
    contract = _structural_contract(
        expected_profile={
            "body_size_class": "SMALL",
            "body_length_class": "LONG",
            "sources": {
                "body_size": "morphology_profile:high",
                "body_length": "morphology_profile:high",
            },
        }
    )
    contract["requirements"]["qa"]["structural_anatomy"]["morphology_consistency"][
        "compare_fields"
    ] = ["body_size_class", "body_length_class"]
    queue = [_obs() for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    summary = r["domains"]["structural_anatomy"]["morphology_consistency"]["trait_summary"]
    assert "body_size_class" not in summary
    assert "body_length_class" in summary


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
    assert version.status == mv.STATUS_COMPLETE
    assert version.candidates[0].decision == "REVIEW"
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


def test_motion_contract_version_bump_requalifies_without_regeneration(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()])
    version = _build_motion(h, "BREATHING", [provider])
    candidate = version.candidates[0]
    stored_qa = dict(candidate.qa_result)
    stored_contract = dict(stored_qa["motion_business_contract"])
    stored_contract["version"] = "motion-qa-contract-old"
    stored_qa["motion_business_contract"] = stored_contract
    _run(
        canon._update(
            mv._candidates_table(),
            mv._MOCK_CANDIDATES,
            candidate.id,
            {"qa_result": stored_qa},
        )
    )

    calls = {"vlm": 0}

    def current_vlm(*args, **kwargs):
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
            vlm_qa_fn=current_vlm,
            conformance_fn=conformance_ok,
        )
    )

    assert rerun.deduplicated is False
    assert calls["vlm"] == 1
    assert provider.calls == 1
    refreshed = next(c for c in rerun.candidates if c.id == candidate.id)
    assert refreshed.qa_result["motion_business_contract"]["version"] == ms.MOTION_QA_CONTRACT_VERSION


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
        vp, "resolve_provider_order",
        lambda order: [FakeVideoProvider("seedance", [GOOD()] * 3)],
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


# ══════════════════════════════════════════════════════════════════════════
# LOCOMOTION 신원 (motion-video-qa-v4) — 펫 크롭 정규화 + 평균/일관성/VLM 판정
# ══════════════════════════════════════════════════════════════════════════
#
# 배경(COME_CLOSER 라이브 REVIEW, worst_frame 0.498): 전체 프레임 히스토그램은
# 다가오기가 성공할수록 시작 키프레임과 멀어진다 — 모션의 성공이 신원 점수를
# 낮추는 구조적 편향. v4 는 LOCOMOTION 의 신원 검사만 펫 크롭 정규화 시그니처와
# 평균+인접 일관성+VLM same-pet 증거로 판정한다. 임계값과 다른 클래스는 불변.


def _approach_frame(pet_h: int, *, swap_color: bool = False,
                    h: int = 160, w: int = 96) -> np.ndarray:
    """세로 그라디언트 중립 배경 + '펫'(위 60% 갈색 / 아래 40% 검정).

    크기가 달라도 색 비율이 같아 정규화 시그니처는 안정적이고, 전체 프레임
    히스토그램은 펫이 커질수록 시작 키프레임과 멀어진다 — 실측 편향의 재현.
    swap_color 는 다른 개체(흰/빨강)를 그린다 — 정규화가 진짜 교체를 가리면
    안 된다는 반증용.
    """
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = np.linspace(118, 140, h).astype(np.uint8)[:, None, None]
    pet_w = min(w - 16, max(8, int(pet_h * 0.6)))
    y0 = max(4, h - 10 - pet_h)
    x0 = (w - pet_w) // 2
    body = np.zeros((min(pet_h, h - y0 - 4), pet_w, 3), dtype=np.uint8)
    top = int(body.shape[0] * 0.6)
    body[:top] = (240, 240, 245) if swap_color else (140, 90, 45)
    body[top:] = (200, 60, 60) if swap_color else (25, 20, 18)
    frame[y0 : y0 + body.shape[0], x0 : x0 + pet_w] = body
    return frame


_LOCO_CONTRACT = {"motion_class": "LOCOMOTION", "video_compat": {}}
_APPROACH_SIZES = (20, 30, 45, 65, 90, 120)


def _eval_loco(frames, *, vlm=VLM_MV_OK, contract=_LOCO_CONTRACT):
    return qa_mod.evaluate_motion_video(
        frames=frames,
        spec_contract=contract,
        start_keyframe_rgb=_approach_frame(20),
        target_keyframe_rgb=None,
        vlm_qa=vlm,
    )


def test_qa_locomotion_close_approach_passes_with_normalized_identity():
    r = _eval_loco([_approach_frame(s) for s in _APPROACH_SIZES])
    assert r["identity_evaluation"]["mode"] == "pet_normalized"
    assert r["identity_evaluation"]["rule"] == "locomotion_mean_consistency"
    assert r["checks"]["identity_over_time"] == "PASS"
    assert r["decision"] == "PASS"
    assert r["qa_version"] == qa_mod.MOTION_VIDEO_QA_VERSION


def test_locomotion_contract_checks_gait_and_direction_as_hard_integrity():
    contract = ms.motion_snapshot(ms.MOTIONS["RUN"])
    result = _eval_loco(
        [_approach_frame(s) for s in _APPROACH_SIZES],
        contract=contract,
        vlm={**VLM_MV_OK, "direction_travel_correct": "no"},
    )
    business_qa.attach_business_result(
        result, attempt_number=1, request_kind="LOCOMOTION"
    )

    assert result["checks"]["vlm_locomotion_form"] == "PASS"
    assert result["checks"]["vlm_direction_travel"] == "FAIL"
    assert result["business_domains"]["direction_travel"]["status"] == "FAIL"
    assert result["business_qa"]["retry_action"] == "REGENERATE"


def test_interaction_contract_enforces_registry_human_hand_policy():
    contract = ms.motion_snapshot(ms.MOTIONS["PET_HEAD"])
    result = _eval_loco(
        [_approach_frame(s) for s in _APPROACH_SIZES],
        contract=contract,
        vlm={**VLM_MV_OK, "human_present": "yes", "human_hand_policy_ok": "no"},
    )
    business_qa.attach_business_result(
        result, attempt_number=1, request_kind="INTERACTION"
    )

    assert contract["video_compat"]["allow_generated_hand"] is True
    assert result["checks"]["vlm_human_hand_policy"] == "FAIL"
    assert result["business_domains"]["human_hand_policy"]["status"] == "FAIL"
    assert result["business_qa"]["retry_action"] == "REGENERATE"


def test_qa_micro_uses_normalized_signature_but_keeps_worst_frame_rule():
    """v5 — MICRO 도 **시그니처**는 펫 크롭 정규화다. 바뀐 것은 무엇으로 재는가
    뿐이고, 판정 **규칙**은 여전히 worst_frame 이다 (평균/인접 일관성 규칙은
    LOCOMOTION 전용). 라이브 실측 근거: BREATHING 은 펫이 거의 안 움직이는데
    전체 프레임 히스토그램이 평평한 배경의 양자화 이동에 지배됐다."""
    r = _eval_loco(
        [_approach_frame(s) for s in _APPROACH_SIZES],
        contract={"motion_class": "MICRO", "video_compat": {}},
    )
    assert r["identity_evaluation"]["mode"] == "pet_normalized"
    assert r["identity_evaluation"]["rule"] == "worst_frame"
    # LOCOMOTION 전용 규칙의 증거(평균/인접)는 MICRO 에 달리지 않는다.
    assert "adjacent_min" not in r["identity_evaluation"]

    # 규칙이 worst_frame 이라는 증명 — 한 프레임만 다른 개체여도 PASS 가 아니다.
    # (LOCOMOTION 의 평균 규칙이었다면 크레이터 한 장은 평균에 묻힐 수 있다.)
    swapped = [_approach_frame(s) for s in _APPROACH_SIZES[:-1]] + [
        _approach_frame(_APPROACH_SIZES[-1], swap_color=True)
    ]
    r2 = _eval_loco(swapped, contract={"motion_class": "MICRO", "video_compat": {}})
    assert r2["checks"]["identity_over_time"] != "PASS"
    assert any("worst_frame" in reason for reason in r2["reasons"])


def test_qa_interaction_uses_normalized_signature_keeps_worst_frame_rule():
    """v6 — INTERACTION 도 정규화 시그니처다. 규칙은 worst_frame 그대로.

    라이브 실측 근거(PET_HEAD/wan): 같은 프레임이 전체 프레임 0.33~0.71 vs 펫
    정규화 0.845~0.944 였다 — 스펙이 허용한 손(allow_generated_hand)의 피부
    픽셀이 신원 점수를 깎는 구조였다. 허용된 연출이 신원을 깎으면 안 된다.
    """
    contract = {
        "motion_class": "INTERACTION",
        "video_compat": {"returns_to_start_pose": True, "allow_generated_hand": True},
    }
    r = _eval_loco([_approach_frame(s) for s in _APPROACH_SIZES], contract=contract)
    assert r["identity_evaluation"]["mode"] == "pet_normalized"
    assert r["identity_evaluation"]["rule"] == "worst_frame"
    # LOCOMOTION 전용 평균/인접 규칙은 INTERACTION 에 적용되지 않는다.
    assert "adjacent_min" not in r["identity_evaluation"]


def test_qa_normalization_never_drops_unsegmentable_frames_fail_open():
    """정규화 불가 프레임이 섞이면 전체 프레임 방식으로 폴백한다 — 부분 측정 금지.

    회귀 근거: 정규화를 MICRO 로 넓혔을 때, 전경 분할이 실패하는 화이트아웃
    프레임이 sims 에서 조용히 빠져 5 장 중 3 장이 화이트아웃인 클립이 신원
    PASS 를 받았다. 신원이 사라진 프레임이야말로 분할이 실패하는 프레임이므로
    부분 측정은 곧 fail-open 이다.
    """
    frames = [_good_frame(), _good_frame(), white_frame(), white_frame(), white_frame()]
    r = _eval(frames)  # MICRO 계약
    ev = r["identity_evaluation"]
    assert ev["normalized_frames"] < ev["decoded_frames"]
    assert ev["mode"] == "full_frame"
    assert r["checks"]["identity_over_time"] == "FAIL"


def test_qa_locomotion_identity_swap_is_not_masked_by_normalization():
    # 마지막 두 프레임이 다른 개체다 — 평균은 살아도 크레이터 가드가 잡는다.
    frames = [_approach_frame(s) for s in (20, 30, 45, 65)] + [
        _approach_frame(90, swap_color=True),
        _approach_frame(120, swap_color=True),
    ]
    r = _eval_loco(frames)
    assert r["checks"]["identity_over_time"] != "PASS"
    assert any("identity_crater_frame" in reason or "identity_mean" in reason
               for reason in r["reasons"])

    # 전부 다른 개체면 평균 자체가 무너진다 → FAIL (fail-closed 유지).
    all_swapped = [_approach_frame(s, swap_color=True) for s in _APPROACH_SIZES]
    r2 = _eval_loco(all_swapped)
    assert r2["checks"]["identity_over_time"] == "FAIL"
    assert r2["decision"] == "FAIL"


def _fake_sig(bins: dict[int, float]):
    from backend.services import pet_identity_service as ids

    hist = [0.0] * 64
    for idx, v in bins.items():
        hist[idx] = v
    return {"version": ids.SIGNATURE_VERSION, "hsv_hist": hist, "phash": "0" * 16}


def _install_normalized_sigs(monkeypatch, start_sig, frame_sigs):
    queue = [start_sig, *frame_sigs]
    monkeypatch.setattr(qa_mod, "_pet_normalized_signature", lambda rgb: queue.pop(0))


def test_qa_locomotion_borderline_needs_vlm_and_consistency(monkeypatch):
    """경계 구간(0.20..0.55) 평균의 PASS 승격은 VLM same-pet + 인접 일관성이
    **둘 다** 있어야 한다. 하나라도 빠지면 REVIEW — fail-closed."""
    frames = [_good_frame()] * 5  # 시간 안정성은 실제 전체 프레임으로 PASS

    start = _fake_sig({0: 1.0})
    borderline = _fake_sig({0: 0.4, 1: 0.6})  # 시작 대비 0.4, 서로는 1.0

    _install_normalized_sigs(monkeypatch, start, [borderline] * 5)
    ok = _eval_loco(frames)
    assert ok["identity_evaluation"]["mode"] == "pet_normalized"
    assert ok["checks"]["identity_over_time"] == "PASS"
    assert any("locomotion_identity_resolved" in reason for reason in ok["reasons"])

    # VLM 확언 없음 → REVIEW.
    _install_normalized_sigs(monkeypatch, start, [borderline] * 5)
    no_vlm = _eval_loco(frames, vlm={**VLM_MV_OK, "same_pet_all_frames": "unknown"})
    assert no_vlm["checks"]["identity_over_time"] == "REVIEW"

    # 인접 일관성 없음(교대 시그니처, 서로 0.4) → VLM 이 yes 여도 REVIEW.
    jitter_a = _fake_sig({0: 0.4, 2: 0.6})
    jitter_b = _fake_sig({0: 0.4, 3: 0.6})
    _install_normalized_sigs(monkeypatch, start, [jitter_a, jitter_b, jitter_a, jitter_b, jitter_a])
    jitter = _eval_loco(frames)
    assert jitter["checks"]["identity_over_time"] == "REVIEW"


def test_qa_locomotion_falls_back_to_full_frame_when_unmeasurable(monkeypatch):
    """정규화 불가(배경 모델 실패 등)면 전체 프레임 측정으로 폴백한다 — 측정이
    없다고 검사가 사라지지 않는다."""
    monkeypatch.setattr(qa_mod, "_pet_normalized_signature", lambda rgb: None)
    g = _good_frame()
    r = qa_mod.evaluate_motion_video(
        frames=[g] * 5,
        spec_contract=_LOCO_CONTRACT,
        start_keyframe_rgb=g,
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["identity_evaluation"]["mode"] == "full_frame"
    assert r["checks"]["identity_over_time"] == "PASS"  # 동일 프레임 — 평균 1.0


# ── 측정 불가한 해부학 증거는 FAIL 근거가 아니다 ───────────────────────────


def _plain_expected():
    return {
        "body_length_class": "LONG",
        "sources": {"body_length": "morphology_profile:high"},
    }


def test_frame_without_pose_keypoints_makes_no_limb_claim():
    """
    루트 원인 회귀 가드: 포즈 백엔드가 키포인트를 하나도 못 낸 프레임은
    '다리가 없다'가 아니라 '잴 수 없었다' 다 — 실제 관측 함수로 확인한다.
    """
    obs = qa_mod._frame_structural_observation(_good_frame())
    assert obs["measurable"] is True
    vis = obs["visibility"]
    assert vis["visible_paw_points"] == 0 and vis["visible_prox_points"] == 0
    anatomy = obs["anatomy"]
    assert anatomy["limb_points_visible"] == 0
    assert anatomy["limb_count_contradiction"] is None


def test_qa_unmeasurable_limb_evidence_never_fails_and_leaves_no_reason(monkeypatch):
    """1) 사지를 잴 수 없으면 FAIL 도, 부정적 사유도 남기지 않는다."""
    queue = [_obs(limb_bad=None) for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=_structural_contract(expected_profile=_plain_expected()),
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    # required 가 아닌 신호는 아예 실리지 않는다 — UNKNOWN 으로도 PASS 를 막지 않는다.
    assert "anatomy_limb_count_placement" not in r["checks"]
    assert not any("limb" in reason for reason in r["reasons"])
    assert r["checks"]["structural_morphology_consistency"] == "PASS"
    assert r["decision"] == "PASS"
    signals = r["domains"]["structural_anatomy"]["morphology_consistency"]["anatomy_signals"]
    assert signals["limb_evidence_frames"] == 0
    assert signals["limb_count_contradictions"] == 0
    assert "anatomy_limb_count_placement" in signals["unmeasurable_signals"]


def test_qa_unmeasurable_limb_evidence_is_unknown_when_registry_requires_it(monkeypatch):
    """계약이 그 신호를 required 로 요구하면 fail-closed 로 UNKNOWN — 그래도 FAIL 은 아니다."""
    contract = _structural_contract(expected_profile=_plain_expected())
    contract["requirements"]["qa"]["structural_anatomy"]["required_checks"] = [
        "vlm_anatomy",
        "structural_morphology_consistency",
        "anatomy_limb_count_placement",
    ]
    queue = [_obs(limb_bad=None) for _ in range(5)]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["anatomy_limb_count_placement"] == "unknown"
    assert r["decision"] == "REVIEW"
    assert not any("limb" in reason for reason in r["reasons"])


def test_qa_insufficient_support_frames_stay_neutral(monkeypatch):
    """2) 근거 프레임이 min_support 에 못 미치면 사지/관절 모두 중립이다."""
    contract = _structural_contract(expected_profile=_plain_expected(), min_support=3)
    # 5장 중 2장만 사지/관절 증거가 있다 — 그 2장이 붕괴를 가리켜도 주장하지 않는다.
    queue = [
        _obs(limb_bad=True, joint_bad=True, joint_samples=3),
        _obs(limb_bad=True, joint_bad=True, joint_samples=3),
        _obs(limb_bad=None, joint_samples=0),
        _obs(limb_bad=None, joint_samples=0),
        _obs(limb_bad=None, joint_samples=0),
    ]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert "anatomy_limb_count_placement" not in r["checks"]
    assert "anatomy_joint_plausibility" not in r["checks"]
    assert r["decision"] != "FAIL"
    assert not any("limb" in reason or "joint" in reason for reason in r["reasons"])
    signals = r["domains"]["structural_anatomy"]["morphology_consistency"]["anatomy_signals"]
    assert signals["limb_evidence_frames"] == 2
    assert signals["joint_evidence_frames"] == 2


def test_qa_supported_limb_and_joint_corruption_still_fails(monkeypatch):
    """3) 실제로 측정된 붕괴 근거가 min_support 만큼 모이면 종전대로 FAIL 이다."""
    contract = _structural_contract(expected_profile=_plain_expected(), min_support=2)
    contract["requirements"]["qa"]["structural_anatomy"]["required_checks"] = [
        "vlm_anatomy",
        "structural_morphology_consistency",
        "anatomy_limb_count_placement",
        "anatomy_joint_plausibility",
    ]
    queue = [
        _obs(limb_bad=True, joint_bad=True),
        _obs(limb_bad=True, joint_bad=True),
        _obs(limb_bad=True, joint_bad=True),
        _obs(limb_bad=None, joint_samples=0),
        _obs(limb_bad=False),
    ]
    monkeypatch.setattr(qa_mod, "_frame_structural_observation", lambda _f: queue.pop(0))

    r = qa_mod.evaluate_motion_video(
        frames=[_good_frame()] * 5,
        spec_contract=contract,
        start_keyframe_rgb=_good_frame(),
        target_keyframe_rgb=None,
        vlm_qa=VLM_MV_OK,
    )
    assert r["checks"]["anatomy_limb_count_placement"] == "FAIL"
    assert r["checks"]["anatomy_joint_plausibility"] == "FAIL"
    assert "anatomy_limb_count_or_placement_corrupted" in r["reasons"]
    assert "anatomy_joint_implausible" in r["reasons"]
    assert r["domains"]["structural_anatomy"]["status"] == "FAIL"
    assert r["decision"] == "FAIL"
    # 측정 불가 프레임은 분모에 들어가지 않는다 — 근거 프레임만 센다.
    signals = r["domains"]["structural_anatomy"]["morphology_consistency"]["anatomy_signals"]
    assert signals["limb_evidence_frames"] == 4
    assert signals["limb_count_contradictions"] == 3
