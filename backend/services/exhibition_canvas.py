"""
전시 피사체 캔버스 정규화 — 누끼(크롭) 1장을 **고정 캔버스**에 일정한 제시 스케일로 놓는다.

  크롭 RGBA → 비율 유지 리사이즈(늘림 없음) → 투명 캔버스에 붙임
    · 실루엣(알파 ≥ SOLID_ALPHA) 높이 = 캔버스 높이 × TARGET_HEIGHT_FRAC
      (옆으로 긴 펫은 폭 ≤ 캔버스 폭 × MAX_WIDTH_FRAC 가 먼저 걸리면 그만큼만 — fit_axis="width")
    · 실루엣 바닥(발/지면) = 캔버스 높이 × GROUND_LINE_FRAC 행 (일정한 지면선)
    · 실루엣 가로 중심 = 캔버스 가로 중심
  → 작은 펫도 큰 펫도 렌더러에 같은 크기·같은 지면선으로 도착한다.

맵(호흡 가중치·잠금)은 이 **정규화된 캔버스 위에서** 계산한다 — 그래서 subject_rgba ·
breathing_weight · locked_mask · anchors 가 전부 같은 좌표계에 있고 재샘플링이 한 번뿐이다.
크롭 → 정규화 변환은 x' = x·scale_xy[0] + offset_xy[0], y' = y·scale_xy[1] + offset_xy[1]
(manifest.normalization 에 기록; 원본 좌표까지 역변환하는 식도 함께 적는다).

환경변수:
  EXHIBITION_CANVAS_WH   기본 "1024x1024"
"""

from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np

CANVAS_VERSION = "canvas-norm-v1"
DEFAULT_CANVAS_WH = (1024, 1024)
#: 실루엣 높이 / 캔버스 높이 목표.
TARGET_HEIGHT_FRAC = 0.70
#: 실루엣 폭 / 캔버스 폭 상한 (옆으로 긴 펫).
MAX_WIDTH_FRAC = 0.90
#: 실루엣 바닥 행 / 캔버스 높이.
GROUND_LINE_FRAC = 0.85
#: 실루엣 판정 알파 — exhibition_breathing_maps.SOLID_ALPHA 와 같다.
SOLID_ALPHA = 128
PRESENCE_ALPHA = 16


class CanvasNormalizationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def canvas_wh() -> tuple[int, int]:
    raw = (os.getenv("EXHIBITION_CANVAS_WH") or "").strip().lower()
    try:
        w, h = (int(v) for v in raw.split("x"))
        if w >= 256 and h >= 256:
            return w, h
    except ValueError:
        pass
    return DEFAULT_CANVAS_WH


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if not rows.size or not cols.size:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1  # 끝 배타


def solid_bbox(alpha: np.ndarray) -> tuple[int, int, int, int]:
    box = _bbox(alpha >= SOLID_ALPHA) or _bbox(alpha >= PRESENCE_ALPHA)
    if box is None:
        raise CanvasNormalizationError("MAPS_EMPTY_ALPHA", "Alpha has no visible subject.")
    return box


def safe_region_bounds(canvas: tuple[int, int] | None = None) -> dict[str, int]:
    """고정 SAFE PET REGION — 어떤 펫이든 정규화된 실루엣 전체가 항상 이 안에 있어야 한다.

    TARGET_HEIGHT_FRAC·MAX_WIDTH_FRAC·GROUND_LINE_FRAC 가 이미 이 영역의 정의다 —
    가로 중앙, 바닥은 지면선 행, 너비/높이 상한은 scale 선택이 강제한다(min of
    height-fit, width-fit). 여기서는 그 셋을 캔버스 픽셀 좌표의 한 박스로 명시한다.
    x1,y1,x2,y2 는 끝 배타(solid_bbox 와 같은 규약).
    """
    W, H = canvas or canvas_wh()
    ground_line_y = int(round(GROUND_LINE_FRAC * H)) - 1
    region_w = int(round(MAX_WIDTH_FRAC * W))
    region_h = int(round(TARGET_HEIGHT_FRAC * H))
    x1 = (W - region_w) // 2
    y2 = ground_line_y + 1
    return {"x1": x1, "y1": y2 - region_h, "x2": x1 + region_w, "y2": y2, "ground_line_y": ground_line_y}


def transform_point(xy: tuple[float, float], info: dict[str, Any]) -> tuple[float, float]:
    """크롭 좌표 → 정규화 캔버스 좌표."""
    sx, sy = info["scale_xy"]
    tx, ty = info["offset_xy"]
    return (float(xy[0]) * sx + tx, float(xy[1]) * sy + ty)


