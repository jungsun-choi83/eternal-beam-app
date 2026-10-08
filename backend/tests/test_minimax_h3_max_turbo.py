"""MiniMax H3 Max Turbo (fal) adapter + config-driven BREATHING model — fakes only."""

from __future__ import annotations

import pytest

from backend.services import durable_provider_jobs as jobs
from backend.services import motion_spec as ms
from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    FalMinimaxH3MaxTurboProvider,
    FalWan3StandardProvider,
    MotionVideoRequest,
    VideoProviderError,
)

#: What the pipeline already requests for BREATHING (test profile: 4.0 s, 480p).
BREATHING_SPEC = {
    "aspect_ratio": "16:9",
    "resolution": "480p",
    "duration_sec": 4,
    "audio": False,
    "camera_fixed": True,
}


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "FAL_KEY", "FAL_API_KEY", "RUNWAY_API_KEY", "PHASE6_VIDEO_TRANSPORT",
        "VIDEO_GENERATION_MOCK", "FAL_INPUT_TRANSPORT", "MOTION_MODEL_BREATHING",
        "MOTION_VENDOR_MINIMAX_H3_MAX_TURBO", "MOTION_VENDOR_WAN_3_STANDARD",
        "WAN_3_STANDARD_TRANSPORT", "FAL_MINIMAX_H3_PROMPT_EXPANSION",
    ):
        monkeypatch.delenv(var, raising=False)
    jobs._MOCK_JOBS.clear()


def _request(spec=None, **overrides) -> MotionVideoRequest:
    fields = dict(
        prompt="calm breathing",
        start_image_url="https://cdn.test/start.png",
        start_image_bytes=b"start",
        output_spec=dict(BREATHING_SPEC if spec is None else spec),
        metadata={"motion_version_id": "mv-1", "start_keyframe_id": "kf-1", "attempt": 1},
    )
    fields.update(overrides)
    return MotionVideoRequest(**fields)


# ── routing ───────────────────────────────────────────────────────────────


