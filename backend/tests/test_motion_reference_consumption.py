"""
Phase 6.7 — 모션 레퍼런스 **실소비** 테스트.

계약: I2V_MOTION_REF 는 레퍼런스 비디오가 실제 프로바이더 입력으로 들어갈 때만
유지된다. 소비 불가면 I2V 로 강등 + 경고. 가짜(메타데이터만) 지원 금지.
"""

from __future__ import annotations

import pytest

from backend.services import action_keyframe_service as kf
from backend.services import canonical_pet_service as canon
from backend.services import motion_reference_service as mrs
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry
from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    MotionVideoRequest,
    MotionVideoResult,
    RunwayWanMotionRefProvider,
    VideoProviderError,
)

from .test_motion_video_generation import (
    VLM_MV_OK,
    FakeVideoProvider,
    _prepare_pipeline,
    _run,
    conformance_ok,
    sampler_identical,
)
from .test_canonical_pet_builder import GOOD
from .test_pet_reference_sets import PET, USER

SPEC = {"aspect_ratio": "9:16", "resolution": "480p", "duration_sec": 4, "audio": False, "camera_fixed": True}

REF_PAYLOAD = {
    "reference_key": "DOG_MEDIUM_RUN_FRONT",
    "version": 1,
    "motion_id": "RUN",
    "asset": {
        "bucket": "user-assets",
        "object_path": "motion-references/dog/medium_standard/run/front/dog_medium_run_front_v1.mp4",
    },
    "duration_sec": 5.0,
    "quality": "APPROVED",
    "selection_level": "LEVEL_3",
    "compatibility": {"level": "LEVEL_3"},
}


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    monkeypatch.setenv("PHASE6_VIDEO_ANCHOR", "0")
    monkeypatch.delenv("VIDEO_GENERATION_MOCK", raising=False)
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    for m in (refs, pet_registry, ids, sets, canon, kf, mv, mrs):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, sets, canon, kf, mv, mrs):
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


def _install_resolved_reference(monkeypatch, payload=REF_PAYLOAD):
    async def fake_resolve(**kwargs):
        return dict(payload) if payload else None

    monkeypatch.setattr(mrs, "resolve_motion_reference", fake_resolve)


class RefCapableFake(FakeVideoProvider):
    supports_motion_reference = True


def _sign(obj):
    return f"https://signed.test/{obj.object_path}"


def _build_run(h, providers):
    return _run(
        mv.build_motion_video(
            user_id=USER, pet_id=PET, motion_id="RUN",
            fetch_bytes=h.kf_fetch, providers=providers,
            frame_sampler=sampler_identical, conformance_fn=conformance_ok,
            sign_url_fn=_sign,
        )
    )


# ── wan3 어댑터 계약 ────────────────────────────────────────────────────────


def test_wan_payload_contains_actual_reference_video():
    req = MotionVideoRequest(
        prompt="p", start_image_url="https://x/start.png", start_image_bytes=b"x",
        output_spec=SPEC, motion_reference_url="https://x/ref.mp4",
    )
    payload = RunwayWanMotionRefProvider().build_payload(req)
    assert payload == {
        "model": "wan3",
        # 라이브 검증: 레퍼런스와 결합 시 position 없는 배열 (bare 문자열은
        # first-frame 키프레임 모드로 해석돼 Runway 400).
        "promptImage": [{"uri": "https://x/start.png"}],
        "promptText": "p",
        "ratio": "480:832",
        "duration": 4,
        "audio": False,
        "referenceVideos": [{"type": "video", "uri": "https://x/ref.mp4"}],
    }


def test_wan_refuses_call_without_reference():
    """레퍼런스 조건부 프로바이더 — 레퍼런스 없이 호출하면 로컬 계약 위반."""
    req = MotionVideoRequest(
        prompt="p", start_image_url="https://x/start.png", start_image_bytes=b"x", output_spec=SPEC
    )
    with pytest.raises(VideoProviderError) as e:
        RunwayWanMotionRefProvider().build_payload(req)
    assert e.value.code == "PROVIDER_CONTRACT"