def normalize_subject_canvas(
    rgba: np.ndarray, *, canvas: tuple[int, int] | None = None
) -> tuple[np.ndarray, dict[str, Any]]:
    """크롭 RGBA(uint8 H×W×4, straight alpha) → (정규화 캔버스 RGBA, 기록)."""
    W, H = canvas or canvas_wh()
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    x1, y1, x2, y2 = solid_bbox(alpha)
    sw, sh = x2 - x1, y2 - y1

    s_height = TARGET_HEIGHT_FRAC * H / float(sh)
    s_width = MAX_WIDTH_FRAC * W / float(sw)
    scale = min(s_height, s_width)
    fit_axis = "height" if s_height <= s_width else "width"

    tw, th = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    sx, sy = tw / float(w), th / float(h)
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(rgba, (tw, th), interpolation=interpolation)
    if resized.ndim == 2:  # (1 px 폭/높이 극단) 채널 축 복원
        resized = resized.reshape(th, tw, -1)

    # 리사이즈 뒤 실루엣 bbox 를 다시 재서(보간 오차 흡수) 지면선·가로 중심을 정확히 맞춘다.
    rx1, ry1, rx2, ry2 = solid_bbox(resized[:, :, 3])
    region = safe_region_bounds((W, H))
    ground_row = region["ground_line_y"]  # 실루엣의 마지막 행이 올 자리
    tx = int(round(W / 2.0 - (rx1 + rx2) / 2.0))
    ty = ground_row - (ry2 - 1)

    out = np.zeros((H, W, 4), np.uint8)
    # 붙여넣기. 크롭의 투명 여백이 캔버스 밖으로 나가는 건 무해하다 — clipped 는 **실루엣**이
    # 잘렸을 때만 True 다 (여백 규칙상 정상적으로는 일어나지 않는다).
    dx1, dy1 = max(0, tx), max(0, ty)
    dx2, dy2 = min(W, tx + tw), min(H, ty + th)
    clipped = (tx + rx1 < 0) or (ty + ry1 < 0) or (tx + rx2 > W) or (ty + ry2 > H)
    if dx2 > dx1 and dy2 > dy1:
        out[dy1:dy2, dx1:dx2] = resized[dy1 - ty : dy2 - ty, dx1 - tx : dx2 - tx]

    nx1, ny1, nx2, ny2 = solid_bbox(out[:, :, 3])
    # 규칙: 전체 실루엣은 항상 SAFE PET REGION 안에 있어야 한다 — scale 선택(min of
    # height-fit, width-fit) + 바닥 정렬 + 가로 중앙이면 수학적으로 항상 성립하지만,
    # resize/threshold 보간으로 생기는 1px 안쪽 오차만 여유를 둔다.
    tol = 1
    if (
        nx1 < region["x1"] - tol or nx2 > region["x2"] + tol
        or ny1 < region["y1"] - tol or ny2 > region["y2"] + tol
    ):
        raise CanvasNormalizationError(
            "SAFE_REGION_VIOLATION",
            f"Normalized subject bbox {[nx1, ny1, nx2, ny2]} does not fit inside the safe pet region {region}.",
        )
    info: dict[str, Any] = {
        "version": CANVAS_VERSION,
        "canvas_wh": [W, H],
        "crop_wh": [w, h],
        "subject_bbox_in_crop": [x1, y1, x2, y2],
        "subject_wh_in_crop": [sw, sh],
        "scale": round(scale, 6),
        "scale_xy": [round(sx, 6), round(sy, 6)],
        "offset_xy": [tx, ty],
        "fit_axis": fit_axis,
        "target_height_frac": TARGET_HEIGHT_FRAC,
        "max_width_frac": MAX_WIDTH_FRAC,
        "ground_line_frac": GROUND_LINE_FRAC,
        "safe_region": region,
        "ground_line_y": region["ground_line_y"],
        "subject_bbox": [nx1, ny1, nx2, ny2],
        "subject_wh": [nx2 - nx1, ny2 - ny1],
        "subject_height_frac": round((ny2 - ny1) / float(H), 4),
        "subject_width_frac": round((nx2 - nx1) / float(W), 4),
        "subject_center_x": round((nx1 + nx2) / 2.0, 1),
        "ground_y": float(ny2 - 1),
        "upscaled": bool(scale > 1.0),
        "clipped": bool(clipped),
        "interpolation": "area" if interpolation == cv2.INTER_AREA else "linear",
        "transform": {
            "crop_to_normalized": "x' = x * scale_xy[0] + offset_xy[0]; y' = y * scale_xy[1] + offset_xy[1]",
            "normalized_to_crop": "x = (x' - offset_xy[0]) / scale_xy[0]; y = (y' - offset_xy[1]) / scale_xy[1]",
            "normalized_to_source": (
                "source_x = (x' - offset_xy[0]) / scale_xy[0] / source.processing_scale + crop_rect_in_source[0]; "
                "source_y = (y' - offset_xy[1]) / scale_xy[1] / source.processing_scale + crop_rect_in_source[1]"
            ),
        },
    }
    return out, info
