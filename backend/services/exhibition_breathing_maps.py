"""
전시(Exhibition) 준비 실행 — 최종 알파 한 장에서 호흡 가중치/잠금 맵을 만든다.

── 위치 ─────────────────────────────────────────────────────────────────────
exhibition_prep_service 가 vitmatte_service.matte_foreground_with_meta() 로 얻은
RGBA 를 넘기면, 이 모듈이 **추가 모델 추론 없이** OpenCV/NumPy 만으로
  - breathing_weight : 0..1, 호흡 변형을 얼마나 받을지 (저주파, 몸통 중심)
  - locked           : 0..1, 1 = 변형 금지 (머리 + 지면 접촉부)
  - anchors          : breathing_center / breathing_axis / ground_y / head_point
를 만든다. 일반 펫 생성 파이프라인(정본·키프레임·모션·QA·발행)과는 무관하다.

── 방법 (dt-torso-v2) ───────────────────────────────────────────────────────
  1. 알파 ≥ 128 → 실루엣, 가장 큰 연결 성분, 구멍 메우기
  2. 거리 변환 D (가장자리에서 멀수록 큼 — 다리/꼬리/귀처럼 얇은 부위는 작다)
  3. 반경 r = OPEN_RADIUS_FRAC·max(D) 열기(opening)로 얇은 부위를 걷어낸 코어
  4. 머리 — 세 등급 (estimate_head):
       HIGH_CONFIDENCE   D 의 두 번째 봉우리가 "목 안장점" 으로 뚜렷이 잘록하게 이어짐
                         → 표준 머리 잠금 (v1 규칙 그대로)
       USABLE_FALLBACK   잘록함은 약해도(털·가슴털·정면 자세) 머리 자리가 그럴듯함
                         · 머리만 한 봉우리가 실루엣 위쪽·몸통 위에 있다, 또는
                         · 봉우리가 없어도 실루엣 모양(세로 자세의 위쪽 끝 / 측면의 솟은 혹)
                         → 더 넓은 보수적 잠금(머리·목·윗가슴), AUTO READY
       NO_PLAUSIBLE_HEAD 근거 없음 → head_point 없음 + 위쪽 예방 잠금, NEEDS_REVIEW
     불확실할수록 **더 많이 잠그고 덜 움직인다** — 약한 추정이 얼굴에 호흡 가중치를 주지 않는다.
  5. 잠금 = 머리(또는 예방) 영역 ∪ 실루엣 아래 GROUND_BAND_FRAC 띠(발/지면 접촉), 페더링
  6. 가중치 = smoothstep(D) × 흐린 몸통 × 몸통 중심 가우시안, 다시 흐림,
     (1 − 잠금) 과 알파를 곱해 0..1 로 정규화

맵은 긴 변 WORK_MAX_SIDE 로 줄여 계산하고 캔버스 크기로 올린다 — 어차피
저주파 맵이라 품질 손실이 없고, 12MP 입력에서 float 배열 여러 장을 잡지 않는다.

좌표계: 캔버스 픽셀, 원점 좌상단, x 오른쪽, y 아래쪽.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np

from .matte_hole_qa import HoleQaConfig, measure_frame

MAP_METHOD = "dt-torso-v2"

#: 맵 계산 해상도 상한 (긴 변 px).
WORK_MAX_SIDE = 1024
#: 실루엣 판정 알파 (0..255).
SOLID_ALPHA = 128
#: "피사체가 있다" 판정 알파 — vitmatte_service.ALPHA_PRESENCE_THRESHOLD 기본값과 같다.
PRESENCE_ALPHA = 16
#: 코어 추출 열기 반경 = 이 비율 × max(D). 다리(반폭 ≈ 0.3·D)는 지우고 목은 남기는 값.
OPEN_RADIUS_FRAC = 0.3
#: 코어 성분 최소 면적(실루엣 대비) — 머리가 열기로 떨어져 나가도 버리지 않게.
CORE_MIN_COMPONENT_FRAC = 0.02
#: 지면 접촉 잠금 띠 높이 (실루엣 높이 대비).
GROUND_BAND_FRAC = 0.15
#: 머리 후보 봉우리: D ≥ 이 비율 × max(D) (다리·꼬리·귀 봉우리는 여기서 빠진다).
HEAD_PEAK_MIN_FRAC = 0.3
#: 몸통 봉우리와 이보다 가까운(× max(D)) 봉우리는 같은 몸통으로 본다.
HEAD_MIN_PEAK_SEPARATION_FRAC = 0.8
#: 검사할 봉우리 수 상한.
HEAD_MAX_PEAKS = 6
#: 이 신뢰도 미만이면 head_low_confidence. 잘록함 확신·그럴듯함 점수의 통과선이기도 하다.
HEAD_MIN_CONFIDENCE = 0.5
#: 두 방향의 잘록함이 이만큼 이내로 비슷하면 "애매함" 으로 보고 신뢰도를 깎는다.
HEAD_AMBIGUITY_MARGIN = 0.05
#: 그럴듯한 봉우리 둘의 점수가 이만큼 이내면 둘 다 잠근다.
HEAD_PLAUSIBILITY_AMBIGUITY = 0.1
#: USABLE_FALLBACK 목 경계: 머리 중심에서 이 배수 × D_p (표준은 1.0 × D_p).
FALLBACK_NECK_CUT_FRAC = 1.35
#: …단 목 경계는 몸통 봉우리에서 최소 이 비율 × max(D) 만큼 머리 쪽에 둔다.
FALLBACK_TORSO_KEEP_FRAC = 0.35
#: 실루엣 높이/너비가 이 이상이면 세로 자세(정면·앉음)로 본다.
GEOM_UPRIGHT_MIN_ASPECT = 1.15
#: 세로 자세 실루엣 모양 대체 / 예방 잠금: 실루엣 위쪽 이 비율을 잠근다.
GEOM_UPRIGHT_LOCK_FRAC = 0.5
#: 측면 자세 머리 혹 잠금을 몸통 쪽으로 이 비율 × max(D) 더 넓힌다.
GEOM_SIDE_EXTEND_FRAC = 0.35

HEAD_TIER_HIGH = "HIGH_CONFIDENCE"
HEAD_TIER_FALLBACK = "USABLE_FALLBACK"
HEAD_TIER_NONE = "NO_PLAUSIBLE_HEAD"
HEAD_TIER_OPERATOR = "OPERATOR"
#: 프레임 테두리 접촉 판정: 테두리 margin 안의 실루엣 픽셀이 min_px 이상.
EDGE_MARGIN_PX = 2
EDGE_MIN_PX = 3
#: 크롭 여백 (긴 변 대비, 최소 px).
CROP_PAD_FRAC = 0.04
CROP_MIN_PAD_PX = 16
#: 전시 interior_holes QA. 정지 전신 사진이라 (1) 발 겹침으로 닫힌 다리 사이·배 밑 빈틈은
#: 구멍으로 세지 않고 (2) 머리·목·가슴·몸통의 크고 깊은 구멍은 하나라도 즉시 실패시킨다.
#: 일반 파이프라인(HoleQaConfig.from_env)은 둘 다 끈 채 그대로다 — matte_hole_qa 참고.
EXHIBITION_HOLE_QA = HoleQaConfig(natural_gap_filter=True, protected_hole_rule=True)

class ExhibitionMapError(Exception):
    """맵을 만들 수 없을 때 (빈 알파, 몸통 미검출 등). code 는 실행 error_code 로 간다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class HeadEstimate:
    xy: Optional[tuple[float, float]]
    confidence: float
    source: str  # "auto" | "operator" | "none"
    direction: Optional[str] = None
    constriction: float = 0.0
    region: Optional[np.ndarray] = field(default=None, repr=False)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    tier: str = HEAD_TIER_NONE
    method: str = "none"
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class MapResult:
    """캔버스 해상도 맵 + 앵커 + 판정 재료."""

    breathing_weight: np.ndarray = field(repr=False)  # float32 (H, W) 0..1
    locked: np.ndarray = field(repr=False)  # float32 (H, W) 0..1
    breathing_center: tuple[float, float]
    breathing_axis: tuple[float, float]
    ground_y: float
    head: HeadEstimate
    interior_holes: bool
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def head_low_confidence(self) -> bool:
        return (
            self.head.xy is None
            or self.head.tier == HEAD_TIER_NONE
            or self.head.confidence < HEAD_MIN_CONFIDENCE
        )


