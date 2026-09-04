"""
fal 트랜스포트 (Phase 6.5 라이브 스택) 테스트 — 실 네트워크 호출 없음.

계약: 프로바이더 이름/라우팅은 불변, 트랜스포트만 env 로 갈린다.
"""

from __future__ import annotations

import pytest

from backend.services import video_motion_providers as vp
from backend.services.video_motion_providers import (
    FalKlingProvider,
    FalSeedanceProvider,
    KlingProvider,
    MotionVideoRequest,
    SeedanceProvider,
)

SPEC = {"aspect_ratio": "9:16", "resolution": "720p", "duration_sec": 5, "audio": False, "camera_fixed": True}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("FAL_KEY", "FAL_API_KEY", "SEEDANCE_API_KEY", "ARK_API_KEY",
                "KLING_ACCESS_KEY", "KLING_SECRET_KEY", "SEEDANCE_TRANSPORT",
                "KLING_TRANSPORT", "PHASE6_VIDEO_TRANSPORT", "VIDEO_GENERATION_MOCK",
                "RUNWAY_API_KEY", "FAL_INPUT_TRANSPORT"):
        monkeypatch.delenv(var, raising=False)


def test_auto_transport_prefers_fal_when_key_present(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    assert vp.transport_for("seedance") == "fal"
    assert isinstance(vp.get_provider("seedance"), FalSeedanceProvider)
    assert isinstance(vp.get_provider("kling"), FalKlingProvider)
    assert vp.get_provider("seedance").available() is True


def test_auto_transport_falls_back_to_direct_without_fal_key():
    assert vp.transport_for("seedance") == "direct"
    assert isinstance(vp.get_provider("seedance"), SeedanceProvider)
    assert isinstance(vp.get_provider("kling"), KlingProvider)


def test_explicit_transport_overrides(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setenv("SEEDANCE_TRANSPORT", "direct")
    assert isinstance(vp.get_provider("seedance"), SeedanceProvider)
    assert isinstance(vp.get_provider("kling"), FalKlingProvider)  # 개별 지정만 direct

    monkeypatch.setenv("PHASE6_VIDEO_TRANSPORT", "direct")
    monkeypatch.delenv("SEEDANCE_TRANSPORT", raising=False)
    assert isinstance(vp.get_provider("kling"), KlingProvider)


def test_routing_names_unchanged_regardless_of_transport(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    names = {c: [p.name for p in vp.routing_for_class(c)] for c in
             ("MICRO", "TRANSITION", "LOCOMOTION", "INTERACTION")}
    assert names == {
        "MICRO": ["seedance", "kling"],
        "TRANSITION": ["kling"],
        "LOCOMOTION": ["seedance", "kling"],
        "INTERACTION": ["kling", "seedance"],
    }


def test_fal_payloads_match_documented_contracts():
    """라이브 검증으로 고정한 실 fal 스키마 — 필드 집합까지 정확히."""
    req = MotionVideoRequest(
        prompt="p", start_image_url="https://x/start.png", start_image_bytes=b"x",
        end_image_url="https://x/end.png", end_image_bytes=b"y", output_spec=SPEC,
    )
    sd = FalSeedanceProvider().build_payload(req)
    assert sd == {
        "prompt": "p",
        "image_url": "https://x/start.png",
        "resolution": "720p",
        "duration": "5",
        "generate_audio": False,          # 기본 true — 반드시 명시적으로 끈다
        "end_image_url": "https://x/end.png",  # end frame 을 버리지 않는다
    }
    # I2V 는 aspect_ratio 가 항상 auto (앵커 이미지가 기하 결정), camera_fixed 는 스키마에 없다.
    assert "aspect_ratio" not in sd and "camera_fixed" not in sd

    kl = FalKlingProvider().build_payload(req)
    assert kl == {
        "prompt": "p",
        "start_image_url": "https://x/start.png",  # image_url 이 아니다
        "duration": "5",
        "generate_audio": False,
        "end_image_url": "https://x/end.png",      # tail_image_url 이 아니다
    }
    assert "aspect_ratio" not in kl

    # end frame 없는 요청에는 해당 필드 자체가 없다.
    req2 = MotionVideoRequest(prompt="p", start_image_url="https://x/s.png", start_image_bytes=b"x", output_spec=SPEC)
    assert "end_image_url" not in FalSeedanceProvider().build_payload(req2)
    assert "end_image_url" not in FalKlingProvider().build_payload(req2)


def test_fal_default_models_are_real_endpoints():
    assert FalSeedanceProvider().model_name() == "bytedance/seedance-2.5/image-to-video"
    assert FalKlingProvider().model_name() == "fal-ai/kling-video/v3/standard/image-to-video"


def test_seedance_duration_below_contract_minimum_raises_locally(monkeypatch):
    """Seedance 2.5 duration 하한 4s — 과금 호출 전에 로컬에서 계약 위반으로 차단."""
    import pytest as _pytest

    from backend.services.video_motion_providers import VideoProviderError

    req = MotionVideoRequest(
        prompt="p", start_image_url="https://x/s.png", start_image_bytes=b"x",
        output_spec={**SPEC, "duration_sec": 3},
    )
    with _pytest.raises(VideoProviderError) as e:
        FalSeedanceProvider().build_payload(req)
    assert e.value.code == "PROVIDER_CONTRACT"
    # generate() 경로에서도 키/HTTP 이전에 잡힌다 (FAL_KEY 조차 필요 없다).
    with _pytest.raises(VideoProviderError) as e2:
        FalSeedanceProvider().generate(req)
    assert e2.value.code == "PROVIDER_CONTRACT"


def test_fal_models_are_env_overridable(monkeypatch):
    monkeypatch.setenv("FAL_SEEDANCE_MODEL", "fal-ai/bytedance/seedance/v9/custom")
    monkeypatch.setenv("FAL_KLING_MODEL", "fal-ai/kling-video/v9/custom")
    assert FalSeedanceProvider().model_name() == "fal-ai/bytedance/seedance/v9/custom"
    assert FalKlingProvider().model_name() == "fal-ai/kling-video/v9/custom"


# ── 결과 파싱 (문서화된 출력 스키마) ─────────────────────────────────────────


def test_extract_fal_video_url_documented_and_defensive():
    assert vp.extract_fal_video_url({"video": {"url": "https://v"}, "seed": 1}) == "https://v"
    assert vp.extract_fal_video_url({"videos": [{"url": "https://v2"}]}) == "https://v2"
    assert vp.extract_fal_video_url({}) == ""
    assert vp.extract_fal_video_url({"video": "https://not-a-dict"}) == ""


def test_sanitize_json_shape_hides_values():
    shape = vp.sanitize_json_shape({"video": {"url": "https://secret"}, "seed": 7})
    assert "secret" not in str(shape) and "7" not in str(shape)
    assert shape == {"video": {"url": "str"}, "seed": "int"}


class _Resp:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _queue_flow(monkeypatch, result_resp: _Resp):
    """POST → request_id, status COMPLETED, 결과는 result_resp."""
    import httpx

    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _Resp(200, {"request_id": "rid", "status_url": "https://q/st", "response_url": "https://q/res"}),
    )

    def fake_get(url, **k):
        if url == "https://q/st":
            return _Resp(200, {"status": "COMPLETED"})
        return result_resp

    monkeypatch.setattr(httpx, "get", fake_get)


def _req():
    return MotionVideoRequest(
        prompt="p", start_image_url="https://x/s.png", start_image_bytes=b"x", output_spec=SPEC
    )


def test_fal_result_fetch_http_error_is_transport_not_empty(monkeypatch):
    """결과 조회 실패를 빈 결과로 삼키지 않는다 (BREATHING V2 오진 재발 방지)."""
    from backend.services.video_motion_providers import VideoProviderError

    _queue_flow(monkeypatch, _Resp(500, {}, text="boom"))
    with pytest.raises(VideoProviderError) as e:
        FalSeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_TRANSPORT" and "500" in e.value.message


def test_fal_result_fetch_4xx_is_provider_rejection(monkeypatch):
    """422 콘텐츠 모더레이션 (BREATHING V3 라이브 사례) — 전송 장애가 아니라 거절."""
    from backend.services.video_motion_providers import VideoProviderError

    _queue_flow(
        monkeypatch,
        _Resp(422, {"detail": [{"type": "content_policy_violation"}]},
              text='{"detail":[{"type":"content_policy_violation"}]}'),
    )
    with pytest.raises(VideoProviderError) as e:
        FalSeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_REJECTED" and "content_policy_violation" in e.value.message


def test_fal_undocumented_result_shape_stops_with_sanitized_shape(monkeypatch):
    from backend.services.video_motion_providers import VideoProviderError

    _queue_flow(monkeypatch, _Resp(200, {"outputs": {"clip": "https://secret-url"}}))
    with pytest.raises(VideoProviderError) as e:
        FalSeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_SCHEMA"
    assert "outputs" in e.value.message and "secret-url" not in e.value.message


def test_fal_storage_input_transport_uploads_and_uses_file_url(monkeypatch):
    """FAL_INPUT_TRANSPORT=fal_storage — initiate → PUT → file_url 이 image_url 이 된다."""
    import httpx

    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setenv("FAL_INPUT_TRANSPORT", "fal_storage")
    posted: dict = {}

    def fake_post(url, **k):
        if "storage/upload/initiate" in url:
            return _Resp(200, {"upload_url": "https://up/put-here", "file_url": "https://v3.fal.media/files/x/start.png"})
        posted["url"] = url
        posted["json"] = k.get("json")
        return _Resp(200, {"request_id": "rid", "status_url": "https://q/st", "response_url": "https://q/res"})

    def fake_get(url, **k):
        if url == "https://q/st":
            return _Resp(200, {"status": "COMPLETED"})
        return _Resp(200, {"video": {"url": "https://cdn/v.mp4"}, "seed": 1})

    puts: list = []
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "put", lambda url, **k: (puts.append((url, k.get("content"))), _Resp(200))[1])
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(vp, "_download", lambda url: b"MP4BYTES")

    result = FalSeedanceProvider().generate(_req())
    assert result.video_bytes == b"MP4BYTES"
    assert puts == [("https://up/put-here", b"x")]  # 정확히 원본 바이트가 올라간다
    assert posted["json"]["image_url"] == "https://v3.fal.media/files/x/start.png"


def test_fal_storage_initiate_schema_mismatch_stops(monkeypatch):
    import httpx

    from backend.services.video_motion_providers import VideoProviderError

    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    monkeypatch.setenv("FAL_INPUT_TRANSPORT", "fal_storage")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _Resp(200, {"unexpected": "shape"}))
    with pytest.raises(VideoProviderError) as e:
        FalSeedanceProvider().generate(_req())
    assert e.value.code == "PROVIDER_SCHEMA"


