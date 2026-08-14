"""
프로바이더 디스패처 테스트 — 실제 fal/Luma 호출은 전부 목업. 과금 없음.

검증 대상:
  1) VIDEO_PROVIDER 미설정 → luma
  2) VIDEO_PROVIDER=luma   → 기존 luma_service
  3) VIDEO_PROVIDER=wan    → wan_service
  4) 정규화된 반환값(문자열 URL)이 기존 호출부 계약과 같음
  5) 확장자 없는 fal URL 이 재생성(추가 과금)을 유발하지 않음
  6) Wan 요청이 480p 를 사용
  7) Wan 요청이 9:16 을 사용 (세로 유지)
  8) 검증기는 평범한 다운로드 바이트만 받음 (fal 객체 노출 없음)
  10) wan → luma 전환이 소스 수정 없이 환경변수만으로 됨
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import video_generation as vg
from backend.services import wan_service


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch):
    for key in (
        "VIDEO_PROVIDER", "FAL_KEY", "FAL_API_KEY", "WAN_MODEL",
        "WAN_RESOLUTION", "WAN_ASPECT_RATIO", "WAN_QUEUE_BASE", "LUMA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


# ── 1~3, 10: 디스패치 ────────────────────────────────────────────────────────


def test_provider_defaults_to_luma_when_unset():
    assert vg.get_video_provider() == vg.PROVIDER_LUMA
    assert vg.is_luma() is True


def test_provider_explicit_luma(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "luma")
    assert vg.get_video_provider() == vg.PROVIDER_LUMA


def test_provider_wan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "wan")
    assert vg.get_video_provider() == vg.PROVIDER_WAN
    assert vg.is_luma() is False


def test_unknown_provider_raises_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch):
    """오타는 조용히 luma 로 흘러가지 않고 즉시 실패해야 한다."""
    monkeypatch.setenv("VIDEO_PROVIDER", "wanna")
    with pytest.raises(vg.UnknownVideoProviderError, match="wanna"):
        vg.get_video_provider()


def test_blank_provider_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch):
    """공백만 있는 값은 '미설정'과 같게 본다 (하위 호환)."""
    monkeypatch.setenv("VIDEO_PROVIDER", "   ")
    assert vg.get_video_provider() == vg.PROVIDER_LUMA


def test_invalid_provider_makes_zero_luma_and_zero_wan_calls(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    핵심 안전 테스트: VIDEO_PROVIDER 오타 시 **어떤 프로바이더도 호출되지 않는다**.
    Luma 크레딧도 fal 크레딧도 소모되지 않아야 한다.
    """
    calls = {"luma": 0, "wan": 0}

    async def fake_luma(*args, **kwargs):
        calls["luma"] += 1
        return "https://cdn.luma/should-never-happen.mp4"

    async def fake_wan(*args, **kwargs):
        calls["wan"] += 1
        return "https://fal/should-never-happen"

    monkeypatch.setattr(
        "backend.services.luma_service.create_generation_and_get_video_url", fake_luma
    )
    monkeypatch.setattr(
        "backend.services.wan_service.create_generation_and_get_video_url", fake_wan
    )
    monkeypatch.setenv("VIDEO_PROVIDER", "invalid-provider")

    with pytest.raises(vg.UnknownVideoProviderError):
        anyio.run(vg.create_generation_and_get_video_url, "https://img", "prompt")

    assert calls["luma"] == 0, "Luma 가 호출됐다 — 크레딧 소모 위험"
    assert calls["wan"] == 0, "Wan 이 호출됐다 — 크레딧 소모 위험"


def test_switching_back_to_luma_needs_only_env(monkeypatch: pytest.MonkeyPatch):
    """10) 같은 프로세스에서 환경변수만 바꿔 왕복 — 소스 수정 없음."""
    monkeypatch.setenv("VIDEO_PROVIDER", "wan")
    assert vg.get_video_provider() == "wan"
    monkeypatch.setenv("VIDEO_PROVIDER", "luma")
    assert vg.get_video_provider() == "luma"
    monkeypatch.delenv("VIDEO_PROVIDER")
    assert vg.get_video_provider() == "luma"


