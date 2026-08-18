"""
실패한 누끼가 유료 영상 생성으로 넘어가지 못하게 막는 게이트.

/api/generate-pet-video 는 Luma 를 호출하는(=과금되는) 엔드포인트다.
여기서 검증하는 것은 "생성 호출 전에 멈추는가" 이다.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from backend.services import vitmatte_service as vs
from backend.services.cutout_errors import AlphaEmptyError, SubjectNotDetectedError

from .conftest import ASGITestClient, make_jpeg_bytes, make_rgba_png_bytes


# --------------------------------------------------------------------------
# validate_cutout_alpha 단위
# --------------------------------------------------------------------------


def test_validate_cutout_alpha_rejects_fully_transparent():
    with pytest.raises(AlphaEmptyError) as exc:
        vs.validate_cutout_alpha(make_rgba_png_bytes(0.0))
    assert exc.value.code == "CUTOUT_ALPHA_EMPTY"


def test_validate_cutout_alpha_accepts_normal_cutout():
    meta = vs.validate_cutout_alpha(make_rgba_png_bytes(0.4))
    assert meta["alpha_checked"] is True
    assert meta["alpha_area_fraction"] == pytest.approx(0.4, abs=0.02)


def test_validate_cutout_alpha_skips_images_without_alpha():
    """목업/테스트 경로가 JPEG 를 보내는 경우 — 검증 불가로 표시하고 통과."""
    meta = vs.validate_cutout_alpha(make_jpeg_bytes())
    assert meta["alpha_checked"] is False


# --------------------------------------------------------------------------
# /api/generate-pet-video 게이트
# --------------------------------------------------------------------------


@pytest.fixture
def generate_client(monkeypatch) -> ASGITestClient:
    from backend.routers import generate as generate_router

    app = FastAPI()
    app.include_router(generate_router.router, prefix="/api")
    client = ASGITestClient(app)
    client.calls = {"luma": 0, "upload": 0}

    async def fake_upload(path, data, content_type):
        client.calls["upload"] += 1
        return f"https://storage.test/{path}"

    async def fake_luma(*args, **kwargs):
        client.calls["luma"] += 1
        return "https://luma.test/video.mp4"

    monkeypatch.setattr(generate_router.supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(generate_router, "create_generation_and_get_video_url", fake_luma)
    return client


def test_generate_pet_video_rejects_empty_alpha_before_luma(generate_client):
    """완전 투명한 누끼는 Luma 호출·업로드 이전에 422 로 막힌다."""
    res = generate_client.post(
        "/api/generate-pet-video",
        files={"file": ("cutout.png", make_rgba_png_bytes(0.0), "image/png")},
        data={"skip_preprocessing": "true", "idle_only": "true"},
    )

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "CUTOUT_ALPHA_EMPTY"
    assert generate_client.calls["luma"] == 0
    assert generate_client.calls["upload"] == 0


def test_generate_pet_video_rejects_undetected_subject_before_luma(
    generate_client, monkeypatch
):
    """skip_preprocessing=false 경로에서 개를 못 찾으면 422, Luma 호출 없음."""
    from backend.routers import generate as generate_router

    def boom(raw):
        raise SubjectNotDetectedError("No supported pet was detected in the image.")

    monkeypatch.setattr(generate_router, "build_dog_only_nobg_png_bytes", boom)

    res = generate_client.post(
        "/api/generate-pet-video",
        files={"file": ("photo.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"skip_preprocessing": "false", "idle_only": "true"},
    )

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "SUBJECT_NOT_DETECTED"
    assert generate_client.calls["luma"] == 0
    assert generate_client.calls["upload"] == 0


def test_generate_pet_video_empty_file_is_400(generate_client):
    res = generate_client.post(
        "/api/generate-pet-video",
        files={"file": ("cutout.png", b"", "image/png")},
        data={"skip_preprocessing": "true"},
    )
    assert res.status_code == 400


# --------------------------------------------------------------------------
# dog_image_preprocessing 폴백 제거
# --------------------------------------------------------------------------


def test_dog_preprocessing_raises_instead_of_full_frame_fallback(monkeypatch):
    """개 미검출 시 전체 프레임 rembg 로 조용히 폴백하지 않는다."""
    from backend.services import dog_image_preprocessing as dp

    called = {"rembg": 0}

    def fake_remove_background(*args, **kwargs):
        called["rembg"] += 1
        return make_rgba_png_bytes(0.5)

    monkeypatch.setattr(dp, "load_yolo", lambda name: _NoDetectionYolo())
    monkeypatch.setattr(dp, "remove_background", fake_remove_background)
    monkeypatch.setattr(dp, "is_black_tan_dog", lambda b: False)

    with pytest.raises(SubjectNotDetectedError) as exc:
        dp.build_dog_only_nobg_png_bytes(make_jpeg_bytes())

    assert exc.value.code == "SUBJECT_NOT_DETECTED"
    assert exc.value.diagnostics["pipeline"] == "dog_only_rembg"
    assert called["rembg"] == 0, "폴백 rembg 가 호출되면 안 됩니다"


def test_dog_preprocessing_dev_fallback_is_opt_in(monkeypatch):
    """개발용 플래그를 켰을 때만 예전 전체 프레임 폴백이 살아난다."""
    from backend.services import dog_image_preprocessing as dp

    called = {"rembg": 0}

    def fake_remove_background(*args, **kwargs):
        called["rembg"] += 1
        return make_rgba_png_bytes(0.5)

    monkeypatch.setenv("PET_PREPROCESS_ALLOW_FULLFRAME_FALLBACK", "1")
    monkeypatch.setattr(dp, "load_yolo", lambda name: _NoDetectionYolo())
    monkeypatch.setattr(dp, "remove_background", fake_remove_background)
    monkeypatch.setattr(dp, "is_black_tan_dog", lambda b: False)
    monkeypatch.setattr(dp, "replace_background_for_rembg", lambda rgb, target: rgb)

    out = dp.build_dog_only_nobg_png_bytes(make_jpeg_bytes())

    assert called["rembg"] == 1
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_dog_preprocessing_uses_pil_source_for_yolo(monkeypatch):
    """ndarray 가 아니라 PIL 이미지를 YOLO 에 넘기는지 (RGB/BGR 회귀 방지)."""
    from PIL import Image

    from backend.services import dog_image_preprocessing as dp

    yolo = _NoDetectionYolo()
    monkeypatch.setattr(dp, "load_yolo", lambda name: yolo)
    monkeypatch.setattr(dp, "is_black_tan_dog", lambda b: False)

    with pytest.raises(SubjectNotDetectedError):
        dp.build_dog_only_nobg_png_bytes(make_jpeg_bytes())

    assert yolo.calls, "YOLO predict 가 호출되지 않았습니다"
    assert isinstance(yolo.calls[0]["source"], Image.Image)


class _NoDetectionYolo:
    names: dict = {}

    def __init__(self):
        self.calls: list[dict] = []

    def predict(self, source=None, **kwargs):
        self.calls.append({"source": source, **kwargs})
        return [_EmptyResult()]


class _EmptyResult:
    boxes = None
