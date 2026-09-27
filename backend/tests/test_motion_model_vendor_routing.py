"""Logical motion model → vendor → adapter routing contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services import (
    durable_provider_jobs as jobs,
    motion_spec as ms,
    motion_video_service as mvs,
    pet_generation_run_service as runs,
    video_motion_providers as vp,
)
from backend.services.video_motion_providers import MotionVideoRequest


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    for name in (
        "MOTION_VENDOR_SEEDANCE",
        "MOTION_VENDOR_KLING_3",
        "MOTION_VENDOR_WAN_3_STANDARD",
        "SEEDANCE_TRANSPORT",
        "KLING_TRANSPORT",
        "WAN_3_STANDARD_TRANSPORT",
        "PHASE6_VIDEO_TRANSPORT",
        "RUNWAY_API_KEY",
        "FAL_KEY",
        "FAL_API_KEY",
        "SEEDANCE_API_KEY",
        "ARK_API_KEY",
        "KLING_ACCESS_KEY",
        "KLING_SECRET_KEY",
        "VIDEO_GENERATION_MOCK",
    ):
        monkeypatch.delenv(name, raising=False)
    jobs.__reset_for_tests()
    yield
    jobs.__reset_for_tests()


@pytest.mark.parametrize(
    "logical_model,env_name,vendor,credential,adapter_class",
    [
        (
            "seedance",
            "MOTION_VENDOR_SEEDANCE",
            "runway",
            ("RUNWAY_API_KEY", "key"),
            vp.RunwaySeedanceProvider,
        ),
        (
            "seedance",
            "MOTION_VENDOR_SEEDANCE",
            "fal",
            ("FAL_KEY", "key"),
            vp.FalSeedanceProvider,
        ),
        (
            "seedance",
            "MOTION_VENDOR_SEEDANCE",
            "byteplus",
            ("SEEDANCE_API_KEY", "key"),
            vp.SeedanceProvider,
        ),
        (
            "kling_3",
            "MOTION_VENDOR_KLING_3",
            "fal",
            ("FAL_KEY", "key"),
            vp.FalKlingProvider,
        ),
        (
            "kling_3",
            "MOTION_VENDOR_KLING_3",
            "kling",
            ("KLING_ACCESS_KEY", "access"),
            vp.KlingProvider,
        ),
        (
            "wan_3_standard",
            "MOTION_VENDOR_WAN_3_STANDARD",
            "runway",
            ("RUNWAY_API_KEY", "key"),
            vp.RunwayWanStandardProvider,
        ),
        (
            "wan_3_standard",
            "MOTION_VENDOR_WAN_3_STANDARD",
            "fal",
            ("FAL_KEY", "key"),
            vp.FalWan3StandardProvider,
        ),
    ],
)
def test_logical_model_vendor_adapter_matrix(
    monkeypatch,
    logical_model,
    env_name,
    vendor,
    credential,
    adapter_class,
):
    monkeypatch.setenv(env_name, vendor)
    monkeypatch.setenv(*credential)
    if logical_model == "kling_3" and vendor == "kling":
        monkeypatch.setenv("KLING_SECRET_KEY", "secret")

    provider = vp.get_provider(logical_model)
    assert isinstance(provider, adapter_class)
    identity = vp.provider_identity(provider)
    assert identity == {
        "logical_model": logical_model,
        "vendor": vendor,
        "adapter": adapter_class.__name__,
        "vendor_model": provider.model_name(),
    }


def test_auto_vendor_availability_and_priority(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    assert isinstance(vp.get_provider("seedance"), vp.FalSeedanceProvider)
    assert isinstance(vp.get_provider("kling_3"), vp.FalKlingProvider)
    assert isinstance(vp.get_provider("wan_3_standard"), vp.FalWan3StandardProvider)

    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    assert isinstance(vp.get_provider("seedance"), vp.RunwaySeedanceProvider)
    assert isinstance(vp.get_provider("kling_3"), vp.FalKlingProvider)
    assert isinstance(vp.get_provider("wan_3_standard"), vp.RunwayWanStandardProvider)

    monkeypatch.delenv("RUNWAY_API_KEY")
    monkeypatch.delenv("FAL_KEY")
    monkeypatch.setenv("SEEDANCE_API_KEY", "byteplus")
    monkeypatch.setenv("KLING_ACCESS_KEY", "access")
    monkeypatch.setenv("KLING_SECRET_KEY", "secret")
    assert isinstance(vp.get_provider("seedance"), vp.SeedanceProvider)
    assert isinstance(vp.get_provider("kling_3"), vp.KlingProvider)


def test_explicit_vendor_missing_credentials_does_not_silently_switch(monkeypatch):
    monkeypatch.setenv("MOTION_VENDOR_SEEDANCE", "runway")
    monkeypatch.setenv("FAL_KEY", "fal-is-available")
    contract = {
        "provider_order": ["seedance"],
        "requirements": {"provider_capabilities": {}},
    }
    resolved, available, _requirements = mvs._resolve_providers_for_contract(contract)
    assert resolved == []
    assert available == []
    assert isinstance(vp.get_provider("seedance"), vp.RunwaySeedanceProvider)


@pytest.mark.parametrize(
    "env_name,value,code",
    [
        ("MOTION_VENDOR_SEEDANCE", "unknown", "UNKNOWN_VENDOR"),
        ("MOTION_VENDOR_KLING_3", "runway", "UNKNOWN_VENDOR"),
        ("MOTION_VENDOR_WAN_3_STANDARD", "direct", "UNSUPPORTED_MODEL_VENDOR"),
    ],
)
def test_unknown_or_unsupported_vendor_fails_closed(monkeypatch, env_name, value, code):
    monkeypatch.setenv(env_name, value)
    logical_model = {
        "MOTION_VENDOR_SEEDANCE": "seedance",
        "MOTION_VENDOR_KLING_3": "kling_3",
        "MOTION_VENDOR_WAN_3_STANDARD": "wan_3_standard",
    }[env_name]
    with pytest.raises(vp.VideoProviderError) as exc:
        vp.get_provider(logical_model)
    assert exc.value.code == code


def test_wan_versions_are_never_aliases_for_wan_3_standard(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    standard = vp.get_provider("wan_3_standard")
    turbo = vp.get_provider("wan")
    flf = vp.get_provider("wan_flf")

    assert vp.provider_identity(standard)["logical_model"] == "wan_3_standard"
    assert vp.provider_identity(turbo)["logical_model"] == "wan_2_2_turbo"
    assert vp.provider_identity(flf)["logical_model"] == "wan_2_1_flf"
    assert type(standard) not in (type(turbo), type(flf))


def test_vendor_swap_requires_no_motion_spec_change(monkeypatch):
    original = ms.provider_order_for_motion("PET_HEAD")
    monkeypatch.setenv("FAL_KEY", "fal")

    monkeypatch.setenv("MOTION_VENDOR_SEEDANCE", "fal")
    assert isinstance(vp.get_provider(original[0]), vp.FalSeedanceProvider)
    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    monkeypatch.setenv("MOTION_VENDOR_SEEDANCE", "runway")
    assert isinstance(vp.get_provider(original[0]), vp.RunwaySeedanceProvider)

    assert ms.provider_order_for_motion("PET_HEAD") == original


def test_wan_3_vendor_swap_requires_no_motion_spec_change(monkeypatch):
    original = ms.provider_order_for_motion("BREATHING")
    assert original[0] == "wan_3_standard"

    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "runway")
    assert isinstance(vp.get_provider(original[0]), vp.RunwayWanStandardProvider)

    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "fal")
    fal = vp.get_provider(original[0])
    assert isinstance(fal, vp.FalWan3StandardProvider)
    assert vp.provider_identity(fal) == {
        "logical_model": "wan_3_standard",
        "vendor": "fal",
        "adapter": "FalWan3StandardProvider",
        "vendor_model": "alibaba/wan-3.0/image-to-video",
    }
    assert ms.provider_order_for_motion("BREATHING") == original


def test_fal_wan_3_survives_end_frame_capability_filter(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "fal")
    contract = ms.motion_snapshot(ms.MOTIONS["LIE_DOWN"])
    contract["provider_order"] = ["wan_3_standard"]

    resolved, available, requirements = mvs._resolve_providers_for_contract(contract)

    assert requirements["required_all"] == ["supports_end_frame"]
    assert [provider.logical_model_id for provider in available] == ["wan_3_standard"]
    assert [provider.logical_model_id for provider in resolved] == ["wan_3_standard"]
    assert isinstance(resolved[0], vp.FalWan3StandardProvider)


def test_explicit_fal_wan_3_without_credentials_fails_closed(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "fal")
    contract = {
        "provider_order": ["wan_3_standard"],
        "requirements": {"provider_capabilities": {}},
    }

    resolved, available, _requirements = mvs._resolve_providers_for_contract(contract)

    assert resolved == []
    assert available == []
    assert isinstance(vp.get_provider("wan_3_standard"), vp.FalWan3StandardProvider)


def test_durable_worker_preserves_fal_model_order(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    monkeypatch.setenv("MOTION_VENDOR_SEEDANCE", "fal")
    monkeypatch.setenv("MOTION_VENDOR_KLING_3", "fal")
    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "runway")
    run = SimpleNamespace(id="run-1", user_id="user-1", pet_id="pet-1")

    interaction = runs._video_providers(run, "PET_HEAD")
    locomotion = runs._video_providers(run, "COME_CLOSER")
    assert [provider.logical_model_id for provider in interaction] == [
        "seedance",
        "kling_3",
    ]
    assert [provider.vendor_id for provider in interaction] == ["fal", "fal"]
    assert [provider.logical_model_id for provider in locomotion] == [
        "kling_3",
        "wan_3_standard",
    ]

    contract = ms.motion_snapshot(ms.MOTIONS["LIE_DOWN"])
    transition = runs._video_providers(run, "LIE_DOWN")
    filtered, available, _requirements = mvs._resolve_providers_for_contract(
        contract, transition
    )
    assert [provider.logical_model_id for provider in available] == [
        "kling_3",
        "wan_3_standard",
    ]
    assert [provider.logical_model_id for provider in filtered] == ["kling_3"]

    monkeypatch.setenv("MOTION_VENDOR_WAN_3_STANDARD", "fal")
    micro = runs._video_providers(run, "BREATHING")
    assert [provider.logical_model_id for provider in micro] == [
        "wan_3_standard",
        "seedance",
    ]
    assert micro[0].vendor_id == "fal"
    assert micro[0].adapter_id == "FalWan3StandardProvider"


def test_direct_adapter_is_not_admitted_to_durable_worker(monkeypatch):
    monkeypatch.setenv("KLING_ACCESS_KEY", "access")
    monkeypatch.setenv("KLING_SECRET_KEY", "secret")
    monkeypatch.setenv("RUNWAY_API_KEY", "runway")
    monkeypatch.setenv("MOTION_VENDOR_KLING_3", "kling")
    run = SimpleNamespace(id="run-2", user_id="user-1", pet_id="pet-1")

    providers = runs._video_providers(run, "COME_CLOSER")
    assert [provider.logical_model_id for provider in providers] == [
        "wan_3_standard"
    ]


def test_native_direct_adapters_do_not_claim_durable_contract():
    providers = jobs.durable_video_providers(
        [vp.SeedanceProvider(), vp.KlingProvider()],
        run_id="run-direct",
        user_id="user-1",
        pet_id="pet-1",
    )
    assert providers == []


class _Response:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_fal_durable_resume_is_idempotent_and_records_vendor(monkeypatch):
    import httpx

    monkeypatch.setenv("FAL_KEY", "fal")
    calls = {"post": 0}

    def post(url, **kwargs):
        calls["post"] += 1
        return _Response(
            200,
            {
                "request_id": "fal-job-1",
                "status_url": "https://fal.test/status",
                "response_url": "https://fal.test/result",
            },
        )

    def get(url, **kwargs):
        if url == "https://fal.test/status":
            return _Response(200, {"status": "COMPLETED"})
        return _Response(200, {"video": {"url": "https://cdn.test/video.mp4"}})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(vp, "_download", lambda _url: b"video")
    provider = vp.FalSeedanceProvider()

    def wrapper():
        return jobs.DurableVideoProvider(
            provider,
            run_id="run-fal-1",
            user_id="user-1",
            pet_id="pet-1",
            provider_operation=jobs.OP_MOTION,
        )

    request = MotionVideoRequest(
        prompt="p",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=b"image",
        output_spec={"duration_sec": 4, "resolution": "720p", "audio": False},
        metadata={
            "motion_version_id": "motion-version-1",
            "start_keyframe_id": "keyframe-1",
            "attempt": 1,
        },
    )

    with pytest.raises(jobs.ProviderWorkPending):
        wrapper().generate(request)
    result = wrapper().generate(request)

    assert result.video_bytes == b"video"
    assert calls["post"] == 1
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    state = next(iter(jobs.summary_for_run("run-fal-1").values()))
    assert state["logical_model"] == "seedance"
    assert state["vendor"] == "fal"
    assert state["adapter"] == "FalSeedanceProvider"
    assert state["vendor_model"] == provider.model_name()


def test_fal_wan_3_durable_submit_check_collect_is_idempotent(monkeypatch):
    import httpx

    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("WAN_QUEUE_BASE", "https://queue.fal.run")
    calls = {"post": 0}
    posted: dict = {}

    def post(url, **kwargs):
        calls["post"] += 1
        posted["url"] = url
        posted["json"] = kwargs["json"]
        return _Response(
            200,
            {
                "request_id": "fal-wan3-job-1",
                "status_url": "https://fal.test/wan3/status",
                "response_url": "https://fal.test/wan3/result",
            },
        )

    def get(url, **kwargs):
        if url == "https://fal.test/wan3/status":
            return _Response(200, {"status": "COMPLETED"})
        return _Response(
            200,
            {
                "video": {"url": "https://cdn.test/wan3.mp4"},
                "seed": 42,
                "duration": 5.0,
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(vp, "_download", lambda _url: b"wan3-video")
    provider = vp.FalWan3StandardProvider()

    def wrapper():
        return jobs.DurableVideoProvider(
            provider,
            run_id="run-fal-wan3",
            user_id="user-1",
            pet_id="pet-1",
            provider_operation=jobs.OP_MOTION,
        )

    request = MotionVideoRequest(
        prompt="gentle breathing",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=b"start",
        end_image_url="https://cdn.test/end.png",
        end_image_bytes=b"end",
        output_spec={
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "duration_sec": 5,
            "audio": False,
        },
        metadata={
            "motion_version_id": "motion-version-wan3",
            "start_keyframe_id": "keyframe-start",
            "target_keyframe_id": "keyframe-end",
            "attempt": 1,
        },
    )

    with pytest.raises(jobs.ProviderWorkPending):
        wrapper().generate(request)
    result = wrapper().generate(request)
    restarted_result = wrapper().generate(request)

    assert result.video_bytes == b"wan3-video"
    assert restarted_result.video_bytes == b"wan3-video"
    assert calls["post"] == 1
    assert posted == {
        "url": "https://queue.fal.run/alibaba/wan-3.0/image-to-video",
        "json": {
            "prompt": "gentle breathing",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "duration": 5,
            "audio": False,
            "start_image_url": "https://cdn.test/start.png",
            "end_image_url": "https://cdn.test/end.png",
        },
    }
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    state = next(iter(jobs.summary_for_run("run-fal-wan3").values()))
    assert state["logical_model"] == "wan_3_standard"
    assert state["vendor"] == "fal"
    assert state["adapter"] == "FalWan3StandardProvider"
    assert state["vendor_model"] == "alibaba/wan-3.0/image-to-video"