def test_dispatch_routes_to_luma_service(monkeypatch: pytest.MonkeyPatch):
    """2) luma 선택 시 luma_service 의 함수가 호출된다."""
    calls: list[tuple] = []

    async def fake_luma(image_url, prompt, **kwargs):
        calls.append((image_url, prompt, kwargs))
        return "https://cdn.luma/x.mp4"

    monkeypatch.setattr(
        "backend.services.luma_service.create_generation_and_get_video_url", fake_luma
    )
    monkeypatch.setenv("VIDEO_PROVIDER", "luma")

    url = anyio.run(vg.create_generation_and_get_video_url, "https://img", "prompt")
    assert url == "https://cdn.luma/x.mp4"
    assert len(calls) == 1
    # model/resolution 을 넘기지 않았으므로 luma_service 자신의 기본값이 유지된다.
    assert "model" not in calls[0][2]
    assert "resolution" not in calls[0][2]


def test_dispatch_routes_to_wan_service(monkeypatch: pytest.MonkeyPatch):
    """3) wan 선택 시 wan_service 가 호출되고, 반환은 문자열 URL(4)."""
    async def fake_wan(image_url, prompt, **kwargs):
        return "https://v3.fal.media/files/abc/output"

    monkeypatch.setattr(
        "backend.services.wan_service.create_generation_and_get_video_url", fake_wan
    )
    monkeypatch.setenv("VIDEO_PROVIDER", "wan")

    url = anyio.run(vg.create_generation_and_get_video_url, "https://img", "prompt")
    assert isinstance(url, str)
    assert url == "https://v3.fal.media/files/abc/output"


# ── 6~7: Wan 요청 파라미터 (480p / 9:16) ────────────────────────────────────


def test_wan_payload_defaults_to_480p_and_9_16():
    payload = wan_service.build_input_payload("https://img", "a dog breathing")
    assert payload["resolution"] == "480p"
    assert payload["aspect_ratio"] == "9:16", "세로 유지 — fal 기본값 auto 는 가로가 될 수 있다"
    assert payload["image_url"] == "https://img"
    assert payload["prompt"] == "a dog breathing"


def test_wan_payload_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WAN_RESOLUTION", "580p")
    monkeypatch.setenv("WAN_ASPECT_RATIO", "1:1")
    payload = wan_service.build_input_payload("https://img", "p")
    assert payload["resolution"] == "580p"
    assert payload["aspect_ratio"] == "1:1"


def test_wan_payload_omits_unsupported_turbo_params():
    """turbo 변형은 num_frames / frames_per_second 를 노출하지 않는다."""
    payload = wan_service.build_input_payload("https://img", "p")
    assert "num_frames" not in payload
    assert "frames_per_second" not in payload


def test_wan_submit_sends_480p_and_9_16_over_the_wire(monkeypatch: pytest.MonkeyPatch):
    """실제 POST 바디에 해상도/비율이 실려 나가는지 확인 (요청은 목업)."""
    sent: dict = {}

    class FakeResp:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "request_id": "req-1",
                "status_url": "https://queue.fal.run/s",
                "response_url": "https://queue.fal.run/r",
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"] = url
        sent["headers"] = headers
        sent["json"] = json
        return FakeResp()

    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(wan_service.requests, "post", fake_post)

    sub = anyio.run(wan_service.create_generation, "https://img", "prompt")
    assert sub.request_id == "req-1"
    assert sent["json"]["resolution"] == "480p"
    assert sent["json"]["aspect_ratio"] == "9:16"
    assert sent["headers"]["Authorization"] == "Key test-key"
    assert "wan/v2.2-a14b/image-to-video/turbo" in sent["url"]


def test_wan_requires_fal_key():
    with pytest.raises(RuntimeError, match="FAL_KEY"):
        anyio.run(wan_service.create_generation, "https://img", "p")


# ── 5: 확장자 없는 fal URL 이 재과금을 유발하지 않는다 ───────────────────────


def test_extensionless_fal_url_is_accepted_for_wan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "wan")
    assert vg.looks_like_video_url("https://v3.fal.media/files/abc/output") is True


