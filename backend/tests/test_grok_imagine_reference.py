"""Reference-only Fal adapter registration. No live or paid provider calls."""

from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services import motion_spec as ms
from backend.services import video_motion_providers as vp

MODEL = "grok_imagine_reference"
ENDPOINT = "xai/grok-imagine-video/reference-to-video"
REFS = ("https://cdn.test/front.png", "https://cdn.test/side.png")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    for key in (
        "PHASE6_VIDEO_TRANSPORT", "VIDEO_GENERATION_MOCK", "FAL_KEY", "FAL_API_KEY",
        "FAL_INPUT_TRANSPORT", "WAN_QUEUE_BASE", "MOTION_VENDOR_GROK_IMAGINE_REFERENCE",
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
        prompt="@Image1 and @Image2 show the same subject moving slowly",
        reference_urls=REFS,
        output_spec={"duration_sec": 4, "resolution": "720p", "aspect_ratio": "9:16"},
        metadata={"motion_version_id": "version-1", "attempt": 1},
    ), **kwargs)


def wrapper(provider=None):
    return jobs.durable_video_providers(
        [provider or vp.get_provider(MODEL)], run_id="run-1", user_id="user-1", pet_id="pet-1",
    )[0]


def test_registration_resolution_and_capabilities(monkeypatch):
    assert MODEL in vp.registered_logical_models()
    assert vp.is_registered_model(MODEL)
    assert vp.vendor_for_model(MODEL) == "fal"
    provider = vp.get_provider(MODEL)
    assert isinstance(provider, vp.FalGrokImagineReferenceProvider)
    assert vp.resolve_provider_order([MODEL]) == [provider]
    assert not provider.available()
    monkeypatch.setenv("FAL_KEY", "fake-key")
    assert provider.available()
    assert provider.identity() == {
        "logical_model": MODEL, "vendor": "fal",
        "adapter": "FalGrokImagineReferenceProvider", "vendor_model": ENDPOINT,
    }
    assert provider.supports_durable_jobs is True
    for candidate in (provider, wrapper(provider)):
        assert candidate.supports_reference_images is True
        assert candidate.supports_multiple_references is True
        assert candidate.supports_start_image is False
        assert candidate.supports_end_frame is False
        assert candidate.supports_motion_reference is False
        assert candidate.reference_budget == 7


def test_vendor_and_endpoint_configuration(monkeypatch):
    monkeypatch.setenv("MOTION_VENDOR_GROK_IMAGINE_REFERENCE", "fal")
    assert vp.get_provider(MODEL).model_name() == ENDPOINT
    monkeypatch.setenv("FAL_GROK_IMAGINE_REFERENCE_MODEL", "test/reference-endpoint")
    assert vp.get_provider(MODEL).model_name() == "test/reference-endpoint"
    monkeypatch.setenv("MOTION_VENDOR_GROK_IMAGINE_REFERENCE", "runway")
    with pytest.raises(vp.VideoProviderError) as caught:
        vp.get_provider(MODEL)
    assert caught.value.code == "UNKNOWN_VENDOR"


def test_existing_defaults_and_opt_in_config(monkeypatch):
    for motion in ms.MOTIONS:
        monkeypatch.delenv(f"MOTION_MODEL_{motion}", raising=False)
        assert MODEL not in ms.provider_order_for_motion(motion)
    assert ms.provider_order_for_motion("BREATHING") == ("wan_3_standard",)
    assert ms.provider_order_for_motion("COME_CLOSER") == ("grok_imagine_lite",)
    assert ms.provider_order_for_motion("RETURN_HOME") == ("kling_3",)
    monkeypatch.setenv("MOTION_MODEL_COME_CLOSER", MODEL)
    assert ms.provider_order_for_motion("COME_CLOSER") == (MODEL,)
    assert isinstance(vp.resolve_provider_order(ms.provider_order_for_motion("COME_CLOSER"))[0],
                      vp.FalGrokImagineReferenceProvider)
    assert ms.provider_order_for_motion("BREATHING") == ("wan_3_standard",)
    assert ms.provider_order_for_motion("RETURN_HOME") == ("kling_3",)


def test_multi_reference_payload_preserves_order_and_has_no_frame_controls():
    req = request()
    assert req.start_image_url is None and req.start_image_bytes is None
    assert vp.get_provider(MODEL).build_payload(req) == {
        "prompt": req.prompt, "reference_image_urls": list(REFS),
        "duration": 4, "resolution": "720p", "aspect_ratio": "9:16",
    }


