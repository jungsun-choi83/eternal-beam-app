"""Fal 1.5 registration and conditioning contracts; all HTTP is mocked."""

from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services import motion_spec as ms
from backend.services import video_motion_providers as vp

MODEL = "grok_imagine_1_5_reference"
ENDPOINT = "xai/grok-imagine-video/v1.5/reference-to-video"
REFS = ("https://cdn.test/front.png", "https://cdn.test/side.png")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    for key in (
        "PHASE6_VIDEO_TRANSPORT", "VIDEO_GENERATION_MOCK", "FAL_KEY", "FAL_API_KEY",
        "FAL_INPUT_TRANSPORT", "WAN_QUEUE_BASE", "MOTION_VENDOR_GROK_IMAGINE_1_5_REFERENCE",
        "FAL_GROK_IMAGINE_1_5_REFERENCE_MODEL", "MOTION_VENDOR_GROK_IMAGINE_REFERENCE",
        "FAL_GROK_IMAGINE_REFERENCE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    for method in ("post", "get", "put"):
        monkeypatch.setattr(httpx, method, Mock(side_effect=AssertionError("unexpected HTTP")))
    jobs.__reset_for_tests()
    yield
    jobs.__reset_for_tests()


def request(**kwargs):
    return replace(vp.MotionVideoRequest(
        prompt="The pet from <IMAGE_0> and <IMAGE_1> walks toward the camera.",
        reference_urls=REFS,
        output_spec={"duration_sec": 15, "resolution": "720p", "aspect_ratio": "9:16"},
        metadata={"motion_version_id": "version-1", "attempt": 1},
    ), **kwargs)


def wrapper(provider=None):
    return jobs.durable_video_providers(
        [provider or vp.get_provider(MODEL)], run_id="run-1", user_id="user-1", pet_id="pet-1",
    )[0]


def test_registration_resolution_and_capabilities(monkeypatch):
    assert vp.MODEL_GROK_IMAGINE_1_5_REFERENCE == MODEL
    assert MODEL in vp.registered_logical_models()
    assert vp.is_registered_model(MODEL)
    assert vp.vendor_for_model(MODEL) == "fal"
    provider = vp.get_provider(MODEL)
    assert type(provider) is vp.FalGrokImagine15ReferenceProvider
    assert vp.resolve_provider_order([MODEL]) == [provider]
    assert provider.identity() == {
        "logical_model": MODEL, "vendor": "fal",
        "adapter": "FalGrokImagine15ReferenceProvider", "vendor_model": ENDPOINT,
    }
    assert not provider.available()
    monkeypatch.setenv("FAL_KEY", "fake-key")
    assert provider.available()
    assert provider.supports_durable_jobs is True
    for candidate in (provider, wrapper(provider)):
        assert candidate.supports_reference_images is True
        assert candidate.supports_multiple_references is True
        assert candidate.reference_budget == 7
        assert candidate.supports_start_image is False
        assert candidate.supports_end_frame is False
        assert candidate.supports_keyframes is False
        assert candidate.supports_motion_reference is False


def test_configuration_is_independent_of_older_reference_model(monkeypatch):
    monkeypatch.setenv("MOTION_VENDOR_GROK_IMAGINE_1_5_REFERENCE", "fal")
    monkeypatch.setenv("FAL_GROK_IMAGINE_1_5_REFERENCE_MODEL", "test/reference-v15")
    assert vp.get_provider(MODEL).model_name() == "test/reference-v15"
    assert vp.get_provider("grok_imagine_reference").model_name() == "xai/grok-imagine-video/reference-to-video"
    monkeypatch.setenv("MOTION_VENDOR_GROK_IMAGINE_1_5_REFERENCE", "runway")
    with pytest.raises(vp.VideoProviderError, match="runway") as caught:
        vp.get_provider(MODEL)
    assert caught.value.code == "UNKNOWN_VENDOR"


@pytest.mark.parametrize("motion", ["COME_CLOSER", "RETURN_HOME"])
def test_opt_in_routing_resolves_without_changing_defaults(monkeypatch, motion):
    for name in ms.MOTIONS:
        monkeypatch.delenv(f"MOTION_MODEL_{name}", raising=False)
    defaults = {name: ms.provider_order_for_motion(name) for name in ms.MOTIONS}
    assert all(MODEL not in order for order in defaults.values())
    assert defaults["BREATHING"] == ("wan_3_standard",)
    assert defaults["COME_CLOSER"] == ("grok_imagine_lite",)
    assert defaults["RETURN_HOME"] == ("kling_3",)
    monkeypatch.setenv(f"MOTION_MODEL_{motion}", MODEL)
    assert ms.provider_order_for_motion(motion) == (MODEL,)
    assert type(vp.resolve_provider_order(ms.provider_order_for_motion(motion))[0]) is vp.FalGrokImagine15ReferenceProvider
    for name, order in defaults.items():
        if name != motion:
            assert ms.provider_order_for_motion(name) == order


def test_payload_preserves_all_reference_indices_and_duplicates():
    refs = (REFS[1], REFS[0], REFS[1], *(f"https://cdn.test/{i}.png" for i in range(4)))
    req = request(reference_urls=refs)
    assert vp.get_provider(MODEL).build_payload(req) == {
        "prompt": req.prompt, "reference_image_urls": list(refs),
        "duration": 15, "resolution": "720p", "aspect_ratio": "9:16",
    }
    assert req.reference_urls == refs


def test_defaults_and_single_reference():
    payload = vp.get_provider(MODEL).build_payload(request(reference_urls=REFS[:1], output_spec={}))
    assert payload["reference_image_urls"] == list(REFS[:1])
    assert (payload["duration"], payload["resolution"], payload["aspect_ratio"]) == (8, "480p", "16:9")


@pytest.mark.parametrize("duration", [1, 8, 11, 15])
@pytest.mark.parametrize("resolution", ["480p", "720p"])
def test_supported_duration_resolution(duration, resolution):
    payload = vp.get_provider(MODEL).build_payload(request(output_spec={
        "duration_sec": duration, "resolution": resolution,
    }))
    assert payload["duration"] == duration
    assert payload["resolution"] == resolution


INVALID = [
    ({"reference_urls": ()}, "reference_urls"),
    ({"reference_urls": REFS * 4}, "reference_urls"),
    ({"reference_urls": "https://cdn.test/front.png"}, "reference_urls"),
    ({"reference_urls": ("file:///front.png",)}, "reference_urls"),
    ({"start_image_url": REFS[0]}, "start_image_url"),
    ({"start_image_bytes": b"start"}, "start_image_bytes"),
    ({"end_image_url": REFS[1]}, "end_image_url"),
    ({"end_image_bytes": b"end"}, "end_image_bytes"),
    ({"keyframes": (vp.MotionVideoKeyframe(REFS[0], 3),)}, "keyframes"),
    ({"motion_reference_url": "https://cdn.test/motion.mp4"}, "motion_reference_url"),
    ({"output_spec": {"resolution": "1080p"}}, "resolution"),
    ({"output_spec": {"aspect_ratio": "auto"}}, "aspect_ratio"),
    ({"output_spec": {"camera_fixed": True}}, "camera_fixed"),
    ({"output_spec": {"image_url": REFS[0]}}, "image_url"),
    ({"output_spec": {"last_frame_url": REFS[1]}}, "last_frame_url"),
    ({"prompt": "x" * 4097}, "prompt"),
    *[({"output_spec": {"duration_sec": d}}, "duration_sec")
      for d in (0, 16, 2.5, True, "8", None, float("nan"), float("inf"))],
]


@pytest.mark.parametrize("overrides,message", INVALID)
@pytest.mark.parametrize("method", ["build_payload", "submit"])
def test_invalid_fields_fail_clearly_before_http(overrides, message, method):
    with pytest.raises(vp.VideoProviderError, match=message) as caught:
        getattr(vp.get_provider(MODEL), method)(request(**overrides))
    assert caught.value.code == "PROVIDER_CONTRACT"
    httpx.post.assert_not_called()
    httpx.put.assert_not_called()


def test_older_reference_adapter_keeps_10_second_limit():
    provider = vp.get_provider("grok_imagine_reference")
    with pytest.raises(vp.VideoProviderError, match="1..10"):
        provider.build_payload(request())
    assert provider.build_payload(request(output_spec={"duration_sec": 10}))["duration"] == 10


@pytest.mark.parametrize("transport", ["signed_url", "fal_storage"])
def test_durable_queue_resume_uses_v15_endpoint_once(monkeypatch, transport):
    monkeypatch.setenv("FAL_KEY", "fake-key")
    monkeypatch.setenv("FAL_INPUT_TRANSPORT", transport)
    post = Mock(return_value=httpx.Response(200, json={
        "request_id": "fal-job", "status_url": "https://fal.test/status",
        "response_url": "https://fal.test/result",
    }))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=(
        {"status": "COMPLETED"} if url.endswith("status") else {"video": {"url": "https://cdn.test/video.mp4"}}
    )))
    monkeypatch.setattr(vp, "_download", lambda _: b"video")
    with pytest.raises(jobs.ProviderWorkPending):
        wrapper().generate(request())
    assert post.call_args.args == (f"https://queue.fal.run/{ENDPOINT}",)
    assert post.call_args.kwargs["json"] == vp.get_provider(MODEL).build_payload(request())
    assert wrapper().generate(request()).video_bytes == b"video"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    with pytest.raises(jobs.ProviderRecoveryRequired, match="fingerprint"):
        wrapper().generate(request(reference_urls=tuple(reversed(REFS))))
    post.assert_called_once()
    httpx.put.assert_not_called()