def test_breathing_routes_to_minimax_by_default(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    providers = vp.resolve_provider_order(ms.provider_order_for_motion("BREATHING"))
    assert isinstance(providers[0], FalMinimaxH3MaxTurboProvider)
    assert vp.provider_identity(providers[0]) == {
        "logical_model": "minimax_h3_max_turbo",
        "vendor": "fal",
        "adapter": "FalMinimaxH3MaxTurboProvider",
        "vendor_model": "minimax/h3-max-turbo/image-to-video",
    }
    assert [p.name for p in providers] == ["minimax_h3_max_turbo", "seedance"]


def test_breathing_routes_to_wan_when_config_says_so(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("MOTION_MODEL_BREATHING", "wan_3_standard")
    providers = vp.resolve_provider_order(ms.provider_order_for_motion("BREATHING"))
    assert isinstance(providers[0], FalWan3StandardProvider)
    assert [p.name for p in providers] == ["wan_3_standard", "seedance"]


def test_other_micro_motions_keep_class_order():
    assert ms.provider_order_for_motion("BLINKING") == ("wan_3_standard", "seedance")


def test_unknown_model_fails_with_clear_error(monkeypatch):
    monkeypatch.setenv("MOTION_MODEL_BREATHING", "sora_9")
    with pytest.raises(VideoProviderError) as exc:
        ms.provider_order_for_motion("BREATHING")
    assert exc.value.code == "UNKNOWN_LOGICAL_MODEL"
    assert "MOTION_MODEL_BREATHING" in exc.value.message
    assert "minimax_h3_max_turbo" in exc.value.message  # lists what is allowed


def test_unknown_vendor_fails_with_clear_error(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("MOTION_VENDOR_MINIMAX_H3_MAX_TURBO", "runway")
    with pytest.raises(VideoProviderError) as exc:
        vp.get_provider("minimax_h3_max_turbo")
    assert exc.value.code == "UNKNOWN_VENDOR"
    assert "minimax_h3_max_turbo" in exc.value.message


# ── fal request ───────────────────────────────────────────────────────────


def test_payload_maps_pipeline_spec_to_fal_schema():
    payload = FalMinimaxH3MaxTurboProvider().build_payload(_request())
    assert payload == {
        "prompt": "calm breathing",
        "prompt_expansion_mode": "disabled",
        "image_url": "https://cdn.test/start.png",
        "duration": 4,
        "resolution": "480P",
    }


@pytest.mark.parametrize(
    "resolution, expected", [("480p", "480P"), ("720p", "768P"), ("1080p", "1080P")]
)
def test_resolution_mapping(resolution, expected):
    payload = FalMinimaxH3MaxTurboProvider().build_payload(
        _request({**BREATHING_SPEC, "resolution": resolution})
    )
    assert payload["resolution"] == expected


def test_payload_follows_spec_and_never_invents_defaults():
    provider = FalMinimaxH3MaxTurboProvider()
    payload = provider.build_payload(
        _request({**BREATHING_SPEC, "duration_sec": 6.5}, end_image_url="https://cdn.test/end.png")
    )
    assert payload["duration"] == 6.5
    assert payload["end_image_url"] == "https://cdn.test/end.png"
    for bad in (
        {"resolution": "480p"},                        # no duration
        {"duration_sec": 4},                           # no resolution
        {"duration_sec": 4, "resolution": "4k"},
        {"duration_sec": 0.5, "resolution": "480p"},
        {"duration_sec": 16, "resolution": "480p"},
        {"duration_sec": 4, "resolution": "480p", "audio": True},
    ):
        with pytest.raises(VideoProviderError) as exc:
            provider.build_payload(_request(bad))
        assert exc.value.code == "PROVIDER_CONTRACT"


def test_prompt_expansion_is_configurable_and_validated(monkeypatch):
    provider = FalMinimaxH3MaxTurboProvider()
    monkeypatch.setenv("FAL_MINIMAX_H3_PROMPT_EXPANSION", "balanced")
    assert provider.build_payload(_request())["prompt_expansion_mode"] == "balanced"
    monkeypatch.setenv("FAL_MINIMAX_H3_PROMPT_EXPANSION", "turbo")
    with pytest.raises(VideoProviderError) as exc:
        provider.build_payload(_request())
    assert exc.value.code == "PROVIDER_CONTRACT"


def test_cost_estimate_uses_post_promo_rates():
    provider = FalMinimaxH3MaxTurboProvider()
    assert provider.estimate_cost_usd({"resolution": "480P", "duration": 4}) == 0.1
    assert provider.estimate_cost_usd({"resolution": "768P", "duration": 4}) == 0.16
    assert provider.estimate_cost_usd({"resolution": "1080P", "duration": 5}) == 0.4
    assert FalWan3StandardProvider().estimate_cost_usd({"duration": 4}) is None


# ── output contract + timing through the durable path ─────────────────────


def _fake_fal(monkeypatch, *, statuses, result, clock):
    import httpx

    seen = {"posts": [], "status_calls": 0}

    def post(url, **kwargs):
        seen["posts"].append({"url": url, "json": kwargs["json"]})
        return _Response(
            200,
            {
                "request_id": "fal-h3-job-1",
                "status_url": "https://fal.test/h3/status",
                "response_url": "https://fal.test/h3/result",
            },
        )

    def get(url, **kwargs):
        if url == "https://fal.test/h3/status":
            status = statuses[min(seen["status_calls"], len(statuses) - 1)]
            seen["status_calls"] += 1
            return _Response(200, {"status": status})
        return _Response(200, result)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(vp, "_download", lambda _url: b"video+audio")
    monkeypatch.setattr(
        vp, "strip_audio_track", lambda data: (b"video-only", {"audio_stripped": True})
    )
    monkeypatch.setattr(vp.time, "time", lambda: clock["now"])
    return seen


def test_durable_job_returns_output_contract_with_queue_run_and_cost(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("WAN_QUEUE_BASE", "https://queue.fal.run")
    clock = {"now": 1000.0}
    seen = _fake_fal(
        monkeypatch,
        statuses=["IN_QUEUE", "IN_PROGRESS", "COMPLETED"],
        result={
            "video": {"url": "https://cdn.test/h3.mp4"},
            "expanded_prompt": None,
            "timings": {"inference": 21.5},
        },
        clock=clock,
    )

    def wrapper():
        return jobs.DurableVideoProvider(
            FalMinimaxH3MaxTurboProvider(),
            run_id="run-h3",
            user_id="user-1",
            pet_id="pet-1",
            provider_operation=jobs.OP_MOTION,
        )

    request = _request()
    result = None
    for now in (1000.0, 1005.0, 1012.0, 1040.0):  # submit, queued, running, completed
        clock["now"] = now
        try:
            result = wrapper().generate(request)
        except jobs.ProviderWorkPending:
            continue

    assert len(seen["posts"]) == 1  # one paid submission across worker ticks
    assert seen["posts"][0]["url"] == "https://queue.fal.run/minimax/h3-max-turbo/image-to-video"
    assert seen["posts"][0]["json"]["duration"] == 4
    assert seen["posts"][0]["json"]["resolution"] == "480P"

    assert result is not None
    assert result.video_bytes == b"video-only"  # audio track removed
    assert result.provider == "minimax_h3_max_turbo"
    assert result.model == "minimax/h3-max-turbo/image-to-video"
    assert result.external_job_id == "fal-h3-job-1"
    assert result.usage["queue_sec"] == 12.0
    assert result.usage["run_sec"] == 28.0
    assert result.usage["total_sec"] == 40.0
    assert result.usage["timing_source"] == "poll"
    assert result.usage["provider_inference_sec"] == 21.5
    assert result.usage["estimated_cost_usd"] == 0.1
    assert result.usage["audio_stripped"] is True

    state = next(iter(jobs.summary_for_run("run-h3").values()))
    assert state["logical_model"] == "minimax_h3_max_turbo"
    assert state["vendor"] == "fal"
    assert state["vendor_model"] == "minimax/h3-max-turbo/image-to-video"
    assert state["adapter"] == "FalMinimaxH3MaxTurboProvider"


def test_timing_split_is_unknown_when_running_was_never_polled(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.setenv("FAL_VIDEO_POLL_INTERVAL_SEC", "0")
    clock = {"now": 500.0}
    _fake_fal(
        monkeypatch,
        statuses=["COMPLETED"],
        result={"video": {"url": "https://cdn.test/h3.mp4"}, "timings": {"inference": 20.0}},
        clock=clock,
    )
    provider = FalMinimaxH3MaxTurboProvider()
    submission = provider.submit(_request())
    clock["now"] = 530.0
    check = provider.check_persisted(submission.external_job_id, dict(submission.metadata))
    result = provider.collect_persisted(
        submission.external_job_id, {**submission.metadata, **check.metadata}
    )
    assert result.usage["queue_sec"] is None
    assert result.usage["run_sec"] is None
    assert result.usage["total_sec"] == 30.0
    assert result.usage["timing_source"] == "unknown"
    assert result.usage["provider_inference_sec"] == 20.0  # kept, but not used as run time


def test_strip_audio_track_keeps_paid_result_when_ffmpeg_is_unavailable(monkeypatch):
    import subprocess

    def boom(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", boom)
    data, notes = vp.strip_audio_track(b"bytes")
    assert data == b"bytes"
    assert notes["audio_stripped"] is False
    assert "audio_strip_error" in notes