def test_wan_generate_submits_reference_to_runway(monkeypatch):
    import httpx

    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    submitted: dict = {}

    def fake_post(url, **k):
        submitted["url"] = url
        submitted["json"] = k.get("json")
        return type("R", (), {"status_code": 200, "json": lambda self: {"id": "22222222-3333-4444-8555-666666666666"}, "text": ""})()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **k: type("R", (), {
            "status_code": 200, "text": "",
            "json": lambda self: {"id": "t", "status": "SUCCEEDED", "output": ["https://cdn/v.mp4"], "cost": {"credits": 10}},
        })(),
    )
    monkeypatch.setattr(vp, "_download", lambda url: b"MP4")
    result = RunwayWanMotionRefProvider().generate(
        MotionVideoRequest(
            prompt="p", start_image_url="https://x/s.png", start_image_bytes=b"x",
            output_spec=SPEC, motion_reference_url="https://signed.test/ref.mp4",
        )
    )
    assert result.video_bytes == b"MP4"
    assert submitted["url"].endswith("/v1/image_to_video")
    # 레퍼런스 비디오 URL 이 **실제 요청 페이로드**에 도달한다 (요구 7).
    assert submitted["json"]["referenceVideos"] == [
        {"type": "video", "uri": "https://signed.test/ref.mp4"}
    ]


def test_reference_capable_registry():
    assert vp.reference_capable_providers() == []  # RUNWAY_API_KEY 없음
    import os
    os.environ["RUNWAY_API_KEY"] = "rw-test-key"
    try:
        caps = vp.reference_capable_providers()
        assert len(caps) == 1 and isinstance(caps[0], RunwayWanMotionRefProvider)
        assert caps[0].supports_motion_reference is True
    finally:
        del os.environ["RUNWAY_API_KEY"]


# ── 빌더 통합 ───────────────────────────────────────────────────────────────


def test_reference_url_reaches_provider_request(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _install_resolved_reference(monkeypatch)
    provider = RefCapableFake("wan", [GOOD()])

    v = _build_run(h, [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO_WITH_MOTION_REF"

    req = provider.requests[0]
    assert req.motion_reference_url == (
        "https://signed.test/motion-references/dog/medium_standard/run/front/dog_medium_run_front_v1.mp4"
    )
    sel = next(c for c in v.candidates if c.selected)
    snap = sel.generation_metadata["motion_reference"]
    assert snap["consumed"] is True
    assert snap["sent_object_path"] == REF_PAYLOAD["asset"]["object_path"]
    assert snap["sent_version"] == 1
    assert any(
        r["kind"] == "motion_reference_video"
        and r["object_path"] == REF_PAYLOAD["asset"]["object_path"]
        for r in sel.input_references
    )


def test_degrades_when_no_reference_capable_provider(storage, monkeypatch):
    """소비 불가 → I2V 강등 + 경고. 레퍼런스 조건부로 표기하지 않는다 (요구 5/6)."""
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _install_resolved_reference(monkeypatch)
    provider = FakeVideoProvider("seedance", [GOOD()])  # supports_motion_reference=False

    v = _build_run(h, [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO"  # 강등된 실제 전략이 기록된다
    assert any("NOT consumed" in w for w in v.warnings)
    assert provider.requests[0].motion_reference_url is None
    sel = next(c for c in v.candidates if c.selected)
    assert sel.generation_metadata["motion_reference"]["consumed"] is False
    assert "sent_object_path" not in sel.generation_metadata["motion_reference"]
    assert not any(r["kind"] == "motion_reference_video" for r in sel.input_references)


def test_degrades_when_reference_exceeds_provider_limit(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _install_resolved_reference(monkeypatch, {**REF_PAYLOAD, "duration_sec": 20.0})
    provider = RefCapableFake("wan", [GOOD()])

    v = _build_run(h, [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO"
    assert any("exceeds provider limit" in w for w in v.warnings)
    assert provider.requests[0].motion_reference_url is None


def test_unresolved_reference_keeps_existing_degraded_path(storage, monkeypatch):
    """레퍼런스 미해석 (기존 Phase 6.6 동작) — 리졸버가 이미 I2V 로 강등한다."""
    h, _ = _prepare_pipeline(monkeypatch, storage)
    _install_resolved_reference(monkeypatch, payload=None)
    provider = FakeVideoProvider("seedance", [GOOD()])

    v = _build_run(h, [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO"
    assert any("unavailable" in w for w in v.warnings)