def test_luma_url_check_is_unchanged(monkeypatch: pytest.MonkeyPatch):
    """Luma 는 예전 그대로 .mp4 접미사를 요구한다 (동작 보존)."""
    monkeypatch.setenv("VIDEO_PROVIDER", "luma")
    assert vg.looks_like_video_url("https://cdn.luma/x.mp4") is True
    assert vg.looks_like_video_url("https://cdn.luma/x.mp4?token=1") is True
    assert vg.looks_like_video_url("https://cdn.luma/x") is False


def test_extensionless_fal_url_does_not_trigger_a_second_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """
    5) 핵심 회귀 테스트.

    확장자 없는 fal URL 로 성공한 생성이 '.mp4 가 아니다'라는 이유로 버려지고
    재생성(추가 과금)이 돌면 안 된다. generate_idle_variant 를 끝까지 돌려
    생성 호출이 정확히 1회인지 센다.
    """
    from backend.services import luma_idle_pipeline as pipeline

    monkeypatch.setenv("VIDEO_PROVIDER", "wan")
    monkeypatch.setenv("IDLE_VALIDATION_ENABLED", "false")  # SSIM 은 이 테스트 관심사가 아님

    gen_calls = {"n": 0}

    async def fake_generate(image_url, prompt, **kwargs):
        gen_calls["n"] += 1
        return "https://v3.fal.media/files/zebra/output"   # 확장자 없음

    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    async def fake_download(url):
        return str(mp4)

    monkeypatch.setattr(pipeline, "create_generation_and_get_video_url", fake_generate)
    monkeypatch.setattr(pipeline, "download_video", fake_download)

    result = anyio.run(
        lambda: pipeline.generate_idle_variant(
            "https://img", "IDLE_BREATH", reference_image_bytes=None, max_retries=2
        )
    )

    assert gen_calls["n"] == 1, f"재생성이 발생했다 (추가 과금): {gen_calls['n']}회"
    assert result.retries_used == 0
    assert result.is_mp4 is True
    assert result.remote_video_url.endswith("/output")


# ── 8: 검증기에 넘어가는 것은 평범한 바이트뿐 ───────────────────────────────


def test_sniff_video_container():
    assert vg.sniff_video_container(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16) == "mp4"
    assert vg.sniff_video_container(b"\x1a\x45\xdf\xa3" + b"\x00" * 16) == "webm"
    assert vg.sniff_video_container(b"not a video at all") is None
    assert vg.sniff_video_container(b"") is None


def test_validator_receives_plain_bytes_not_provider_objects(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """8) validate_idle_video 는 bytes 만 받는다 — fal 응답 객체가 새지 않는다."""
    from backend.services import luma_idle_pipeline as pipeline

    monkeypatch.setenv("VIDEO_PROVIDER", "wan")
    monkeypatch.setenv("IDLE_VALIDATION_ENABLED", "true")

    seen: dict = {}

    def fake_validate(video_bytes, reference_bytes, **kwargs):
        seen["video_type"] = type(video_bytes)
        seen["ref_type"] = type(reference_bytes)
        from backend.services.idle_validation_service import IdleValidationResult

        return IdleValidationResult(True, 0.9, 0.9, False, False, "ok")

    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(payload)

    async def fake_generate(image_url, prompt, **kwargs):
        return "https://v3.fal.media/files/x/output"

    async def fake_download(url):
        return str(mp4)

    monkeypatch.setattr(pipeline, "create_generation_and_get_video_url", fake_generate)
    monkeypatch.setattr(pipeline, "download_video", fake_download)
    monkeypatch.setattr(pipeline, "validate_idle_video", fake_validate)

    anyio.run(
        lambda: pipeline.generate_idle_variant(
            "https://img", "IDLE_BREATH", reference_image_bytes=b"refbytes", max_retries=0
        )
    )
    assert seen["video_type"] is bytes
    assert seen["ref_type"] is bytes


def test_extract_video_url_shapes():
    assert wan_service.extract_video_url({"video": {"url": "https://a/b"}}) == "https://a/b"
    assert wan_service.extract_video_url({"videos": [{"url": "https://a/c"}]}) == "https://a/c"
    with pytest.raises(RuntimeError):
        wan_service.extract_video_url({"nope": 1})
