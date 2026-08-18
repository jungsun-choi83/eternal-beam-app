"""
vitmatte 파이프라인 Phase 1 동작 검증.

무거운 모델은 전부 목업한다 — 검증 대상은 추론 품질이 아니라
"제어 흐름과 진단 정보가 정직한가" 이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.services import vitmatte_service as vs
from backend.services.cutout_errors import (
    AlphaEmptyError,
    MaskTooLargeError,
    MaskTooSmallError,
    RectangleLikeMaskError,
    SubjectNotDetectedError,
)

from .conftest import blob_mask, make_jpeg_bytes

IMG_W, IMG_H = 128, 96


def _detection(bbox=(20, 15, 100, 80), conf=0.87, cls_id=16, name="dog"):
    return vs.SubjectDetection(bbox=bbox, class_id=cls_id, class_name=name, confidence=conf)


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    detection,
    mask: np.ndarray | None = None,
    alpha_fraction: float = 0.3,
    segmenter_outcome: vs.SegmentationOutcome | None = None,
):
    """검출 / 세그멘테이션 / ViTMatte 를 모두 목업으로 갈아끼운다."""
    calls: dict[str, list] = {"detect": [], "segment": [], "vitmatte": []}

    def fake_detect(image, yolo_model, *, conf):
        calls["detect"].append({"yolo_model": yolo_model, "conf": conf})
        return detection

    def fake_segment(rgb, prompt_bbox, **kwargs):
        calls["segment"].append({"prompt_bbox": prompt_bbox, **kwargs})
        if segmenter_outcome is not None:
            return segmenter_outcome
        fg = mask if mask is not None else blob_mask(IMG_H, IMG_W, area_fraction=0.3)
        return vs.SegmentationOutcome(
            fg_binary=fg,
            trimap=np.where(fg > 0, 255, 0).astype(np.uint8),
            segmenter_used="sam2",
            sam2_score=0.91,
        )

    def fake_vitmatte(rgb, trimap, model_name, device):
        calls["vitmatte"].append({"model_name": model_name, "device": device})
        alpha = np.zeros((IMG_H, IMG_W), dtype=np.float32)
        rows = int(round(IMG_H * alpha_fraction))
        if rows > 0:
            alpha[:rows, :] = 1.0
        return alpha

    monkeypatch.setattr(vs, "_detect_subject", fake_detect)
    monkeypatch.setattr(vs, "_segment_foreground", fake_segment)
    monkeypatch.setattr(vs, "_run_vitmatte", fake_vitmatte)
    return calls


# --------------------------------------------------------------------------
# 1. 피사체 미검출 → 422 (중앙 사각형 폴백 없음)
# --------------------------------------------------------------------------


def test_no_detection_raises_subject_not_detected(monkeypatch):
    calls = _install_pipeline(monkeypatch, detection=None)

    with pytest.raises(SubjectNotDetectedError) as exc:
        vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert exc.value.code == "SUBJECT_NOT_DETECTED"
    assert exc.value.http_status == 422
    diag = exc.value.diagnostics
    assert diag["subject_detected"] is False
    assert diag["raw_bbox"] is None
    assert diag["input_width"] == IMG_W and diag["input_height"] == IMG_H

    # 핵심: 세그멘테이션/매팅으로 절대 넘어가지 않는다.
    assert calls["segment"] == []
    assert calls["vitmatte"] == []


def test_no_central_rectangle_fallback_remains_in_source():
    """중앙 80% 사각형 시드를 만들던 코드가 남아 있지 않은지 확인."""
    import inspect

    src = inspect.getsource(vs)
    # 예전 폴백은 `int(w * 0.1)` / `int(h * 0.1)` 패딩으로 중앙 박스를 만들었다.
    assert "int(w * 0.1)" not in src
    assert "int(h * 0.1)" not in src


def test_segmenters_require_a_bbox():
    """_sam2_mask / _grabcut_mask 는 이제 Optional bbox 를 받지 않는다."""
    import inspect

    for fn, param in ((vs._grabcut_mask, "bbox"), (vs._sam2_mask, "prompt_bbox")):
        sig = inspect.signature(fn)
        assert sig.parameters[param].default is inspect.Parameter.empty


# --------------------------------------------------------------------------
# 2. tight 프롬프트 bbox vs padded 크롭 bbox
# --------------------------------------------------------------------------


def test_sam2_prompt_uses_tight_bbox_and_crop_uses_padded(monkeypatch):
    tight = (20, 15, 100, 80)
    calls = _install_pipeline(monkeypatch, detection=_detection(bbox=tight))

    _png, meta = vs.matte_foreground_with_meta(
        make_jpeg_bytes(IMG_W, IMG_H), bbox_pad_frac=0.15
    )

    # SAM2 에는 패딩 없는 tight box 가 들어가야 한다.
    assert calls["segment"][0]["prompt_bbox"] == tight
    assert meta["sam2_prompt_bbox"] == list(tight)
    assert meta["raw_bbox"] == list(tight)

    # 크롭 박스는 별도로, 더 넓게.
    crop = meta["crop_bbox"]
    assert crop != list(tight)
    assert crop[0] < tight[0] and crop[1] < tight[1]
    assert crop[2] > tight[2] and crop[3] > tight[3]


def test_expand_bbox_is_clamped_to_frame():
    assert vs._expand_bbox((0, 0, 10, 10), 20, 20, 1.0) == (0, 0, 15, 15)


# --------------------------------------------------------------------------
# 3. 마스크 / 알파 품질 게이트
# --------------------------------------------------------------------------


def test_mask_below_minimum_is_rejected(monkeypatch):
    tiny = blob_mask(IMG_H, IMG_W, area_fraction=0.01)
    _install_pipeline(monkeypatch, detection=_detection(), mask=tiny)

    with pytest.raises(MaskTooSmallError) as exc:
        vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert exc.value.code == "CUTOUT_MASK_TOO_SMALL"
    assert exc.value.diagnostics["mask_area_fraction"] < vs.MIN_MASK_AREA_FRACTION
    # 진단은 실패해도 채워져야 한다.
    assert exc.value.diagnostics["subject_detected"] is True
    assert exc.value.diagnostics["segmenter_used"] == "sam2"


def test_mask_above_maximum_is_rejected(monkeypatch):
    # 타원은 bbox 대비 최대 ~0.785 라 0.85 를 넘길 수 없다 — 꽉 찬 밴드를 쓴다.
    # (이 마스크는 사각형이기도 하지만, 크기 검사가 사각형 검사보다 먼저 돈다.)
    huge = blob_mask(IMG_H, IMG_W, area_fraction=0.95, rectangle=True)
    _install_pipeline(monkeypatch, detection=_detection(), mask=huge)

    with pytest.raises(MaskTooLargeError) as exc:
        vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert exc.value.code == "CUTOUT_MASK_TOO_LARGE"
    assert exc.value.diagnostics["mask_area_fraction"] > vs.MAX_MASK_AREA_FRACTION


def test_rectangle_like_mask_is_rejected(monkeypatch):
    rect = blob_mask(IMG_H, IMG_W, area_fraction=0.3, rectangle=True)
    _install_pipeline(monkeypatch, detection=_detection(), mask=rect)

    with pytest.raises(RectangleLikeMaskError) as exc:
        vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert exc.value.code == "CUTOUT_RECTANGLE_LIKE"
    assert exc.value.diagnostics["rectangle_like_mask"] is True
    assert exc.value.diagnostics["mask_bbox_fill_ratio"] >= vs.RECTANGLE_FILL_RATIO_THRESHOLD


def test_elliptical_mask_is_not_rectangle_like(monkeypatch):
    """정상적인 동물 실루엣(타원)은 사각형으로 오탐하지 않아야 한다."""
    ellipse = blob_mask(IMG_H, IMG_W, area_fraction=0.3, rectangle=False)
    _install_pipeline(monkeypatch, detection=_detection(), mask=ellipse)

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert meta["rectangle_like_mask"] is False
    assert meta["mask_bbox_fill_ratio"] < vs.RECTANGLE_FILL_RATIO_THRESHOLD


def test_empty_alpha_is_rejected(monkeypatch):
    _install_pipeline(monkeypatch, detection=_detection(), alpha_fraction=0.0)

    with pytest.raises(AlphaEmptyError) as exc:
        vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert exc.value.code == "CUTOUT_ALPHA_EMPTY"
    assert exc.value.diagnostics["alpha_area_fraction"] == 0.0


def test_rectangle_rejection_can_be_disabled(monkeypatch):
    rect = blob_mask(IMG_H, IMG_W, area_fraction=0.3, rectangle=True)
    _install_pipeline(monkeypatch, detection=_detection(), mask=rect)
    monkeypatch.setattr(vs, "REJECT_RECTANGLE_LIKE", False)

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    # 거절하지 않더라도 플래그는 남는다.
    assert meta["rectangle_like_mask"] is True


# --------------------------------------------------------------------------
# 4. SAM2 실패는 삼키지 않는다
# --------------------------------------------------------------------------


def test_sam2_failure_is_logged_and_recorded(monkeypatch, caplog):
    """SAM2 예외 → GrabCut 폴백 + 로그 + 메타에 사유/타입 기록.

    (Phase 2A: _segment_foreground 는 _sam2_mask 가 아니라 _sam2_candidates 를
    호출하므로 목업 대상이 바뀌었다. 폴백 동작 자체는 Phase 1 그대로.)
    """
    fg = blob_mask(IMG_H, IMG_W, area_fraction=0.3)

    def boom(rgb, prompt_bbox, model_name, device, *, multimask):
        raise RuntimeError("sam2 weights unavailable")

    def fake_grabcut(rgb, bbox, **kwargs):
        return fg

    monkeypatch.setattr(vs, "_sam2_candidates", boom)
    monkeypatch.setattr(vs, "_grabcut_mask", fake_grabcut)
    monkeypatch.setattr(
        vs, "_binary_mask_to_trimap", lambda m, **kw: np.where(m > 0, 255, 0).astype(np.uint8)
    )

    with caplog.at_level("ERROR"):
        outcome = vs._segment_foreground(
            np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8),
            (10, 10, 50, 50),
            segmenter="sam2",
            sam2_model="fake/sam2",
            device="cpu",
        )

    assert outcome.segmenter_used == "grabcut"
    assert outcome.fallback is True
    assert outcome.fallback_reason == "sam2_failed"
    assert outcome.error_type == "RuntimeError"
    assert "sam2 weights unavailable" in (outcome.error_message or "")
    assert any("SAM2 segmentation failed" in r.message for r in caplog.records)


def test_sam2_failure_surfaces_in_response_meta(monkeypatch):
    fg = blob_mask(IMG_H, IMG_W, area_fraction=0.3)
    failed = vs.SegmentationOutcome(
        fg_binary=fg,
        trimap=np.where(fg > 0, 255, 0).astype(np.uint8),
        segmenter_used="grabcut",
        fallback=True,
        fallback_reason="sam2_failed",
        error_type="RuntimeError",
        error_message="sam2 weights unavailable",
    )
    _install_pipeline(monkeypatch, detection=_detection(), segmenter_outcome=failed)

    _png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert meta["segmenter_requested"] == "sam2"
    assert meta["segmenter_used"] == "grabcut"
    assert meta["segmenter_fallback"] is True
    assert meta["fallback_reason"] == "sam2_failed"
    assert meta["segmenter_error"].startswith("RuntimeError:")
    # 하위 호환 키도 실제 사용된 세그멘터를 가리켜야 한다.
    assert meta["segmenter"] == "grabcut"


# --------------------------------------------------------------------------
# 5. 성공 응답의 진단 정보가 정직한가
# --------------------------------------------------------------------------


def test_successful_result_contains_truthful_diagnostics(monkeypatch):
    _install_pipeline(monkeypatch, detection=_detection(conf=0.87), alpha_fraction=0.3)

    png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    for key in (
        "detector",
        "detector_model",
        "subject_detected",
        "subject_class",
        "detection_confidence",
        "raw_bbox",
        "sam2_prompt_bbox",
        "crop_bbox",
        "segmenter_requested",
        "segmenter_used",
        "segmenter_fallback",
        "fallback_reason",
        "segmenter_error",
        "sam2_score",
        "mask_area_fraction",
        "rectangle_like_mask",
        "alpha_area_fraction",
        "input_width",
        "input_height",
        "processing_width",
        "processing_height",
    ):
        assert key in meta, f"missing diagnostic field: {key}"

    assert meta["detector"] == "yolo"
    assert meta["subject_detected"] is True
    assert meta["subject_class"] == "dog"
    assert meta["detection_confidence"] == pytest.approx(0.87)
    assert meta["segmenter_fallback"] is False
    assert meta["segmenter_error"] is None
    assert meta["sam2_score"] == pytest.approx(0.91)
    assert meta["alpha_area_fraction"] == pytest.approx(0.3, abs=0.02)
    assert meta["input_width"] == IMG_W and meta["input_height"] == IMG_H


def test_debug_artifacts_disabled_by_default(monkeypatch):
    _install_pipeline(monkeypatch, detection=_detection())
    artifacts: dict[str, bytes] = {}

    vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H), debug_artifacts=artifacts)

    assert artifacts == {}, "디버그 아티팩트는 기본적으로 꺼져 있어야 합니다"


def test_debug_artifacts_collected_when_enabled(monkeypatch):
    _install_pipeline(monkeypatch, detection=_detection())
    monkeypatch.setattr(vs, "DEBUG_ARTIFACTS_ENABLED", True)
    artifacts: dict[str, bytes] = {}

    vs.matte_foreground_with_meta(make_jpeg_bytes(IMG_W, IMG_H), debug_artifacts=artifacts)

    assert set(artifacts) == {
        "01_detection_bbox.png",
        "02_crop_bbox.png",
        "03_segmentation_mask.png",
        # Phase 2B: 사람 인지 보정 전 기준선 마스크
        "03b_mask_before_person_aware.png",
        "04_trimap.png",
        "05_alpha.png",
        "06_checkerboard.png",
    }
    for data in artifacts.values():
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------
# 6. analyze_mask 단위 동작
# --------------------------------------------------------------------------


def test_analyze_mask_rectangle_vs_ellipse():
    rect = blob_mask(100, 100, area_fraction=0.25, rectangle=True)
    ellipse = blob_mask(100, 100, area_fraction=0.25, rectangle=False)

    rect_stats = vs.analyze_mask(rect)
    ellipse_stats = vs.analyze_mask(ellipse)

    assert rect_stats.bbox_fill_ratio == pytest.approx(1.0, abs=0.02)
    assert rect_stats.rectangle_like is True
    assert ellipse_stats.bbox_fill_ratio == pytest.approx(0.785, abs=0.05)
    assert ellipse_stats.rectangle_like is False


def test_analyze_mask_empty():
    stats = vs.analyze_mask(np.zeros((10, 10), dtype=np.uint8))
    assert stats.area_fraction == 0.0
    assert stats.rectangle_like is False
