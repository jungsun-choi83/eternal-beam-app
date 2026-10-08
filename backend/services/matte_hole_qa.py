"""
bgmodel 매트의 **내부 구멍** 검사 — 값싼 QA 로 ViTMatte 폴백 여부를 정한다.

── 왜 필요한가 ──────────────────────────────────────────────────────────────
bgmodel 은 픽셀 단위 색 거리 + 무채색 그림자 판정이라 피사체가 어디 있는지
모른다. 중립 회색 배경 위의 흰 펫에서는 코·입술·눈가·주근깨 같은 어두운
무채색 부위가 "배경/그림자"로 판정돼 실루엣 **안쪽**에 투명 구멍이 생긴다.
실제 펫 실루엣 안쪽에는 투명 영역이 거의 없어야 하므로, 실루엣에 완전히
둘러싸인 투명 영역을 세면 이 실패를 모델 없이 잡을 수 있다.

── 알고리즘 (프레임당) ──────────────────────────────────────────────────────
  1. α ≥ fg_alpha → 전경.
  2. 배경의 4-연결 성분 중 프레임 테두리에 닿지 않는 것 = 둘러싸인 구멍.
  3. min_hole_px 미만은 잡음으로 무시, 실루엣 대비 max_region_fraction 초과는
     다리/꼬리 사이의 정당한 빈틈일 수 있어 무시한다.
  4. 남은 구멍 면적 비율 ≥ frame_min_fraction **그리고** 개수 ≥ frame_min_count
     이면 그 프레임은 실패.
클립: 균등 간격 표본 중 실패 프레임이 max(min_failed_frames,
ceil(min_failed_ratio·표본 수)) 이상이면 실패 — 한 프레임만으로는 폴백하지 않는다.

임계는 전부 환경 변수로 바꿀 수 있고 기본값은 보수적이다(명백한 다수 구멍만).
단일 사례에 맞춘 값이 아니다 — 실제 클립 분포를 모아 조정할 것.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np

QA_VERSION = "matte-hole-qa-v1"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class HoleQaConfig:
    samples: int = 5
    fg_alpha: float = 0.5
    min_hole_px: int = 4
    max_region_fraction: float = 0.02
    frame_min_fraction: float = 0.01
    frame_min_count: int = 5
    min_failed_frames: int = 2
    min_failed_ratio: float = 0.5

    @classmethod
    def from_env(cls) -> "HoleQaConfig":
        p = "MOTION_DELIVERY_HOLE_QA_"
        d = cls()
        return cls(
            samples=min(5, max(3, int(_env_float(p + "SAMPLES", d.samples)))),
            fg_alpha=_env_float(p + "FG_ALPHA", d.fg_alpha),
            min_hole_px=max(1, int(_env_float(p + "MIN_HOLE_PX", d.min_hole_px))),
            max_region_fraction=_env_float(p + "MAX_REGION_FRACTION", d.max_region_fraction),
            frame_min_fraction=_env_float(p + "FRAME_MIN_FRACTION", d.frame_min_fraction),
            frame_min_count=max(1, int(_env_float(p + "FRAME_MIN_COUNT", d.frame_min_count))),
            min_failed_frames=max(1, int(_env_float(p + "MIN_FAILED_FRAMES", d.min_failed_frames))),
            min_failed_ratio=_env_float(p + "MIN_FAILED_RATIO", d.min_failed_ratio),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def hole_qa_enabled() -> bool:
    return os.getenv("MOTION_DELIVERY_HOLE_QA", "1").strip().lower() in ("1", "true", "yes")


def sample_indices(count: int, samples: int) -> list[int]:
    """[0, count) 에서 균등 간격 표본 (양 끝 포함, 중복 제거)."""
    if count <= 0:
        return []
    if count <= samples:
        return list(range(count))
    return sorted({round(i * (count - 1) / (samples - 1)) for i in range(samples)})


def measure_frame(alpha: np.ndarray, cfg: HoleQaConfig) -> dict[str, Any]:
    """알파 한 장 → 둘러싸인 구멍 지표."""
    import cv2

    fg = np.asarray(alpha, dtype=np.float32) >= cfg.fg_alpha
    h, w = fg.shape[:2]
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(
        (~fg).astype(np.uint8), connectivity=4
    )
    counted: list[int] = []
    enclosed_total = 0
    ignored_large = 0
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue  # 테두리에 닿음 → 바깥 배경
        enclosed_total += area
        counted.append(area)

    silhouette = int(fg.sum()) + enclosed_total
    kept: list[int] = []
    for area in counted:
        if area < cfg.min_hole_px:
            continue
        if silhouette > 0 and area / silhouette > cfg.max_region_fraction:
            ignored_large += 1
            continue
        kept.append(area)

    hole_area = int(sum(kept))
    fraction = (hole_area / silhouette) if silhouette > 0 else 0.0
    failed = (
        silhouette > 0
        and fraction >= cfg.frame_min_fraction
        and len(kept) >= cfg.frame_min_count
    )
    return {
        "silhouette_px": silhouette,
        "hole_count": len(kept),
        "hole_px": hole_area,
        "hole_fraction": round(fraction, 5),
        "ignored_large_gaps": ignored_large,
        "failed": bool(failed),
    }


def evaluate(
    alphas: Sequence[np.ndarray], cfg: Optional[HoleQaConfig] = None
) -> dict[str, Any]:
    """알파 시퀀스 → 클립 판정. passed=False 면 폴백 대상."""
    cfg = cfg or HoleQaConfig.from_env()
    idx = sample_indices(len(alphas), cfg.samples)
    frames = [{"index": i, **measure_frame(alphas[i], cfg)} for i in idx]
    failed = [f["index"] for f in frames if f["failed"]]
    required = max(cfg.min_failed_frames, math.ceil(cfg.min_failed_ratio * len(idx)))
    passed = not idx or len(failed) < required
    return {
        "version": QA_VERSION,
        "passed": passed,
        "sampled_frames": idx,
        "failed_frames": failed,
        "required_failed_frames": required,
        "max_hole_fraction": max((f["hole_fraction"] for f in frames), default=0.0),
        "max_hole_count": max((f["hole_count"] for f in frames), default=0),
        "frames": frames,
        "config": cfg.to_dict(),
    }