def test_documented_defaults_and_maximum_references():
    refs = tuple(f"https://cdn.test/{n}.png" for n in range(7))
    payload = vp.get_provider(MODEL).build_payload(request(reference_urls=refs, output_spec={}))
    assert payload["reference_image_urls"] == list(refs)
    assert (payload["duration"], payload["resolution"], payload["aspect_ratio"]) == (8, "480p", "16:9")


@pytest.mark.parametrize("duration", [1, 10])
@pytest.mark.parametrize("resolution", ["480p", "720p"])
@pytest.mark.parametrize("ratio", vp.FalGrokImagineReferenceProvider.ALLOWED_ASPECT_RATIOS)
def test_supported_output_contract(duration, resolution, ratio):
    payload = vp.get_provider(MODEL).build_payload(request(output_spec={
        "duration_sec": duration, "resolution": resolution, "aspect_ratio": ratio,
    }))
    assert payload["duration"] == duration
    assert payload["resolution"] == resolution
    assert payload["aspect_ratio"] == ratio


def test_image_data_uri_reference():
    uri = "data:image/png;base64,aW1hZ2U="
    assert vp.get_provider(MODEL).build_payload(request(reference_urls=(uri,)))["reference_image_urls"] == [uri]


INVALID = [
    {"reference_urls": ()},
    {"reference_urls": REFS * 4},
    {"reference_urls": "https://cdn.test/image.png"},
    {"reference_urls": (None,)},
    {"reference_urls": ("",)},
    {"reference_urls": ("file:///image.png",)},
    {"reference_urls": ("https://",)},
    {"reference_urls": ("data:video/mp4;base64,aW1hZ2U=",)},
    {"start_image_url": "https://cdn.test/start.png"},
    {"start_image_bytes": b"start"},
    {"end_image_url": "https://cdn.test/end.png"},
    {"end_image_bytes": b"end"},
    {"motion_reference_url": "https://cdn.test/motion.mp4"},
    {"prompt": " "},
    {"prompt": "x" * 4097},
    {"output_spec": None},
    {"output_spec": {"audio": False}},
    {"output_spec": {"camera_fixed": True}},
    {"output_spec": {"seed": 42}},
    {"output_spec": {"resolution": "1080p"}},
    {"output_spec": {"aspect_ratio": "auto"}},
    *[{"output_spec": {"duration_sec": d}} for d in (0, 11, 4.5, True, "4", None, float("nan"), float("inf"))],
]


@pytest.mark.parametrize("overrides", INVALID)
@pytest.mark.parametrize("method", ["build_payload", "submit"])
def test_invalid_inputs_fail_before_any_http(overrides, method, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake-key")
    with pytest.raises(vp.VideoProviderError) as caught:
        getattr(vp.get_provider(MODEL), method)(request(**overrides))
    assert caught.value.code == "PROVIDER_CONTRACT"
    assert caught.value.message
    httpx.post.assert_not_called()
    httpx.put.assert_not_called()


def test_existing_i2v_providers_still_require_start_image(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fake-key")
    for provider in (vp.FalSeedanceProvider(), vp.FalKlingProvider(), vp.FalGrokImagineLiteProvider()):
        assert provider.supports_start_image is True
        assert provider.supports_reference_images is False
        with pytest.raises(vp.VideoProviderError) as caught:
            provider.submit(request())
        assert caught.value.code == "NO_START_IMAGE"
    httpx.post.assert_not_called()


@pytest.mark.parametrize("transport", ["signed_url", "fal_storage"])
def test_durable_queue_resume_preserves_multi_reference_payload(monkeypatch, transport):
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
    assert post.call_args.kwargs["json"]["reference_image_urls"] == list(REFS)
    result = wrapper().generate(request())
    assert result.video_bytes == b"video"
    assert result.provider == MODEL
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.COLLECTED
    post.assert_called_once()
    httpx.put.assert_not_called()
    # Different identity references/order cannot reuse the original submission.
    with pytest.raises(jobs.ProviderRecoveryRequired, match="fingerprint"):
        wrapper().generate(request(reference_urls=tuple(reversed(REFS))))
    post.assert_called_once()


def test_invalid_durable_request_is_definitively_failed_not_ambiguous():
    with pytest.raises(vp.VideoProviderError) as caught:
        wrapper().generate(request(end_image_bytes=b"unsupported"))
    assert caught.value.code == "PROVIDER_CONTRACT"
    assert jobs._MOCK_JOBS[0]["submission_status"] == jobs.FAILED
    httpx.post.assert_not_called()
