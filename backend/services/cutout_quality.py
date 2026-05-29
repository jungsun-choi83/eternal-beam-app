"""
누끼 결과 RGBA 알파 분석 — 장모(털 경계) 여부 판별 (비용 0).

경계 링 픽셀 중 alpha ∈ [alpha_low, alpha_high] 비율이 높으면
반투명 털이 많다고 보고 alpha matting 재처리를 권장합니다.
"""

from __future__ import annotations

import io
import os
from typing import Any

import numpy as np
from PIL import Image

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False


def _fur_edge_threshold() -> float:
    return float(os.getenv("CUTOUT_FUR_EDGE_RATIO_THRESHOLD", "0.38"))


def analyze_alpha_fur_edge(
    png_bytes: bytes,
    *,
    alpha_low: int = 10,
    alpha_high: int = 240,
) -> dict[str, Any]:
    """
    Returns:
      semi_transparent_ratio: 경계 링에서 반투명(alpha_low~alpha_high) 픽셀 비율
      needs_refinement: 비율 >= CUTOUT_FUR_EDGE_RATIO_THRESHOLD
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = np.array(img, dtype=np.uint8)[:, :, 3]
    h, w = alpha.shape
    if h == 0 or w == 0:
        return {
            "semi_transparent_ratio": 0.0,
            "boundary_pixel_count": 0,
            "needs_refinement": False,
            "threshold": _fur_edge_threshold(),
        }

    subject = (alpha > alpha_low).astype(np.uint8)
    if CV2_AVAILABLE:
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(subject, kernel, iterations=2)
        eroded = cv2.erode(subject, kernel, iterations=1)
        boundary = (dilated - eroded) > 0
    else:
        # OpenCV 없을 때: 전경 근처 전체에서 반투명 비율
        boundary = (alpha >= alpha_low) & (alpha <= alpha_high)

    boundary_count = int(np.count_nonzero(boundary))
    if boundary_count == 0:
        ratio = 0.0
    else:
        semi = boundary & (alpha >= alpha_low) & (alpha <= alpha_high)
        ratio = float(np.count_nonzero(semi)) / float(boundary_count)

    threshold = _fur_edge_threshold()
    needs = ratio >= threshold

    # 명세 호환용 0~1 품질 점수 (반투명 비율이 낮을수록 좋음)
    quality_score = round(max(0.0, min(1.0, 1.0 - ratio)), 4)

    return {
        "semi_transparent_ratio": round(ratio, 4),
        "boundary_pixel_count": boundary_count,
        "needs_refinement": needs,
        "threshold": threshold,
        "quality_score": quality_score,
        "alpha_low": alpha_low,
        "alpha_high": alpha_high,
    }
