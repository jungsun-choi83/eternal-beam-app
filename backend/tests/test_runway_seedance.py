"""
Runway Seedance 2.5 트랜스포트 (Phase 6) 계약 테스트 — 실 네트워크 호출 없음.

계약은 docs.dev.runwayml.com/openapi.json 기준:
  POST /v1/image_to_video model="seedance2_5"
    promptImage(단일 | [{uri,position:first|last}]), promptText, ratio(enum),
    duration(4..30 정수), audio(기본 true)
  GET /v1/tasks/{id} → SUCCEEDED {output:[url], cost} | FAILED {failure, failureCode}
"""

from __future__ import annotations

import pytest

from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    FalSeedanceProvider,
    MotionVideoRequest,
    RunwaySeedanceProvider,
    VideoProviderError,
)

SPEC = {"aspect_ratio": "9:16", "resolution": "480p", "duration_sec": 4, "audio": False, "camera_fixed": True}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("RUNWAY_API_KEY", "RUNWAY_SEEDANCE_MODEL", "FAL_KEY", "FAL_API_KEY",
                "SEEDANCE_API_KEY", "ARK_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY",
                "SEEDANCE_TRANSPORT", "KLING_TRANSPORT", "PHASE6_VIDEO_TRANSPORT",
                "VIDEO_GENERATION_MOCK"):
        monkeypatch.delenv(var, raising=False)


def _req(**kw):
    base = dict(
        prompt="p", start_image_url="https://x/start.png", start_image_bytes=b"x", output_spec=SPEC
    )
    base.update(kw)
    return MotionVideoRequest(**base)


# ── 페이로드 (문서화된 스키마와 정확히) ─────────────────────────────────────


def test_payload_matches_documented_schema():
    payload = RunwaySeedanceProvider().build_payload(_req())
    assert payload == {
        "model": "seedance2_5",
        "promptImage": "https://x/start.png",
        "promptText": "p",
        "ratio": "480:854",       # 9:16 × 480p — enum 값 그대로
        "duration": 4,            # 정수 (문자열 아님)
        "audio": False,           # 기본 true — 명시적으로 끈다
    }


def test_end_frame_uses_keyframe_positions():
    payload = RunwaySeedanceProvider().build_payload(
        _req(end_image_url="https://x/end.png", end_image_bytes=b"y")
    )
    assert payload["promptImage"] == [
        {"uri": "https://x/start.png", "position": "first"},
        {"uri": "https://x/end.png", "position": "last"},
    ]


@pytest.mark.parametrize(
    "aspect,res,ratio",
    [
        ("9:16", "480p", "480:854"),
        ("9:16", "720p", "720:1280"),
        ("9:16", "1080p", "1080:1920"),
        ("16:9", "720p", "1280:720"),
        ("1:1", "480p", "640:640"),
    ],
)
def test_ratio_mapping(aspect, res, ratio):
    payload = RunwaySeedanceProvider().build_payload(
        _req(output_spec={**SPEC, "aspect_ratio": aspect, "resolution": res})
    )
    assert payload["ratio"] == ratio


def test_unmapped_ratio_is_contract_violation_before_http():
    with pytest.raises(VideoProviderError) as e:
        RunwaySeedanceProvider().build_payload(
            _req(output_spec={**SPEC, "aspect_ratio": "4:3", "resolution": "480p"})
        )
    assert e.value.code == "PROVIDER_CONTRACT"


def test_duration_bounds_are_contract_violations():
    for bad in (3, 31):
        with pytest.raises(VideoProviderError) as e:
            RunwaySeedanceProvider().build_payload(_req(output_spec={**SPEC, "duration_sec": bad}))
        assert e.value.code == "PROVIDER_CONTRACT"
    # generate() 경로에서도 키/HTTP 이전에 잡힌다.
    with pytest.raises(VideoProviderError) as e2:
        RunwaySeedanceProvider().generate(_req(output_spec={**SPEC, "duration_sec": 3}))
    assert e2.value.code == "PROVIDER_CONTRACT"


# ── generate 흐름 (가짜 httpx) ──────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _flow(monkeypatch, task_body: dict):
    import httpx

    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    submitted: dict = {}

    def fake_post(url, **k):
        submitted["url"] = url
        submitted["json"] = k.get("json")
        submitted["headers"] = k.get("headers")
        return _Resp(200, {"id": "11111111-2222-4333-8444-555555555555"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp(200, task_body))
    return submitted


def test_generate_succeeded_parses_output_and_cost(monkeypatch):
    submitted = _flow(
        monkeypatch,
        {"id": "t", "status": "SUCCEEDED", "output": ["https://cdn/v.mp4"], "cost": {"credits": 25}},
    )
    monkeypatch.setattr(vp, "_download", lambda url: b"MP4BYTES")
    result = RunwaySeedanceProvider().generate(_req())
    assert result.video_bytes == b"MP4BYTES"
    assert result.external_job_id == "11111111-2222-4333-8444-555555555555"
    assert result.usage["cost"] == {"credits": 25} and "latency_sec" in result.usage
    assert submitted["url"].endswith("/v1/image_to_video")
    assert submitted["headers"]["X-Runway-Version"] == "2024-11-06"
    assert submitted["json"]["model"] == "seedance2_5"


def test_generate_failed_surfaces_failure_code(monkeypatch):
    _flow(monkeypatch, {"id": "t", "status": "FAILED",
                        "failure": "Something went wrong", "failureCode": "INTERNAL.BAD_OUTPUT.CODE01"})
    with pytest.raises(VideoProviderError) as e:
        RunwaySeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_FAILED" and "INTERNAL.BAD_OUTPUT.CODE01" in e.value.message


def test_generate_succeeded_without_output_is_schema_error(monkeypatch):
    _flow(monkeypatch, {"id": "t", "status": "SUCCEEDED", "output": []})
    with pytest.raises(VideoProviderError) as e:
        RunwaySeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_SCHEMA"


def test_submit_4xx_is_rejection(monkeypatch):
    import httpx

    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _Resp(400, text="bad request"))
    with pytest.raises(VideoProviderError) as e:
        RunwaySeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_REJECTED"


# ── 트랜스포트 선택 ─────────────────────────────────────────────────────────


def test_auto_prefers_runway_for_seedance_when_key_present(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    assert vp.transport_for("seedance") == "runway"
    assert isinstance(vp.get_provider("seedance"), RunwaySeedanceProvider)
    # Kling 은 Runway 에 없다 — fal 유지.
    assert vp.transport_for("kling") == "fal"


def test_fal_seedance_preserved_behind_explicit_env(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "fal")
    assert isinstance(vp.get_provider("seedance"), FalSeedanceProvider)


def test_auto_falls_back_to_fal_then_direct_without_runway_key(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    assert vp.transport_for("seedance") == "fal"
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert vp.transport_for("seedance") == "direct"


def test_routing_table_unchanged_with_runway_transport(monkeypatch):
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-test-key")
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    names = {c: [p.name for p in vp.routing_for_class(c)] for c in
             ("MICRO", "TRANSITION", "LOCOMOTION", "INTERACTION")}
    assert names == {
        "MICRO": ["seedance", "kling"],
        "TRANSITION": ["kling"],
        "LOCOMOTION": ["seedance", "kling"],
        "INTERACTION": ["kling", "seedance"],
    }
    # MICRO primary 는 Runway 트랜스포트의 seedance2_5 다.
    micro = vp.routing_for_class("MICRO")
    assert isinstance(micro[0], RunwaySeedanceProvider)
    assert micro[0].model_name() == "seedance2_5"
    assert micro[0].supports_end_frame is True
