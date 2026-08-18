"""
누끼 라우터 계약 테스트.

- 실패는 HTTP 200 + {"error": ...} 가 아니라 422 + {"detail": {"code", ...}}
- `refined` 는 실제로 정제가 일어났을 때만 true
- 실패한 누끼는 Storage 업로드로 넘어가지 않는다
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi import FastAPI

from backend.routers import cutout as cutout_router
from backend.routers import matting as matting_router
from backend.services import vitmatte_service as vs
from backend.services.cutout_errors import MaskTooSmallError, SubjectNotDetectedError

from .conftest import ASGITestClient, blob_mask, make_jpeg_bytes, make_rgba_png_bytes


def _client_for(router_module, monkeypatch) -> ASGITestClient:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api")
    client = ASGITestClient(app)

    async def fake_upload(path, data, content_type):
        client.uploads.append(path)
        return f"https://storage.test/{path}"

    async def fake_row(*args, **kwargs):
        return None

    monkeypatch.setattr(router_module.supabase_assets, "upload_asset_to_storage", fake_upload)
    monkeypatch.setattr(router_module.supabase_assets, "ensure_user_asset_row", fake_row)
    return client


@pytest.fixture
def matting_client(monkeypatch) -> ASGITestClient:
    return _client_for(matting_router, monkeypatch)


@pytest.fixture
def cutout_client(monkeypatch) -> ASGITestClient:
    return _client_for(cutout_router, monkeypatch)


def _files(name: str = "photo.jpg"):
    return {"file": (name, make_jpeg_bytes(), "image/jpeg")}


# --------------------------------------------------------------------------
# /api/matting/cutout
# --------------------------------------------------------------------------


def test_matting_no_subject_returns_422(matting_client, monkeypatch):
    def boom(raw, **kwargs):
        raise SubjectNotDetectedError(
            "No supported pet was detected in the image.",
            diagnostics={"subject_detected": False},
        )

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", boom)

    res = matting_client.post("/api/matting/cutout", files=_files())

    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "SUBJECT_NOT_DETECTED"
    assert "message" in detail
    # 실패했으니 Storage 업로드는 없어야 한다.
    assert matting_client.uploads == []


def test_matting_mask_too_small_returns_422_with_code(matting_client, monkeypatch):
    def boom(raw, **kwargs):
        raise MaskTooSmallError("too small", diagnostics={"mask_area_fraction": 0.001})

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", boom)

    res = matting_client.post("/api/matting/cutout", files=_files())

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "CUTOUT_MASK_TOO_SMALL"


def test_matting_does_not_leak_traceback_in_production(matting_client, monkeypatch):
    def boom(raw, **kwargs):
        raise ValueError("internal detail that must not leak")

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", boom)
    monkeypatch.setattr(matting_router, "DEBUG_ARTIFACTS_ENABLED", False)

    res = matting_client.post("/api/matting/cutout", files=_files())

    assert res.status_code == 500
    body = res.text
    assert "internal detail that must not leak" not in body
    assert res.json()["detail"]["code"] == "CUTOUT_INTERNAL_ERROR"


def test_matting_success_reports_vitmatte_single_pass(matting_client, monkeypatch):
    png = make_rgba_png_bytes(0.4)

    def ok(raw, **kwargs):
        return png, {
            "method": "vitmatte",
            "subject_detected": True,
            "subject_class": "dog",
            "segmenter_used": "sam2",
            "segmenter": "sam2",
        }

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", ok)

    res = matting_client.post(
        "/api/matting/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    assert res.status_code == 200
    body = res.json()
    q = body["cutout_quality"]
    assert body["error"] is None
    assert body["subject_detected"] is True
    assert q["refined"] is True
    assert q["refinement_type"] == "vitmatte"
    # ViTMatte 는 단일 패스다 — rembg 처럼 2차 패스를 돌았다고 주장하면 안 된다.
    assert q["second_pass"] is False
    assert body["cutout_png_base64"]


def test_matting_debug_flag_ignored_when_disabled(matting_client, monkeypatch):
    png = make_rgba_png_bytes(0.4)
    seen: dict = {}

    def ok(raw, **kwargs):
        seen["debug_artifacts"] = kwargs.get("debug_artifacts")
        return png, {"subject_detected": True}

    monkeypatch.setattr(matting_router, "matte_foreground_with_meta", ok)
    monkeypatch.setattr(matting_router, "DEBUG_ARTIFACTS_ENABLED", False)

    res = matting_client.post(
        "/api/matting/cutout",
        files=_files(),
        data={"save_to_storage": "false", "debug": "true"},
    )

    assert res.status_code == 200
    assert seen["debug_artifacts"] is None
    assert "debug_artifacts" not in res.json()


# --------------------------------------------------------------------------
# /api/cutout (rembg)
# --------------------------------------------------------------------------


def test_cutout_adaptive_reports_second_pass_truthfully(cutout_client, monkeypatch):
    """2차 매팅 패스가 실제로 돌았을 때만 second_pass=True."""
    png = make_rgba_png_bytes(0.4)

    monkeypatch.setattr(cutout_router, "_fast_cutout", lambda raw, m, meta_out=None: png)
    monkeypatch.setattr(
        cutout_router, "analyze_alpha_fur_edge", lambda p: {"needs_refinement": True}
    )

    def fake_refine(raw, model_name, meta_out=None):
        if meta_out is not None:
            meta_out["alpha_matting_used"] = True
        return png

    monkeypatch.setattr(cutout_router, "_refined_cutout", fake_refine)

    res = cutout_client.post(
        "/api/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    q = res.json()["cutout_quality"]
    assert q["refined"] is True
    assert q["refinement_type"] == "rembg_alpha_matting"
    assert q["second_pass"] is True
    assert q["cutout_pass"] == "fast_then_matting"


def test_cutout_adaptive_without_refinement_reports_refined_false(cutout_client, monkeypatch):
    png = make_rgba_png_bytes(0.4)

    monkeypatch.setattr(cutout_router, "_fast_cutout", lambda raw, m, meta_out=None: png)
    monkeypatch.setattr(
        cutout_router, "analyze_alpha_fur_edge", lambda p: {"needs_refinement": False}
    )

    res = cutout_client.post(
        "/api/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    q = res.json()["cutout_quality"]
    assert q["refined"] is False
    assert q["refinement_type"] is None
    assert q["second_pass"] is False


def test_cutout_refine_pass_without_alpha_matting_is_not_called_refined(
    cutout_client, monkeypatch
):
    """2차 패스는 돌았지만 메모리 예산 때문에 알파 매팅이 꺼진 경우."""
    png = make_rgba_png_bytes(0.4)

    monkeypatch.setattr(cutout_router, "_fast_cutout", lambda raw, m, meta_out=None: png)
    monkeypatch.setattr(
        cutout_router, "analyze_alpha_fur_edge", lambda p: {"needs_refinement": True}
    )

    def fake_refine(raw, model_name, meta_out=None):
        if meta_out is not None:
            meta_out["alpha_matting_used"] = False  # OOM 회피로 꺼짐
        return png

    monkeypatch.setattr(cutout_router, "_refined_cutout", fake_refine)

    res = cutout_client.post(
        "/api/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    q = res.json()["cutout_quality"]
    assert q["refined"] is False
    assert q["refinement_type"] is None


def test_cutout_pet_only_subject_not_detected_returns_422(cutout_client, monkeypatch):
    """pet_only 경로의 미검출이 조용히 전체 프레임 rembg 로 새지 않는지."""

    def boom(raw, model_name, pet_only):
        raise SubjectNotDetectedError("No supported pet was detected in the image.")

    monkeypatch.setattr(cutout_router, "_run_cutout", boom)

    res = cutout_client.post(
        "/api/cutout",
        files=_files(),
        data={"pet_only": "true", "auto_refine": "false", "save_to_storage": "true"},
    )

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "SUBJECT_NOT_DETECTED"
    assert cutout_client.uploads == []


def test_cutout_empty_file_is_400(cutout_client):
    res = cutout_client.post("/api/cutout", files={"file": ("x.jpg", b"", "image/jpeg")})
    assert res.status_code == 400


# --------------------------------------------------------------------------
# 실제 파이프라인을 통과한 422 (라우터 + 서비스 결합)
# --------------------------------------------------------------------------


def test_matting_end_to_end_rejection_uses_real_pipeline(matting_client, monkeypatch):
    """서비스가 던진 예외가 라우터에서 422 코드로 그대로 매핑되는지."""
    monkeypatch.setattr(vs, "_detect_subject", lambda *a, **k: None)

    res = matting_client.post(
        "/api/matting/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "SUBJECT_NOT_DETECTED"
    assert matting_client.uploads == []


def test_matting_end_to_end_rectangle_rejection(matting_client, monkeypatch):
    rect = blob_mask(96, 128, area_fraction=0.3, rectangle=True)

    monkeypatch.setattr(
        vs,
        "_detect_subject",
        lambda *a, **k: vs.SubjectDetection((10, 10, 90, 70), 16, "dog", 0.9),
    )
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda rgb, bbox, **kw: vs.SegmentationOutcome(
            fg_binary=rect,
            trimap=np.where(rect > 0, 255, 0).astype(np.uint8),
            segmenter_used="grabcut",
            fallback=True,
            fallback_reason="sam2_failed",
        ),
    )

    res = matting_client.post(
        "/api/matting/cutout", files=_files(), data={"save_to_storage": "false"}
    )

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "CUTOUT_RECTANGLE_LIKE"