@pytest.mark.parametrize("provider", [
    vp.SeedanceProvider(), vp.KlingProvider(), vp.FalKlingProvider(),
    vp.RunwaySeedanceProvider(), vp.MockVideoProvider(),
])
def test_existing_adapters_cannot_silently_drop_new_keyframes(provider):
    with pytest.raises(vp.VideoProviderError, match="does not support keyframes"):
        provider.generate(request(keyframes=(vp.MotionVideoKeyframe(REFS[0], 3),)))
    httpx.post.assert_not_called()


def test_future_adapter_can_combine_references_and_pinned_frames(monkeypatch):
    # Contract test only: no direct-xAI adapter or routing is registered.
    class FutureProvider(vp.VideoGenerationProvider):
        name = logical_model_id = "future-test"
        supports_durable_jobs = True
        supports_reference_images = supports_multiple_references = True
        supports_start_image = supports_end_frame = supports_keyframes = True

        def submit(self, req):
            vp.validate_keyframe_capability(self, req)
            received.append(req)
            raise AssertionError("submission intercepted")

        def collect(self, job_id):
            raise AssertionError("collection intercepted")

        def check(self, job_id):
            raise AssertionError("status intercepted")

    received = []
    captured_hashes = []
    provider = wrapper(FutureProvider())
    assert provider.supports_keyframes and provider.supports_end_frame and provider.supports_start_image
    assert provider.supports_reference_images and provider.supports_multiple_references

    def execute(phase_id, attempt, request_hash, submit, collect):
        captured_hashes.append(request_hash)
        submit()

    monkeypatch.setattr(provider, "_execute", execute)
    req = request(start_image_url=REFS[0], end_image_url=REFS[1],
                  keyframes=(vp.MotionVideoKeyframe(REFS[0], 3),))
    for candidate in (req, replace(req, keyframes=(vp.MotionVideoKeyframe(REFS[0], 4),))):
        with pytest.raises(AssertionError, match="submission intercepted"):
            provider.generate(candidate)
    assert received[0] is req
    assert captured_hashes[0] != captured_hashes[1]


def test_durable_fal_rejects_intermediate_frames_before_job_creation():
    with pytest.raises(vp.VideoProviderError, match="keyframes"):
        wrapper().generate(request(keyframes=(vp.MotionVideoKeyframe(REFS[0], 3),)))
    assert not jobs._MOCK_JOBS
    httpx.post.assert_not_called()
