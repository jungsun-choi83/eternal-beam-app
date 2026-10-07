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

── 자연 빈틈 필터 (natural_gap_filter, 기본 꺼짐 — 전시 실행만 켠다) ─────────
다리 사이·배 밑의 빈틈은 바깥 배경과 이어져 있어 구멍이 아니다. 그런데 뒷발이
앞다리에 겹치거나 발끼리 닿으면 그 빈틈이 **얇은 다리(발)로 닫혀** 둘러싸인 구멍이
된다. 면적이 실루엣의 1–2% 라 max_region_fraction(2%) 로도 걸러지지 않는다.
켜면, 아래 셋을 **모두** 만족하는 구멍은 자연 빈틈으로 보고 세지 않는다:
  · 하체: 구멍 위끝이 실루엣 높이의 natural_gap_min_rel_top 아래
  · 길쭉함: 긴 변/짧은 변 ≥ natural_gap_min_elongation (다리 사이 세로 틈, 배 밑 가로 틈)
  · 얇은 바닥: 구멍 바로 아래 몸 두께(열마다, 아래로 빈 공간을 만날 때까지)의 중앙값
          ≤ natural_gap_max_floor × 실루엣 높이 — 겹친 발·뻗은 뒷발처럼 얇은 띠 하나로만
          닫혀 있고 그 아래는 바닥(빈 공간)이다. 몸통을 가로지른 틈은 아래에 몸통·다리가
          통째로 있어 두껍다. (빈틈 폭과 무관하다 — 넓은 다리 사이 빈틈도 된다)
머리·가슴·몸통 속 투명 구멍은 위치나 바닥 두께에서 걸러지지 않으므로 그대로 실패한다.

── 보호 부위 큰 구멍 즉시 실패 (protected_hole_rule, 기본 꺼짐 — 전시 실행만 켠다) ──
위 집계 규칙은 "작은 구멍 여러 개" 용이다. 큰 구멍 **하나**는 개수 조건(≥5)에 못 미쳐
통과하고, 2% 를 넘으면 '큰 빈틈' 으로 아예 빠진다 — 가슴에 뚫린 큰 구멍이 보이지 않는다.
켜면, 자연 빈틈이 아닌 둘러싸인 구멍 중 아래를 만족하는 것이 하나라도 있으면 즉시 실패:
  · 크다: 면적 ≥ hard_min_fraction × 실루엣
  · 보호 부위: (머리·목·가슴 — 위끝이 natural_gap_min_rel_top 위 **그리고**
                깊이 ≥ hard_upper_min_depth)
               **또는** (몸통 덩어리 속 — 깊이 ≥ hard_deep_min_depth, 위치 무관)
깊이 = 구멍 안 최대 "바깥까지 거리" ÷ 실루엣 최대값. 이때 바깥 = 바깥 배경 ∪ 자연 빈틈
(다리 사이 빈틈을 몸으로 채워 깊이를 부풀리지 않는다). 자연 빈틈은 이 규칙 전에 빠진다 —
다리 사이 빈틈은 폭과 무관하게 '얇은 바닥' 으로 가려지므로 깊이 임계가 그것을 지킬 필요가 없다.
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
    # 자연 빈틈 필터 — 기본 꺼짐 (from_env 는 켜지 않는다: 일반 파이프라인 QA 는 그대로).
    natural_gap_filter: bool = False
    natural_gap_min_rel_top: float = 0.45
    natural_gap_max_floor: float = 0.06
    natural_gap_min_elongation: float = 1.5
    # 보호 부위 큰 구멍 즉시 실패 — 기본 꺼짐 (from_env 는 켜지 않는다).
    protected_hole_rule: bool = False
    hard_min_fraction: float = 0.0025
    hard_upper_min_depth: float = 0.25
    hard_deep_min_depth: float = 0.45

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


def _natural_gap_labels(
    fg: np.ndarray,
    labels: np.ndarray,
    stats: np.ndarray,
    exterior_ids: list[int],
    hole_ids: list[int],
    cfg: HoleQaConfig,
) -> set[int]:
    """둘러싸인 구멍 중 다리 사이·배 밑 같은 자연 빈틈의 라벨 (모듈 독스트링 참고).

    바닥 두께는 열마다 구멍의 가장 아래 픽셀 바로 밑에서부터 전경이 이어지는 길이다.
    그 아래가 빈 공간(바깥 배경 또는 다른 후보 빈틈)이어야 바닥으로 친다 — 다른 둘러싸인
    구멍(결함일 수 있다)에서 끊기면 바닥이 아니다.
    """
    rows = np.flatnonzero(fg.any(axis=1))
    if not rows.size:
        return set()
    top, height = int(rows[0]), int(rows[-1] - rows[0] + 1)

    candidates = []
    for i in hole_ids:
        _x, y, bw, bh, _area = (int(v) for v in stats[i])
        if (y - top) / height < cfg.natural_gap_min_rel_top:
            continue
        if max(bw, bh) / max(1, min(bw, bh)) < cfg.natural_gap_min_elongation:
            continue
        candidates.append(i)
    if not candidates:
        return set()

    open_space = (np.isin(labels, exterior_ids) & ~fg) | np.isin(labels, candidates)
    max_floor = cfg.natural_gap_max_floor * height
    h = fg.shape[0]
    natural: set[int] = set()
    for i in candidates:
        x, y, bw, bh, _area = (int(v) for v in stats[i])
        sub = labels[y : y + bh, x : x + bw] == i
        floors = []
        for c in range(bw):
            ys = np.flatnonzero(sub[:, c])
            if not ys.size:
                continue
            col = x + c
            start_y = y + int(ys[-1]) + 1
            below = fg[start_y:, col]
            run = int(np.argmin(below)) if (below.size and not below.all()) else below.size
            end_y = start_y + run
            floors.append(run if end_y < h and open_space[end_y, col] else np.inf)
        if floors and float(np.median(floors)) <= max_floor:
            natural.add(i)
    return natural


