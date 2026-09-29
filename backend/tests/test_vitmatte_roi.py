"""
ViTMatte ROI 크롭 (메모리 최적화 1단계) 검증.

검증 대상은 모델 품질이 아니라 계약이다:
  - 출력 알파는 항상 입력과 같은 (H, W)
  - ROI = union(crop_bbox, bbox(trimap != 0)) + 여백(≥32px), 이미지 경계로 클램프
  - ROI 안 알파는 크롭 결과와 좌표가 정확히 맞고, ROI 밖은 0
  - ROI 를 만들 수 없거나 계산이 실패하면 기존 전체 프레임 경로로 폴백
무거운 모델은 전부 목업한다.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from backend.services import vitmatte_service as vs

from .conftest import blob_mask, make_jpeg_bytes

H, W = 400, 600
PAD = vs.VITMATTE_ROI_MIN_PAD_PX


def _rect(h: int, w: int, y1: int, y2: int, x1: int, x2: int, value: int = 255) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    m[y1:y2, x1:x2] = value
    return m


def _trimap_with_band(fg: np.ndarray, band: int = 6) -> np.ndarray:
    """0/255 마스크 → {0,128,255} 트라이맵 (경계 ±band 픽셀을 128 로)."""
    import cv2

    k = np.ones((3, 3), np.uint8)
    sure = cv2.erode(fg, k, iterations=band)
    dil = cv2.dilate(fg, k, iterations=band)
    tri = np.full(fg.shape, 128, dtype=np.uint8)
    tri[sure > 0] = 255
    tri[dil == 0] = 0
    return tri


def _rule_alpha(trimap: np.ndarray) -> np.ndarray:
    """정렬 검증용 결정적 '매팅': 255→1.0, 128→0.5, 0→0.0"""
    a = np.zeros(trimap.shape[:2], dtype=np.float32)
    a[trimap == 255] = 1.0
    a[trimap == 128] = 0.5
    return a


class _FakeViTMatte:
    """_run_vitmatte 대역 — 받은 입력 크기를 기록하고 규칙 알파를 돌려준다."""

    def __init__(self, wrong_shape_first_call=None):
        self.calls: list[dict] = []
        self.wrong_shape_first_call = wrong_shape_first_call

    def __call__(self, rgb, trimap, model_name, device):
        assert rgb.shape[:2] == trimap.shape[:2]
        self.calls.append(
            {"shape": tuple(trimap.shape[:2]), "contiguous": bool(rgb.flags["C_CONTIGUOUS"])}
        )
        if self.wrong_shape_first_call is not None and len(self.calls) == 1:
            return np.zeros(self.wrong_shape_first_call, dtype=np.float32)
        return _rule_alpha(trimap)


@pytest.fixture(autouse=True)
def _roi_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vs, "VITMATTE_ROI_ENABLED", True)


# --------------------------------------------------------------------------
# compute_vitmatte_roi — 기하
# --------------------------------------------------------------------------


def test_roi_centered_pet_is_union_plus_padding():
    trimap = _rect(H, W, 150, 250, 200, 400)
    crop_bbox = (190, 140, 410, 260)  # YOLO 박스(패딩 포함)가 마스크보다 조금 큼

    roi = vs.compute_vitmatte_roi(crop_bbox, trimap)

    assert roi == (190 - PAD, 140 - PAD, 410 + PAD, 260 + PAD)
    x1, y1, x2, y2 = roi
    assert 0 <= x1 < x2 <= W and 0 <= y1 < y2 <= H


def test_roi_padding_never_below_32px():
    trimap = _rect(H, W, 150, 250, 200, 400)
    roi = vs.compute_vitmatte_roi((200, 150, 400, 250), trimap, pad_px=4)
    assert roi == (200 - 32, 150 - 32, 400 + 32, 250 + 32)


@pytest.mark.parametrize(
    "mask_box, crop_bbox, expected",
    [
        # 좌상단 모서리에 붙은 펫 → 좌/상은 0 으로 클램프
        ((0, 100, 0, 120), (0, 0, 120, 100), (0, 0, 120 + PAD, 100 + PAD)),
        # 우하단 모서리 → 우/하는 W/H 로 클램프
        ((300, 400, 480, 600), (480, 300, 600, 400), (480 - PAD, 300 - PAD, W, H)),
        # 왼쪽 가장자리만 닿음
        ((150, 250, 0, 200), (0, 150, 200, 250), (0, 150 - PAD, 200 + PAD, 250 + PAD)),
        # 아래쪽 가장자리만 닿음
        ((300, 400, 200, 400), (200, 300, 400, 400), (200 - PAD, 300 - PAD, 400 + PAD, H)),
    ],
)
def test_roi_clamps_at_edges_and_corners(mask_box, crop_bbox, expected):
    y1, y2, x1, x2 = mask_box
    trimap = _rect(H, W, y1, y2, x1, x2)
    assert vs.compute_vitmatte_roi(crop_bbox, trimap) == expected


def test_roi_includes_tail_and_ear_outside_yolo_bbox():
    """SAM2 마스크(트라이맵)가 YOLO 박스 밖으로 나가면 ROI 는 트라이맵을 따른다."""
    body = _rect(H, W, 150, 250, 200, 400)
    tail = _rect(H, W, 240, 250, 400, 520)  # 오른쪽으로 삐져나온 꼬리
    ear = _rect(H, W, 100, 150, 210, 230)  # 위로 솟은 귀
    trimap = np.maximum(np.maximum(body, tail), ear)
    crop_bbox = (200, 150, 400, 250)  # 몸통만 잡은 박스

    roi = vs.compute_vitmatte_roi(crop_bbox, trimap)

    assert roi == (200 - PAD, 100 - PAD, 520 + PAD, 250 + PAD)
    x1, y1, x2, y2 = roi
    # 트라이맵의 0 이 아닌 픽셀은 하나도 ROI 밖에 없어야 한다.
    outside = trimap.copy()
    outside[y1:y2, x1:x2] = 0
    assert not outside.any()


def test_roi_full_frame_pet_equals_whole_image():
    trimap = np.full((H, W), 255, dtype=np.uint8)
    assert vs.compute_vitmatte_roi((0, 0, W, H), trimap) == (0, 0, W, H)


def test_roi_uses_unknown_band_not_only_foreground():
    """트라이맵 128(미확정) 픽셀도 ROI 범위에 들어간다 — 그곳이 매팅 대상이다."""
    trimap = _rect(H, W, 150, 250, 200, 400, value=128)
    assert vs.compute_vitmatte_roi(None, trimap) == (200 - PAD, 150 - PAD, 400 + PAD, 250 + PAD)


@pytest.mark.parametrize(
    "crop_bbox, trimap",
    [
        (None, np.zeros((H, W), dtype=np.uint8)),  # 아무 근거도 없음
        ((10, 10, 10, 50), np.zeros((H, W), dtype=np.uint8)),  # 퇴화한 박스 + 빈 트라이맵
        ((0, 0, 100, 100), np.zeros((H,), dtype=np.uint8)),  # 2D 가 아님
        ((0, 0, 100, 100), np.zeros((0, W), dtype=np.uint8)),  # 빈 이미지
    ],
)
def test_roi_invalid_inputs_return_none(crop_bbox, trimap):
    assert vs.compute_vitmatte_roi(crop_bbox, trimap) is None


# --------------------------------------------------------------------------
# _run_vitmatte_roi — 붙여넣기 정렬 / 출력 크기 / 폴백
# --------------------------------------------------------------------------


def test_run_roi_pastes_back_aligned_and_keeps_full_size(monkeypatch):
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 255, size=(H, W, 3), dtype=np.uint8)
    fg = _rect(H, W, 150, 250, 200, 400)
    trimap = _trimap_with_band(fg)
    crop_bbox = (190, 140, 410, 260)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=crop_bbox)

    # 출력 계약: 원본 크기, float32
    assert alpha.shape == (H, W)
    assert alpha.dtype == np.float32
    # 크롭에 대해 낸 결과가 전체 프레임 규칙과 픽셀 단위로 정확히 같은 자리에 붙는다
    expected = _rule_alpha(trimap)
    assert np.array_equal(alpha, expected)
    # ROI 밖은 0
    x1, y1, x2, y2 = info["roi"]
    outside = alpha.copy()
    outside[y1:y2, x1:x2] = 0
    assert not outside.any()
    # 모델은 ROI 크기만 받았고, 연속 메모리 배열을 받았다
    assert fake.calls == [{"shape": (y2 - y1, x2 - x1), "contiguous": True}]
    assert (y2 - y1) < H and (x2 - x1) < W
    # 진단
    assert info["fallback"] is False and info["fallback_reason"] is None
    assert info["original_size"] == [W, H]
    assert info["roi_size"] == [x2 - x1, y2 - y1]
    assert info["roi_area_ratio"] == pytest.approx(((x2 - x1) * (y2 - y1)) / (W * H), abs=1e-4)
    assert info["pad_px"] >= 32


def test_run_roi_off_center_pet_alignment_at_corner(monkeypatch):
    """모서리에 붙은 펫: 클램프된 ROI 에서도 좌표가 어긋나지 않는다."""
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    fg = _rect(H, W, 300, 400, 480, 600)
    trimap = _trimap_with_band(fg)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=(480, 300, 600, 400))

    assert alpha.shape == (H, W)
    assert np.array_equal(alpha, _rule_alpha(trimap))
    assert info["roi"][2] == W and info["roi"][3] == H


def test_run_roi_full_frame_pet_has_ratio_one(monkeypatch):
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    trimap = np.full((H, W), 255, dtype=np.uint8)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=(0, 0, W, H))

    assert alpha.shape == (H, W)
    assert info["fallback"] is False
    assert info["roi"] == [0, 0, W, H]
    assert info["roi_area_ratio"] == 1.0
    assert fake.calls[0]["shape"] == (H, W)


def test_run_roi_falls_back_when_roi_unavailable(monkeypatch):
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    trimap = np.zeros((H, W), dtype=np.uint8)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=None)

    assert alpha.shape == (H, W)
    assert info["fallback"] is True and info["fallback_reason"] == "roi_unavailable"
    assert fake.calls == [{"shape": (H, W), "contiguous": True}]


def test_run_roi_falls_back_when_roi_computation_raises(monkeypatch):
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)

    def boom(*a, **k):
        raise ValueError("bad roi")

    monkeypatch.setattr(vs, "compute_vitmatte_roi", boom)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    trimap = _rect(H, W, 150, 250, 200, 400)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=(200, 150, 400, 250))

    assert alpha.shape == (H, W)
    assert np.array_equal(alpha, _rule_alpha(trimap))  # 전체 프레임 결과 그대로
    assert info["fallback"] is True
    assert info["fallback_reason"] == "roi_error:ValueError"
    assert fake.calls == [{"shape": (H, W), "contiguous": True}]


def test_run_roi_disabled_uses_full_frame(monkeypatch):
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    trimap = _rect(H, W, 150, 250, 200, 400)

    _, info = vs._run_vitmatte_roi(
        rgb, trimap, "m", "cpu", crop_bbox=(200, 150, 400, 250), enabled=False
    )

    assert info["enabled"] is False
    assert info["fallback"] is True and info["fallback_reason"] == "disabled"
    assert fake.calls[0]["shape"] == (H, W)


def test_run_roi_alpha_shape_mismatch_falls_back_to_full_frame(monkeypatch):
    """모델이 ROI 와 다른 크기의 알파를 내면 잘못 붙이지 않고 전체 프레임으로."""
    fake = _FakeViTMatte(wrong_shape_first_call=(10, 10))
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    trimap = _rect(H, W, 150, 250, 200, 400)

    alpha, info = vs._run_vitmatte_roi(rgb, trimap, "m", "cpu", crop_bbox=(200, 150, 400, 250))

    assert alpha.shape == (H, W)
    assert np.array_equal(alpha, _rule_alpha(trimap))  # 두 번째(전체 프레임) 호출 결과
    assert info["fallback"] is True and info["fallback_reason"] == "alpha_shape_mismatch"
    assert info["roi"] is None
    assert len(fake.calls) == 2 and fake.calls[1]["shape"] == (H, W)


# --------------------------------------------------------------------------
# 파이프라인 통합 — matte_foreground_with_meta 의 출력 크기/진단
# --------------------------------------------------------------------------


def test_pipeline_output_png_keeps_input_size_with_roi(monkeypatch):
    img_w, img_h = 512, 384
    fg = blob_mask(img_h, img_w, area_fraction=0.15)  # 중앙 타원
    ys, xs = np.nonzero(fg)
    tight = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    trimap = _trimap_with_band(fg)

    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    monkeypatch.setattr(
        vs,
        "_detect_subject",
        lambda image, yolo_model, *, conf: vs.SubjectDetection(
            bbox=tight, class_id=16, class_name="dog", confidence=0.9
        ),
    )
    monkeypatch.setattr(vs, "_detect_persons", lambda *a, **k: [])
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda rgb, prompt_bbox, **kw: vs.SegmentationOutcome(
            fg_binary=fg, trimap=trimap, segmenter_used="sam2", sam2_score=0.9
        ),
    )

    png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(img_w, img_h))

    with Image.open(io.BytesIO(png)) as out:
        assert out.size == (img_w, img_h)
        assert out.mode == "RGBA"
        alpha = np.asarray(out)[:, :, 3]
    roi = meta["vitmatte_roi"]
    assert roi["fallback"] is False
    assert roi["original_size"] == [img_w, img_h]
    assert 0.0 < roi["roi_area_ratio"] < 1.0
    x1, y1, x2, y2 = roi["roi"]
    assert fake.calls[0]["shape"] == (y2 - y1, x2 - x1)
    # ROI 밖 알파는 0, 확실한 전경(255)은 불투명 — 그림자 억제는 알파 1.0 을 건드리지 않는다
    outside = alpha.copy()
    outside[y1:y2, x1:x2] = 0
    assert not outside.any()
    assert alpha[trimap == 255].min() == 255
    # 기존 진단 필드/처리 해상도 계약은 그대로다 (리사이즈 없음)
    assert meta["processing_width"] == img_w and meta["processing_height"] == img_h
    assert meta["method"] == "vitmatte"


def test_pipeline_full_frame_pet_still_produces_full_size(monkeypatch):
    img_w, img_h = 256, 192
    fg = blob_mask(img_h, img_w, area_fraction=0.8)  # 프레임 대부분을 덮는 타원(사각형 게이트 통과)
    trimap = _trimap_with_band(fg)
    fake = _FakeViTMatte()
    monkeypatch.setattr(vs, "_run_vitmatte", fake)
    monkeypatch.setattr(
        vs,
        "_detect_subject",
        lambda image, yolo_model, *, conf: vs.SubjectDetection(
            bbox=(0, 0, img_w, img_h), class_id=16, class_name="dog", confidence=0.9
        ),
    )
    monkeypatch.setattr(vs, "_detect_persons", lambda *a, **k: [])
    monkeypatch.setattr(
        vs,
        "_segment_foreground",
        lambda rgb, prompt_bbox, **kw: vs.SegmentationOutcome(
            fg_binary=fg, trimap=trimap, segmenter_used="sam2", sam2_score=0.9
        ),
    )

    png, meta = vs.matte_foreground_with_meta(make_jpeg_bytes(img_w, img_h))

    with Image.open(io.BytesIO(png)) as out:
        assert out.size == (img_w, img_h)
    assert meta["vitmatte_roi"]["roi"] == [0, 0, img_w, img_h]
    assert meta["vitmatte_roi"]["roi_area_ratio"] == 1.0
    assert fake.calls[0]["shape"] == (img_h, img_w)