# ── 크롭 · 테두리 ────────────────────────────────────────────────────────────


def subject_crop_rect(alpha: np.ndarray) -> tuple[int, int, int, int]:
    """알파가 있는 영역 + 여백 → (x1, y1, x2, y2), 끝은 배타. 알파가 비면 ExhibitionMapError."""
    h, w = alpha.shape[:2]
    present = alpha >= PRESENCE_ALPHA
    cols = np.flatnonzero(present.any(axis=0))
    rows = np.flatnonzero(present.any(axis=1))
    if not cols.size or not rows.size:
        raise ExhibitionMapError("MAPS_EMPTY_ALPHA", "Alpha has no visible subject.")
    pad = max(CROP_MIN_PAD_PX, int(round(CROP_PAD_FRAC * max(h, w))))
    return (
        max(0, int(cols[0]) - pad),
        max(0, int(rows[0]) - pad),
        min(w, int(cols[-1]) + 1 + pad),
        min(h, int(rows[-1]) + 1 + pad),
    )


def detect_frame_edge_touch(
    alpha: np.ndarray, *, margin_px: int = EDGE_MARGIN_PX, min_px: int = EDGE_MIN_PX
) -> dict[str, Any]:
    """**원본 프레임** 기준 알파가 테두리에 닿는가 — 몸이 잘린 사진(전신 아님) 판정."""
    solid = alpha >= SOLID_ALPHA
    m = max(1, int(margin_px))
    bands = {
        "top": solid[:m, :],
        "bottom": solid[-m:, :],
        "left": solid[:, :m],
        "right": solid[:, -m:],
    }
    sides = [name for name, band in bands.items() if int(band.sum()) >= min_px]
    return {"touches_frame_edge": bool(sides), "sides": sides}


# ── 실루엣 · 코어 ────────────────────────────────────────────────────────────