def _protected_hard_defects(
    fg: np.ndarray,
    labels: np.ndarray,
    stats: np.ndarray,
    exterior_ids: list[int],
    natural: set[int],
    hole_ids: list[int],
    silhouette: int,
    cfg: HoleQaConfig,
) -> list[dict[str, Any]]:
    """보호 부위(머리·목·가슴·몸통)의 크고 깊은 구멍 (모듈 독스트링 참고)."""
    import cv2

    big = [i for i in hole_ids if i not in natural and int(stats[i][4]) >= cfg.hard_min_fraction * silhouette]
    if not big:
        return []
    rows = np.flatnonzero(fg.any(axis=1))
    top, height = int(rows[0]), int(rows[-1] - rows[0] + 1)
    open_space = (np.isin(labels, exterior_ids) & ~fg) | np.isin(labels, list(natural))
    depth = cv2.distanceTransform((~open_space).astype(np.uint8), cv2.DIST_L2, 5)
    depth_ref = float(depth.max()) or 1.0

    defects = []
    for i in big:
        x, y, bw, bh, area = (int(v) for v in stats[i])
        d = float(depth[labels == i].max()) / depth_ref
        rel_top = (y - top) / height
        upper = rel_top < cfg.natural_gap_min_rel_top and d >= cfg.hard_upper_min_depth
        deep = d >= cfg.hard_deep_min_depth
        if upper or deep:
            defects.append({
                "bbox": [x, y, bw, bh],
                "area_px": area,
                "fraction": round(area / silhouette, 5),
                "rel_top": round(rel_top, 4),
                "depth": round(d, 4),
                "zone": "upper_body" if upper else "deep_torso",
            })
    return defects


def measure_frame(alpha: np.ndarray, cfg: HoleQaConfig) -> dict[str, Any]:
    """알파 한 장 → 둘러싸인 구멍 지표."""
    import cv2

    fg = np.asarray(alpha, dtype=np.float32) >= cfg.fg_alpha
    h, w = fg.shape[:2]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        (~fg).astype(np.uint8), connectivity=4
    )
    counted: list[tuple[int, int]] = []  # (label, area)
    exterior_ids: list[int] = []
    enclosed_total = 0
    ignored_large = 0
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            exterior_ids.append(i)
            continue  # 테두리에 닿음 → 바깥 배경
        enclosed_total += area
        counted.append((i, area))

    silhouette = int(fg.sum()) + enclosed_total
    sized: list[tuple[int, int]] = []
    for label, area in counted:
        if area < cfg.min_hole_px:
            continue
        if silhouette > 0 and area / silhouette > cfg.max_region_fraction:
            ignored_large += 1
            continue
        sized.append((label, area))

    # 큰(>2%) 구멍도 자연 빈틈 여부를 가린다 — 보호 부위 규칙이 큰 다리 사이 빈틈을 잡지 않게.
    candidates = [lb for lb, area in counted if area >= cfg.min_hole_px]
    natural: set[int] = set()
    if cfg.natural_gap_filter and candidates:
        pool = candidates if cfg.protected_hole_rule else [lb for lb, _a in sized]
        natural = _natural_gap_labels(fg, labels, stats, exterior_ids, pool, cfg)
    kept = [area for label, area in sized if label not in natural]
    ignored_natural = sum(1 for label, _a in sized if label in natural)
    hard: list[dict[str, Any]] = []
    if cfg.protected_hole_rule and candidates and silhouette > 0:
        hard = _protected_hard_defects(fg, labels, stats, exterior_ids, natural, candidates, silhouette, cfg)

    hole_area = int(sum(kept))
    fraction = (hole_area / silhouette) if silhouette > 0 else 0.0
    failed = (
        silhouette > 0
        and fraction >= cfg.frame_min_fraction
        and len(kept) >= cfg.frame_min_count
    ) or bool(hard)
    out = {
        "silhouette_px": silhouette,
        "hole_count": len(kept),
        "hole_px": hole_area,
        "hole_fraction": round(fraction, 5),
        "ignored_large_gaps": ignored_large,
        "failed": bool(failed),
    }
    if cfg.natural_gap_filter:
        out["ignored_natural_gaps"] = ignored_natural
    if cfg.protected_hole_rule:
        out["protected_hard_defects"] = hard
    return out


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
