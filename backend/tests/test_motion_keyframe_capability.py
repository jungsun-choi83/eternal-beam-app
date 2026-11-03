"""Intermediate keyframes must never be silently dropped or trigger a paid retry."""

from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services import video_motion_providers as vp
from backend.services.provider_job_contract import (
    SUCCEEDED,
    ProviderJobCheck,
    ProviderSubmission,
)

LEGACY_FINGERPRINT = "5468d5febe90f2f51f618449e24a7bad4ce0e6cbb73b92fe2b60285157f6a607"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    for method in ("post", "get", "put"):
        monkeypatch.setattr(httpx, method, Mock(side_effect=AssertionError("unexpected HTTP")))
    jobs.__reset_for_tests()
    yield
    jobs.__reset_for_tests()


def request(**overrides):
    return replace(vp.MotionVideoRequest(
        prompt="A pet breathing calmly.",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=b"start",
        output_spec={"duration_sec": 4, "resolution": "720p"},
        metadata={"motion_version_id": "version-1", "start_keyframe_id": "start-1"},
    ), **overrides)


SUBMIT_ADAPTERS = (
    vp.FalSeedanceProvider, vp.FalKlingProvider, vp.FalWan3StandardProvider,
    vp.FalMinimaxH3MaxTurboProvider, vp.FalGrokImagineLiteProvider,
    vp.FalWanProvider, vp.FalWanFlfProvider,
    vp.RunwaySeedanceProvider, vp.RunwayWanStandardProvider, vp.RunwayWanMotionRefProvider,
)
DURABLE_ADAPTERS = tuple(adapter for adapter in SUBMIT_ADAPTERS if adapter.supports_durable_jobs)


@pytest.mark.parametrize("adapter", (
    vp.SeedanceProvider, vp.KlingProvider, vp.MockVideoProvider, *SUBMIT_ADAPTERS,
))
def test_direct_generation_rejects_unsupported_keyframes_before_http(adapter):
    with pytest.raises(vp.VideoProviderError, match="does not support keyframes") as exc:
        adapter().generate(request(keyframes=(vp.MotionVideoKeyframe("https://cdn.test/mid.png", 2),)))
    assert exc.value.code == "KEYFRAMES_UNSUPPORTED"
    httpx.post.assert_not_called()
    httpx.get.assert_not_called()
    httpx.put.assert_not_called()


@pytest.mark.parametrize("adapter", SUBMIT_ADAPTERS)
def test_direct_submission_rejects_unsupported_keyframes_before_http(adapter):
    with pytest.raises(vp.VideoProviderError, match="does not support keyframes"):
        adapter().submit(request(keyframes=(vp.MotionVideoKeyframe("https://cdn.test/mid.png", 2),)))
    httpx.post.assert_not_called()
    httpx.put.assert_not_called()


@pytest.mark.parametrize("adapter", DURABLE_ADAPTERS)
def test_durable_rejection_precedes_job_creation_and_delegate_submission(adapter, monkeypatch):
    delegate = adapter()
    submit = Mock(side_effect=AssertionError("unexpected submission"))
    monkeypatch.setattr(delegate, "submit", submit)
    provider = jobs.durable_video_providers(
        [delegate], run_id="run-1", user_id="user-1", pet_id="pet-1",
    )[0]
    with pytest.raises(vp.VideoProviderError, match="does not support keyframes"):
        provider.generate(request(keyframes=(vp.MotionVideoKeyframe("https://cdn.test/mid.png", 2),)))
    submit.assert_not_called()
    assert not jobs._MOCK_JOBS


class FakeVideoProvider(vp.VideoGenerationProvider):
    name = logical_model_id = "test-video"
    vendor_id = "test"
    adapter_id = "FakeVideoProvider"
    supports_durable_jobs = True

    def __init__(self):
        self.requests = []

    def model_name(self):
        return "test-model"

    def submit(self, req):
        vp.validate_keyframe_capability(self, req)
        self.requests.append(req)
        return ProviderSubmission("job-1")

    def check(self, external_job_id):
        return ProviderJobCheck(SUCCEEDED, SUCCEEDED)

    def collect(self, external_job_id):
        return vp.MotionVideoResult(b"video", self.name, self.model_name(), external_job_id)


def wrapper(delegate):
    return jobs.durable_video_providers(
        [delegate], run_id="run-1", user_id="user-1", pet_id="pet-1",
    )[0]


def test_ordinary_request_submits_and_resumes_with_legacy_fingerprint():
    delegate = FakeVideoProvider()
    provider = wrapper(delegate)
    req = request()
    with pytest.raises(jobs.ProviderWorkPending):
        provider.generate(req)
    # Pin the pre-keyframes fingerprint: existing jobs must resume without repurchase.
    assert jobs._MOCK_JOBS[0]["request_fingerprint"] == LEGACY_FINGERPRINT
    assert provider.generate(req).video_bytes == b"video"
    assert delegate.requests == [req]


@pytest.mark.parametrize("changed_frame", (
    vp.MotionVideoKeyframe("https://cdn.test/mid.png", 3),
    vp.MotionVideoKeyframe("https://cdn.test/other.png", 2),
))
def test_supported_frames_reach_delegate_and_changed_frames_cannot_reuse_job(changed_frame):
    delegate = FakeVideoProvider()
    delegate.supports_keyframes = True
    provider = wrapper(delegate)
    req = request(keyframes=(vp.MotionVideoKeyframe("https://cdn.test/mid.png", 2),))
    with pytest.raises(jobs.ProviderWorkPending):
        provider.generate(req)
    assert delegate.requests[0] is req
    assert provider.generate(req).video_bytes == b"video"
    with pytest.raises(jobs.ProviderRecoveryRequired, match="fingerprint"):
        provider.generate(replace(req, keyframes=(changed_frame,)))
    assert delegate.requests == [req]


def test_mock_accepts_ordinary_start_and_end_frames():
    result = vp.MockVideoProvider().generate(request(end_image_bytes=b"end"))
    assert result.video_bytes == b"start"