def test_default_input_transport_does_not_touch_fal_storage(monkeypatch):
    """기본(signed_url)에서는 스토리지 initiate 호출 자체가 없다."""
    import httpx

    _queue_flow(monkeypatch, _Resp(200, {"video": {"url": "https://cdn/v.mp4"}, "seed": 1}))
    real_fake_post = httpx.post

    def guarded_post(url, **k):
        assert "storage/upload" not in url
        return real_fake_post(url, **k)

    monkeypatch.setattr(httpx, "post", guarded_post)
    monkeypatch.setattr(vp, "_download", lambda url: b"MP4BYTES")
    result = FalSeedanceProvider().generate(_req())
    assert result.external_job_id == "rid"


def test_fal_documented_result_parses_and_downloads(monkeypatch):
    _queue_flow(monkeypatch, _Resp(200, {"video": {"url": "https://cdn/v.mp4"}, "seed": 42}))
    monkeypatch.setattr(vp, "_download", lambda url: b"MP4BYTES")
    result = FalSeedanceProvider().generate(_req())
    assert result.video_bytes == b"MP4BYTES"
    assert result.external_job_id == "rid"
    assert result.usage["seed"] == 42 and "latency_sec" in result.usage


def test_fal_end_frame_capability_env_gate(monkeypatch):
    assert FalSeedanceProvider().supports_end_frame is True
    monkeypatch.setenv("FAL_SEEDANCE_SUPPORTS_END_FRAME", "0")
    assert FalSeedanceProvider().supports_end_frame is False
    assert FalKlingProvider().supports_end_frame is True