def _largest_component(mask: np.ndarray) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros(mask.shape, dtype=bool)
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == biggest


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """테두리에서 닿지 않는 배경 = 구멍 → 채운다."""
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
    padded[1:-1, 1:-1] = mask.astype(np.uint8) * 255
    flood = padded.copy()
    ff_mask = np.zeros((h + 4, w + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    holes = flood[1:-1, 1:-1] == 0
    return mask | holes


def solid_silhouette(alpha_u8: np.ndarray) -> np.ndarray:
    return _fill_holes(_largest_component(alpha_u8 >= SOLID_ALPHA))


def _dilate_disk(mask: np.ndarray, radius: float) -> np.ndarray:
    """원판 팽창 — 거리 변환 임계로 계산 (큰 반경에서도 O(N), 타원 커널보다 수십 배 빠름)."""
    if radius <= 0:
        return mask.astype(bool)
    outside = cv2.distanceTransform((~mask.astype(bool)).astype(np.uint8), cv2.DIST_L2, 5)
    return outside <= float(radius)


def _open_disk(mask: np.ndarray, radius: float) -> np.ndarray:
    """원판 열기 = 침식(D > r) 후 팽창."""
    inside = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    return _dilate_disk(inside > float(radius), radius) & mask.astype(bool)


def _core(solid: np.ndarray, d_max: float) -> np.ndarray:
    """열기로 얇은 부위(다리·꼬리·귀)를 걷어낸 코어. 큰 성분은 모두 유지."""
    opened = _open_disk(solid, max(1.0, OPEN_RADIUS_FRAC * d_max))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(opened.astype(np.uint8), connectivity=8)
    min_area = CORE_MIN_COMPONENT_FRAC * max(1, int(solid.sum()))
    keep = np.zeros_like(opened)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep |= labels == i
    return keep if keep.any() else solid.copy()


# ── 머리 ─────────────────────────────────────────────────────────────────────
#
# 거리 변환 D 의 "봉우리" 로 머리를 찾는다. 몸통은 D 의 전역 최대(p0)이고, 머리는
# 목으로 이어진 두 번째 봉우리(p, 높이 D_p)다. 두 봉우리를 잇는 경로 중 D 가 가장
# 덜 내려가는 경로의 최저값(안장점 τ*)이 목 두께의 절반이다. 잘록함
# c = 1 − τ*/D_p 가 크면 "머리가 목으로 몸통에 붙어 있다" 는 증거가 강하다.
# 방향(위/옆)을 가정하지 않으므로 정면·측면 모두 같은 규칙이다.


def _peaks(dist: np.ndarray, d_max: float) -> list[tuple[int, int, float]]:
    """국소 최대 (x, y, D) — D ≥ HEAD_PEAK_MIN_FRAC·max(D), 높은 순 상위 K 개."""
    r = max(3, int(round(0.25 * d_max)))
    dil = cv2.dilate(dist, np.ones((2 * r + 1, 2 * r + 1), np.uint8))  # 사각 창 = 분리형, 빠름
    peak_mask = ((dist >= dil - 1e-4) & (dist >= HEAD_PEAK_MIN_FRAC * d_max)).astype(np.uint8)
    n, labels = cv2.connectedComponents(peak_mask, connectivity=8)
    out: list[tuple[int, int, float]] = []
    for i in range(1, n):
        ys, xs = np.nonzero(labels == i)
        k = int(np.argmax(dist[ys, xs]))
        out.append((int(xs[k]), int(ys[k]), float(dist[ys[k], xs[k]])))
    out.sort(key=lambda t: t[2], reverse=True)
    return out[:HEAD_MAX_PEAKS]


def _saddle(dist: np.ndarray, a: tuple[int, int], b: tuple[int, int], hi: float) -> float:
    """a, b 가 {D ≥ τ} 에서 연결되는 최대 τ (이진 탐색)."""
    lo, top = 0.0, hi
    for _ in range(14):
        mid = 0.5 * (lo + top)
        _n, labels = cv2.connectedComponents((dist >= mid).astype(np.uint8), connectivity=8)
        la, lb = labels[a[1], a[0]], labels[b[1], b[0]]
        if la != 0 and la == lb:
            lo = mid
        else:
            top = mid
    return lo


def _confidence(constriction: float) -> float:
    """잘록함 → 해부학적 확신 (0..1). HIGH_CONFIDENCE 판정에만 쓴다."""
    return float(np.clip((constriction - 0.1) / 0.25, 0.0, 1.0))


def _ramp(x: float, lo: float, hi: float) -> float:
    """lo → 0, hi → 1 선형 (lo > hi 이면 감소 방향)."""
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def _banded_confidence(tier: str, score: float) -> float:
    """등급별 대역으로 신뢰도를 매긴다 — 숫자 하나로 정렬되고 0.5 경계의 뜻이 유지된다.

    HIGH_CONFIDENCE [0.75, 1.0] · USABLE_FALLBACK [0.5, 0.75) · NO_PLAUSIBLE_HEAD [0, 0.5)
    """
    s = float(np.clip(score, 0.0, 1.0))
    if tier == HEAD_TIER_HIGH:
        return round(0.75 + 0.5 * (max(s, 0.5) - 0.5), 4)
    if tier == HEAD_TIER_FALLBACK:
        return round(0.5 + 0.24 * (max(s, 0.5) - 0.5) / 0.5, 4)
    return round(min(s, 0.49) * 0.5 / 0.49 if s > 0 else 0.0, 4)


@dataclass
class _Shape:
    top: int
    bottom: int
    left: int
    right: int
    height: int
    width: int
    aspect: float  # 높이 / 너비
    solidity: float  # 면적 / 볼록 껍질 면적 — 1 에 가까우면 형체 없는 덩어리


def _shape_stats(solid: np.ndarray) -> _Shape:
    rows = np.flatnonzero(solid.any(axis=1))
    cols = np.flatnonzero(solid.any(axis=0))
    top, bottom, left, right = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    height, width = bottom - top + 1, right - left + 1
    contours, _ = cv2.findContours(solid.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull_area = cv2.contourArea(cv2.convexHull(max(contours, key=cv2.contourArea))) if contours else 0.0
    solidity = float(solid.sum()) / hull_area if hull_area > 0 else 1.0
    return _Shape(top, bottom, left, right, height, width, height / float(width), min(1.0, solidity))


def _candidate_plausibility(
    xy: tuple[int, int], d_p: float, p0: tuple[int, int], d_max: float, shape: _Shape
) -> dict[str, float]:
    """DT 봉우리가 '머리 자리' 에 있는가 — 잘록함과 무관한 위치·크기 근거.

    · height : 실루엣 위쪽에 있다 (상대 y ≤ 0.30 → 1, ≥ 0.55 → 0)
    · above  : 몸통 봉우리보다 위 (Δy ≥ 0.5·D → 1, ≤ −0.25·D → 0)
    · size   : 머리만 한 굵기 (D_p ≥ 0.6·D → 1, ≤ 0.35·D → 0) — 꼬리·발·귀는 여기서 빠진다
    score = 셋 중 최솟값 — 하나라도 어긋나면 머리 자리가 아니다.
    """
    rel_y = (xy[1] - shape.top) / float(shape.height)
    terms = {
        "height": _ramp(rel_y, 0.55, 0.30),
        "above": _ramp((p0[1] - xy[1]) / d_max, -0.25, 0.5),
        "size": _ramp(d_p / d_max, 0.35, 0.6),
    }
    terms = {k: round(v, 4) for k, v in terms.items()}
    terms["rel_y"] = round(rel_y, 4)
    terms["score"] = min(terms["height"], terms["above"], terms["size"])
    return terms


def _half_plane_region(
    solid: np.ndarray, p: tuple[float, float], u: tuple[float, float], cut: float
) -> np.ndarray:
    """p 에서 −u 방향으로 cut 만큼 간 점을 지나는, u 에 수직인 직선 너머(머리 쪽) 실루엣 전체."""
    qx, qy = p[0] - u[0] * cut, p[1] - u[1] * cut
    yy, xx = np.mgrid[0 : solid.shape[0], 0 : solid.shape[1]]
    return solid & ((xx - qx) * u[0] + (yy - qy) * u[1] >= 0)


def _conservative_candidate_region(
    solid: np.ndarray, c: dict[str, Any], p0: tuple[int, int], d_max: float, shape: _Shape
) -> np.ndarray:
    """USABLE_FALLBACK 잠금: 목 경계를 몸통 쪽으로 더 내려 그 너머(머리·목·윗가슴)를 통째로 잠근다.

    목 경계를 머리 중심에서 FALLBACK_NECK_CUT_FRAC·D_p 로 내리되, 몸통 봉우리에서
    최소 FALLBACK_TORSO_KEEP_FRAC·D 만큼 머리 쪽에 남겨 몸통을 지킨다. 세로 자세에서
    머리가 위에 있으면 경계를 수평으로 둔다 — 봉우리가 털·자세로 옆으로 치우쳐도
    잠금이 비스듬히 얼굴 한쪽을 남기지 않게.
    """
    p = c["xy"]
    vx, vy = p[0] - p0[0], p[1] - p0[1]
    if shape.aspect >= GEOM_UPRIGHT_MIN_ASPECT and c["direction"] == "up":
        vx = 0
    sep = abs(vy) if vx == 0 else math.hypot(vx, vy)
    sep = sep or 1.0
    u = (vx / sep, vy / sep)
    d_p = c["peak_d"]
    cut = max(d_p, min(FALLBACK_NECK_CUT_FRAC * d_p, sep - FALLBACK_TORSO_KEEP_FRAC * d_max))
    return _half_plane_region(solid, p, u, cut)


def _geometric_fallback(
    solid: np.ndarray, core: np.ndarray, d_max: float, shape: _Shape
) -> dict[str, Any]:
    """DT 봉우리로 머리를 못 찾았을 때 — 실루엣 모양만으로 '머리가 있을 자리' 를 잡는다.

    upright (세로로 선 정면·앉은 자세, 털북숭이·둥근 몸, 옷 입은 펫):
      머리는 위쪽 끝에 몸통과 붙어 있다. 위쪽 띠가 몸통 최대 폭보다 좁아지고(taper),
      실루엣이 형체 없는 덩어리가 아니면(solidity — 귀·다리·꼬리·목이 오목부를 만든다)
      실루엣 위 GEOM_UPRIGHT_LOCK_FRAC 를 통째로 잠근다.
    side (가로로 긴 측면 자세):
      코어 윗선이 한쪽 끝에서 등선보다 솟은 혹(lobe) = 머리. 그 끝을 통째로 잠근다.
    결과 score < 0.5 면 NO_PLAUSIBLE_HEAD.
    """
    top, h = shape.top, shape.height
    terms: dict[str, Any] = {
        "aspect": round(shape.aspect, 4),
        "solidity": round(shape.solidity, 4),
        "solidity_term": round(_ramp(shape.solidity, 0.97, 0.91), 4),
    }
    yy, xx = np.mgrid[0 : solid.shape[0], 0 : solid.shape[1]]

    def _core_width(q0: float, q1: float) -> float:
        band = core[int(top + q0 * h) : int(top + q1 * h) + 1]
        widths = [np.ptp(np.flatnonzero(r)) + 1 for r in band if r.any()]
        return float(np.median(widths)) if widths else 0.0

    if shape.aspect >= GEOM_UPRIGHT_MIN_ASPECT:
        body_w = max(
            (np.ptp(np.flatnonzero(core[y])) + 1 for y in range(int(top + 0.3 * h), int(top + 0.75 * h) + 1)
             if core[y].any()),
            default=0,
        )
        top_w = _core_width(0.05, 0.15)
        taper = top_w / body_w if body_w else 1.0
        terms.update(
            mode="upright",
            aspect_term=round(_ramp(shape.aspect, 1.0, 1.3), 4),
            taper=round(taper, 4),
            taper_term=round(_ramp(taper, 1.0, 0.7), 4),
        )
        score = min(terms["aspect_term"], terms["solidity_term"], terms["taper_term"])
        y_cut = top + GEOM_UPRIGHT_LOCK_FRAC * h
        region = solid & (yy < y_cut)
        face = core & (yy >= top + 0.1 * h) & (yy <= top + 0.3 * h)
        if not face.any():
            face = region
        fy, fx = np.nonzero(face)
        xy = (float(np.median(fx)), float(np.median(fy)))
        direction = "up"
    else:
        cols = np.flatnonzero(core.any(axis=0))
        tops = np.full(solid.shape[1], np.inf)
        tops[cols] = np.argmax(core[:, cols], axis=0)
        cl, cr = int(cols[0]), int(cols[-1])
        wc = cr - cl + 1
        mid = tops[cl + int(0.3 * wc) : cl + int(0.7 * wc) + 1]
        back = float(np.median(mid[np.isfinite(mid)])) if np.isfinite(mid).any() else float(top)
        ground_top = shape.bottom + 1 - max(2, int(round(GROUND_BAND_FRAC * h)))
        reach = GEOM_SIDE_EXTEND_FRAC * d_max
        ends = []
        for side, sl in (("left", slice(cl, cl + int(0.35 * wc) + 1)), ("right", slice(cr - int(0.35 * wc), cr + 1))):
            seg_cols = np.arange(sl.start, sl.stop)
            seg = tops[seg_cols]
            if not np.isfinite(seg).any():
                continue
            t_min = float(np.min(seg))
            # 등선 위로 솟은 높이를 몸 두께(max D)로 정규화 — 다리 길이에 좌우되지 않는다.
            lift = (back - t_min) / d_max
            lobe = seg_cols[seg <= back - 0.5 * (back - t_min)]
            ends.append({"side": side, "lift": lift, "term": _ramp(lift, 0.1, 0.35),
                         "t_min": t_min, "lobe": lobe if lobe.size else seg_cols})
        ends.sort(key=lambda e: e["lift"], reverse=True)
        head_end = ends[0] if ends else {"side": "left", "lift": 0.0, "term": 0.0, "t_min": float(top),
                                         "lobe": np.arange(cl, cl + 1)}
        terms.update(mode="side", side=head_end["side"], lift=round(head_end["lift"], 4),
                     lift_term=round(head_end["term"], 4))
        score = min(terms["lift_term"], terms["solidity_term"])
        region = np.zeros_like(solid)
        locked_ends = []
        for e in ends:
            # 양 끝이 다 솟았으면(머리 vs 엉덩이) 어느 쪽인지 모른다 — 둘 다 잠근다.
            if e is head_end or e["term"] >= HEAD_MIN_CONFIDENCE:
                if e["side"] == "left":
                    region |= solid & (xx <= float(e["lobe"].max()) + reach)
                else:
                    region |= solid & (xx >= float(e["lobe"].min()) - reach)
                locked_ends.append(e["side"])
        region &= yy < ground_top
        terms["locked_ends"] = locked_ends
        face = core & region & (yy <= head_end["t_min"] + 0.8 * d_max)
        if head_end["side"] == "left":
            face &= xx <= float(head_end["lobe"].max()) + reach
        else:
            face &= xx >= float(head_end["lobe"].min()) - reach
        if not face.any():
            face = region if region.any() else solid
        fy, fx = np.nonzero(face)
        xy = (float(np.median(fx)), float(np.median(fy)))
        direction = head_end["side"]
    terms["score"] = round(float(score), 4)
    return {"xy": xy, "region": region, "direction": direction, "terms": terms}


def _precautionary_region(solid: np.ndarray, shape: _Shape) -> np.ndarray:
    """NO_PLAUSIBLE_HEAD 여도 얼굴이 숨쉬지 않게 — 실루엣 위쪽을 잠근다 (운영자 검토 전까지의 안전값)."""
    yy = np.arange(solid.shape[0])[:, None]
    return solid & (yy < shape.top + GEOM_UPRIGHT_LOCK_FRAC * shape.height)


def _head_region(
    solid: np.ndarray,
    dist: np.ndarray,
    p: tuple[int, int],
    p0: tuple[int, int],
    d_p: float,
    saddle: float,
) -> np.ndarray:
    """머리 봉우리 쪽 실루엣(귀 포함): 목 너머 반평면 ∩ 머리 근방."""
    _n, labels = cv2.connectedComponents((dist > saddle * 1.02 + 0.5).astype(np.uint8), connectivity=8)
    lab = labels[p[1], p[0]]
    blob = labels == lab if lab != 0 else np.zeros_like(solid)
    blob[p[1], p[0]] = True
    grown = _dilate_disk(blob, saddle + 0.35 * d_p)
    # 목 경계: 머리 중심에서 몸통 쪽으로 D_p 만큼 간 점을 지나는, p0→p 에 수직인 직선.
    vx, vy = p[0] - p0[0], p[1] - p0[1]
    norm = math.hypot(vx, vy) or 1.0
    ux, uy = vx / norm, vy / norm
    qx, qy = p[0] - ux * d_p, p[1] - uy * d_p
    yy, xx = np.mgrid[0 : solid.shape[0], 0 : solid.shape[1]]
    beyond = (xx - qx) * ux + (yy - qy) * uy >= 0
    return solid & grown & beyond


def _direction_label(p: tuple[int, int], p0: tuple[int, int]) -> str:
    dx, dy = p[0] - p0[0], p[1] - p0[1]
    if abs(dy) >= abs(dx):
        return "up" if dy < 0 else "down"
    return "left" if dx < 0 else "right"


def estimate_head(
    solid: np.ndarray,
    dist: np.ndarray,
    *,
    hint_xy: Optional[tuple[float, float]] = None,
    core: Optional[np.ndarray] = None,
) -> HeadEstimate:
    """머리 위치 + 잠금 영역을 등급과 함께 돌려준다.

      HIGH_CONFIDENCE   : 목 잘록함이 분명한 DT 봉우리 → 표준 머리 잠금 (기존 규칙 그대로)
      USABLE_FALLBACK   : 잘록함은 약하지만 머리 자리가 그럴듯함 → 더 넓은 보수적 잠금, AUTO READY
      NO_PLAUSIBLE_HEAD : 근거 없음 → head_point 없음, 위쪽 예방 잠금, NEEDS_REVIEW
    불확실할수록 **더 많이 잠그고 덜 움직인다.**
    """
    d_max = float(dist.max())
    y0, x0 = np.unravel_index(int(np.argmax(dist)), dist.shape)
    p0 = (int(x0), int(y0))
    min_sep = HEAD_MIN_PEAK_SEPARATION_FRAC * d_max
    shape = _shape_stats(solid)
    if core is None:
        core = _core(solid, d_max)

    cands: list[dict[str, Any]] = []
    for x, y, d_p in _peaks(dist, d_max):
        if math.hypot(x - p0[0], y - p0[1]) < min_sep:
            continue
        saddle = _saddle(dist, (x, y), p0, d_p)
        raw = 1.0 - saddle / max(d_p, 1e-6)
        score = raw
        if y > p0[1]:
            # 몸통 중심보다 낮은 봉우리(엉덩이·뒷다리 허벅지)는 머리일 가능성이 낮다.
            score *= 0.5
        cands.append(
            {
                "xy": (x, y),
                "peak_d": round(d_p, 2),
                "saddle_d": round(saddle, 2),
                "raw_constriction": round(raw, 4),
                "constriction": round(score, 4),
                "constriction_confidence": round(_confidence(score), 4),
                "separation_d": round(math.hypot(x - p0[0], y - p0[1]) / d_max, 4),
                "direction": _direction_label((x, y), p0),
                "plausibility": _candidate_plausibility((x, y), d_p, p0, d_max, shape),
            }
        )
    cands.sort(key=lambda c: c["constriction"], reverse=True)
    public = [dict(c, xy=[int(c["xy"][0]), int(c["xy"][1])]) for c in cands]

    def _region(c: dict[str, Any]) -> np.ndarray:
        return _head_region(solid, dist, c["xy"], p0, c["peak_d"], c["saddle_d"])

    def _diag(**kw: Any) -> dict[str, Any]:
        return {
            "torso_peak": [p0[0], p0[1]],
            "d_max": round(d_max, 2),
            "shape": {"aspect": round(shape.aspect, 4), "solidity": round(shape.solidity, 4)},
            **kw,
        }

    if hint_xy is not None:
        hx, hy = float(hint_xy[0]), float(hint_xy[1])
        ix, iy = int(round(hx)), int(round(hy))
        inside = 0 <= iy < solid.shape[0] and 0 <= ix < solid.shape[1]
        for c in cands:
            region = _region(c)
            if inside and region[iy, ix]:
                return HeadEstimate(
                    xy=(hx, hy), confidence=1.0, source="operator",
                    direction=c["direction"], constriction=c["constriction"],
                    region=region, candidates=public, tier=HEAD_TIER_OPERATOR, method="operator_hint",
                    diagnostics=_diag(),
                )
        yy, xx = np.ogrid[: solid.shape[0], : solid.shape[1]]
        disk = (xx - hx) ** 2 + (yy - hy) ** 2 <= (0.8 * d_max) ** 2
        return HeadEstimate(
            xy=(hx, hy), confidence=1.0, source="operator", region=solid & disk, candidates=public,
            tier=HEAD_TIER_OPERATOR, method="operator_hint", diagnostics=_diag(),
        )

    # 1) HIGH_CONFIDENCE — 기존 잘록함 규칙 (합성·뚜렷한 목). 잠금도 기존 표준 영역.
    if cands:
        best = cands[0]
        cc = _confidence(best["constriction"])
        ambiguous = len(cands) > 1 and cands[1]["constriction"] >= best["constriction"] - HEAD_AMBIGUITY_MARGIN
        if ambiguous:
            # 봉우리 둘 다 "머리 같다" — 어느 쪽인지 모른다.
            cc *= 0.6
        if cc >= HEAD_MIN_CONFIDENCE:
            return HeadEstimate(
                xy=(float(best["xy"][0]), float(best["xy"][1])),
                confidence=_banded_confidence(HEAD_TIER_HIGH, cc),
                source="auto",
                direction=best["direction"],
                constriction=best["constriction"],
                region=_region(best),
                candidates=public,
                tier=HEAD_TIER_HIGH,
                method="dt_neck_saddle",
                diagnostics=_diag(constriction_confidence=round(cc, 4), ambiguous=ambiguous,
                                  reason="clear neck constriction"),
            )

    # 2) USABLE_FALLBACK (봉우리) — 잘록함은 약해도 위치·크기가 머리 자리인 봉우리.
    plausible = [c for c in cands if c["plausibility"]["score"] >= HEAD_MIN_CONFIDENCE]
    if plausible:
        plausible.sort(key=lambda c: (c["plausibility"]["score"], c["peak_d"]), reverse=True)
        best = plausible[0]
        region = _conservative_candidate_region(solid, best, p0, d_max, shape)
        locked_extra = []
        for other in plausible[1:]:
            if other["plausibility"]["score"] >= best["plausibility"]["score"] - HEAD_PLAUSIBILITY_AMBIGUITY:
                # 어느 쪽이 머리인지 애매하면 둘 다 잠근다 (lock more, animate less).
                region |= _conservative_candidate_region(solid, other, p0, d_max, shape)
                locked_extra.append([int(other["xy"][0]), int(other["xy"][1])])
        s = best["plausibility"]["score"]
        return HeadEstimate(
            xy=(float(best["xy"][0]), float(best["xy"][1])),
            confidence=_banded_confidence(HEAD_TIER_FALLBACK, s),
            source="auto",
            direction=best["direction"],
            constriction=best["constriction"],
            region=region,
            candidates=public,
            tier=HEAD_TIER_FALLBACK,
            method="dt_peak_position",
            diagnostics=_diag(
                plausibility=best["plausibility"], also_locked=locked_extra,
                reason="head-sized DT peak in upper head zone; neck constriction weak",
            ),
        )

    # 3) USABLE_FALLBACK (실루엣 모양) — 머리가 몸통과 한 덩어리라 봉우리가 없을 때.
    geo = _geometric_fallback(solid, core, d_max, shape)
    if geo["terms"]["score"] >= HEAD_MIN_CONFIDENCE:
        return HeadEstimate(
            xy=geo["xy"],
            confidence=_banded_confidence(HEAD_TIER_FALLBACK, geo["terms"]["score"]),
            source="auto",
            direction=geo["direction"],
            constriction=0.0,
            region=geo["region"],
            candidates=public,
            tier=HEAD_TIER_FALLBACK,
            method=f"silhouette_{geo['terms']['mode']}",
            diagnostics=_diag(geometry=geo["terms"],
                              reason="no separate head peak; head located from silhouette shape"),
        )

    # 4) NO_PLAUSIBLE_HEAD — 머리를 지어내지 않는다. 그래도 위쪽은 예방적으로 잠근다.
    best_score = max([geo["terms"]["score"]] + [c["plausibility"]["score"] for c in cands])
    return HeadEstimate(
        xy=None,
        confidence=_banded_confidence(HEAD_TIER_NONE, best_score),
        source="none",
        candidates=public,
        region=_precautionary_region(solid, shape),
        tier=HEAD_TIER_NONE,
        method="none",
        diagnostics=_diag(geometry=geo["terms"],
                          reason="no head-like peak and silhouette has no head-like structure"),
    )


# ── 가중치 · 잠금 ────────────────────────────────────────────────────────────


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _blur(arr: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(arr.astype(np.float32), (0, 0), sigmaX=max(0.5, float(sigma)))


def _breathing_axis(torso: np.ndarray) -> tuple[float, float]:
    """몸통 주축(척추 방향)에 수직, 위쪽(y<0)을 향하는 단위 벡터."""
    ys, xs = np.nonzero(torso)
    if len(xs) < 3:
        return (0.0, -1.0)
    cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
    evals, evecs = np.linalg.eigh(cov)  # 오름차순
    if evals[0] <= 0 or math.sqrt(evals[1] / evals[0]) < 1.15:
        return (0.0, -1.0)  # 거의 둥글다 — 방향을 지어내지 않는다
    minor = evecs[:, 0]
    if minor[1] > 0:
        minor = -minor
    return (round(float(minor[0]), 4), round(float(minor[1]), 4))


def build_breathing_maps(
    rgba: np.ndarray,
    *,
    head_hint_xy: Optional[tuple[float, float]] = None,
) -> MapResult:
    """캔버스 RGBA(uint8, H×W×4) → MapResult. head_hint_xy 는 캔버스 좌표."""
    alpha_u8 = rgba[:, :, 3]
    H, W = alpha_u8.shape
    scale = min(1.0, WORK_MAX_SIDE / float(max(H, W)))
    if scale < 1.0:
        wa = cv2.resize(alpha_u8, (max(1, round(W * scale)), max(1, round(H * scale))), interpolation=cv2.INTER_AREA)
    else:
        wa = alpha_u8
    h, w = wa.shape

    solid = solid_silhouette(wa)
    if not solid.any():
        raise ExhibitionMapError("MAPS_EMPTY_ALPHA", "Alpha has no solid subject.")
    dist = cv2.distanceTransform(solid.astype(np.uint8), cv2.DIST_L2, 5)
    d_max = float(dist.max())
    if d_max < 3.0:
        raise ExhibitionMapError("MAPS_SUBJECT_TOO_THIN", "Subject silhouette is too thin to map.")
    core = _core(solid, d_max)

    hint_work = None
    if head_hint_xy is not None:
        hint_work = (head_hint_xy[0] * scale, head_hint_xy[1] * scale)
    head = estimate_head(solid, dist, hint_xy=hint_work, core=core)

    # 지면 접촉 띠.
    rows = np.flatnonzero(solid.any(axis=1))
    top_y, ground_y_w = int(rows[0]), int(rows[-1])
    band_h = max(2, int(round(GROUND_BAND_FRAC * (ground_y_w - top_y + 1))))
    ground_band = solid.copy()
    ground_band[: max(0, ground_y_w + 1 - band_h), :] = False

    head_region = head.region if head.region is not None else np.zeros_like(solid)
    locked_bin = head_region | ground_band

    feather = max(2.0, 0.12 * d_max)
    locked_soft = np.clip(_blur(locked_bin, feather) * 2.0, 0.0, 1.0)
    locked_soft = np.maximum(locked_soft, locked_bin.astype(np.float32))

    # 몸통 = 코어 − (팽창한 머리) − 지면 띠, 가장 큰 성분.
    head_grown = _dilate_disk(head_region, 0.15 * d_max)
    torso = _largest_component(core & ~head_grown & ~ground_band)
    if not torso.any() and head.tier in (HEAD_TIER_FALLBACK, HEAD_TIER_NONE):
        # 보수적 잠금이 코어를 거의 덮었다 — 여유(팽창) 없이 잠금 바깥만으로 몸통을 잡는다.
        torso = _largest_component(core & ~head_region & ~ground_band)
    if not torso.any():
        raise ExhibitionMapError("MAPS_TORSO_NOT_FOUND", "Could not isolate a torso region.")

    tys, txs = np.nonzero(torso)
    cx, cy = float(txs.mean()), float(tys.mean())
    sigma_c = 0.9 * math.sqrt(float(torso.sum()) / math.pi)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    radial = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma_c**2))
    del yy, xx

    interior = _smoothstep(0.1, 0.7, dist / d_max)
    torso_soft = np.clip(_blur(torso, 0.35 * d_max), 0.0, 1.0)
    weight = _blur(interior * torso_soft * radial, 0.15 * d_max)
    weight *= 1.0 - locked_soft
    weight *= solid  # 실루엣 밖으로 흐린 값이 새지 않게

    if scale < 1.0:
        weight = cv2.resize(weight, (W, H), interpolation=cv2.INTER_LINEAR)
        locked_full = cv2.resize(locked_soft, (W, H), interpolation=cv2.INTER_LINEAR)
    else:
        locked_full = locked_soft
    alpha_f = alpha_u8.astype(np.float32) / 255.0
    weight = weight * alpha_f
    peak = float(weight.max())
    if peak <= 1e-6:
        raise ExhibitionMapError("MAPS_EMPTY_WEIGHT", "Breathing weight map is empty.")
    weight = np.clip(weight / peak, 0.0, 1.0).astype(np.float32)
    locked_full = np.where(alpha_u8 > 0, np.clip(locked_full, 0.0, 1.0), 0.0).astype(np.float32)

    holes = measure_frame(alpha_f, EXHIBITION_HOLE_QA)

    inv = 1.0 / scale
    candidates_canvas = [
        dict(
            c,
            xy=[round(c["xy"][0] * inv, 1), round(c["xy"][1] * inv, 1)],
            peak_d=round(c["peak_d"] * inv, 2),
            saddle_d=round(c["saddle_d"] * inv, 2),
        )
        for c in head.candidates
    ]
    head_out = HeadEstimate(
        xy=(round(head.xy[0] * inv, 1), round(head.xy[1] * inv, 1)) if head.xy else None,
        confidence=head.confidence,
        source=head.source,
        direction=head.direction,
        constriction=head.constriction,
        region=None,
        candidates=candidates_canvas,
        tier=head.tier,
        method=head.method,
        diagnostics=dict(
            head.diagnostics,
            torso_peak=[round(v * inv, 1) for v in head.diagnostics.get("torso_peak", [])],
            d_max=round(d_max * inv, 2),
        ),
    )
    full_rows = np.flatnonzero((alpha_u8 >= SOLID_ALPHA).any(axis=1))
    ground_y = float(full_rows[-1]) if full_rows.size else round(ground_y_w * inv, 1)

    return MapResult(
        breathing_weight=weight,
        locked=locked_full,
        breathing_center=(round(cx * inv, 1), round(cy * inv, 1)),
        breathing_axis=_breathing_axis(torso),
        ground_y=ground_y,
        head=head_out,
        interior_holes=bool(holes["failed"]),
        diagnostics={
            "work_scale": round(scale, 5),
            "d_max_px": round(d_max * inv, 2),
            "open_radius_px": round(OPEN_RADIUS_FRAC * d_max * inv, 2),
            "torso_area_frac": round(float(torso.sum()) / float(solid.sum()), 4),
            "ground_band_frac": GROUND_BAND_FRAC,
            "head_direction": head.direction,
            "head_constriction": head.constriction,
            "head_tier": head.tier,
            "head_method": head.method,
            "head_lock_area_frac": round(float(head_region.sum()) / float(solid.sum()), 4),
            "head_estimate": head_out.diagnostics,
            "head_candidates": candidates_canvas,
            "hole_qa": holes,
        },
    )


# ── 인코딩 ───────────────────────────────────────────────────────────────────


def encode_weight_png16(weight: np.ndarray) -> bytes:
    """0..1 float → 16-bit 그레이스케일 PNG."""
    arr = np.round(np.clip(weight, 0.0, 1.0) * 65535.0).astype(np.uint16)
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise ExhibitionMapError("MAPS_ENCODE_FAILED", "Could not encode breathing_weight.png")
    return buf.tobytes()


def encode_mask_png8(mask: np.ndarray) -> bytes:
    """0..1 float → 8-bit 그레이스케일 PNG (255 = 잠금)."""
    arr = np.round(np.clip(mask, 0.0, 1.0) * 255.0).astype(np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise ExhibitionMapError("MAPS_ENCODE_FAILED", "Could not encode locked_mask.png")
    return buf.tobytes()


def clean_straight_rgba(rgba: np.ndarray, *, bleed_sigma: float = 4.0) -> np.ndarray:
    """
    straight(비-프리멀티) RGBA 에서 알파 0 아래의 RGB 를 정리한다.

    매팅 출력은 알파 0 영역에도 **원본 사진 배경**(집 안, 사람 등)을 그대로 들고
    있다. 외부 시스템에 넘기는 패키지에 그걸 남기지 않되, 쌍선형 필터링 시 검은
    테두리가 생기지 않도록 가장자리 근처만 피사체 색을 번지게(bleed) 채우고
    나머지는 0 으로 둔다. 알파 > 0 픽셀의 RGB 와 알파는 바꾸지 않는다.
    """
    rgb = rgba[:, :, :3].astype(np.float32)
    a = rgba[:, :, 3].astype(np.float32) / 255.0
    num = cv2.GaussianBlur(rgb * a[:, :, None], (0, 0), bleed_sigma)
    den = cv2.GaussianBlur(a, (0, 0), bleed_sigma)
    bleed = np.where(den[:, :, None] > 1e-3, num / np.maximum(den[:, :, None], 1e-3), 0.0)
    zero = rgba[:, :, 3] == 0
    out = rgba.copy()
    out[zero, :3] = np.clip(bleed[zero], 0, 255).astype(np.uint8)
    return out


def render_preview_overlay(rgba: np.ndarray, maps: MapResult) -> np.ndarray:
    """운영자 확인용 RGB: 회색 배경 위 피사체 + 초록(가중치)/빨강(잠금) + 앵커."""
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    base = rgba[:, :, :3].astype(np.float32) * a + 128.0 * (1.0 - a)
    wgt = maps.breathing_weight[:, :, None] * 0.55
    lck = maps.locked[:, :, None] * 0.45
    out = base * (1.0 - wgt - lck) + np.array([40, 220, 90], np.float32) * wgt + np.array(
        [230, 50, 50], np.float32
    ) * lck
    out = np.clip(out, 0, 255).astype(np.uint8)
    r = max(4, int(0.01 * max(out.shape[:2])))
    cx, cy = (int(v) for v in maps.breathing_center)
    ax, ay = maps.breathing_axis
    cv2.circle(out, (cx, cy), r, (255, 255, 255), -1)
    cv2.arrowedLine(out, (cx, cy), (int(cx + ax * 8 * r), int(cy + ay * 8 * r)), (255, 255, 255), max(1, r // 2))
    if maps.head.xy:
        hx, hy = (int(v) for v in maps.head.xy)
        cv2.circle(out, (hx, hy), r, (255, 230, 0), max(1, r // 2))
    gy = int(maps.ground_y)
    cv2.line(out, (0, gy), (out.shape[1] - 1, gy), (0, 160, 255), max(1, r // 3))
    return out
