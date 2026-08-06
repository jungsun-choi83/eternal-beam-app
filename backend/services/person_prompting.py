"""
사람 인지 SAM2 프롬프팅 (Phase 2B).

문제
----
Phase 2A 에서 후보 랭킹을 고쳤지만 손/팔이 그대로 남았다. 이유는 단순하다:
**SAM2 가 낸 세 후보 모두 손을 포함**하고 있었기 때문이다. 손은 펫 bbox 안에
있고, 털과 이어져 있어서 박스 프롬프트만으로는 "이건 빼라"는 정보가 없다.

랭킹으로는 못 고친다. 모델에 **부정 증거(negative point)** 를 줘야 한다.

접근
----
1. 박스 전용 마스크(Phase 2A 결과)를 먼저 얻는다 — 이게 비교 기준선이다.
2. 사람 bbox 와 펫 bbox 의 겹침을 본다.
3. 기준선 마스크에서
     - 사람 영역 **밖**의 깊숙한 안쪽  → 양성 포인트 (확실한 펫)
     - 사람 영역 **안**의 깊숙한 안쪽  → 음성 포인트 (확실한 사람)
   양쪽 다 거리변환(distance transform) 최대점을 쓰므로 경계에서 최대한 멀다.
   → 요구사항 "손-털 경계 근처에는 음성 포인트를 두지 말 것" 을 만족한다.
4. 같은 박스 + 포인트로 SAM2 를 다시 돌리고 Phase 2A 채점기를 그대로 재사용한다.
5. **보정 결과가 명백히 더 나을 때만** 채택한다. 애매하면 기준선을 유지한다.

채택 기준(모두 만족해야 함)
  - 보정 마스크가 Phase 2A 유효성 검사를 통과할 것
  - 제거량이 (MIN, MAX] 구간일 것 — 너무 적으면 이득 없고, 너무 많으면 개를 먹은 것
  - 제거된 픽셀의 대부분이 **사람 영역 안**일 것 (개를 깎은 게 아님을 증명)
  - 채점 점수가 기준선보다 크게 나빠지지 않을 것

이 모듈은 SAM2 를 직접 부르지 않는다. 후보 생성 함수를 주입받아 쓰므로
vitmatte_service 와 순환 임포트가 없고 테스트에서 목업하기도 쉽다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:  # pragma: no cover
    CV2_AVAILABLE = False

BBox = tuple[int, int, int, int]
Point = tuple[int, int]

# --- 임계값 (모두 환경변수로 조정 가능) ---------------------------------------

#: 사람 bbox 가 펫 bbox 를 이 비율 이상 덮어야 "의미 있는 겹침"으로 본다.
PERSON_OVERLAP_MIN = float(os.getenv("PERSON_PROMPT_OVERLAP_MIN", "0.05"))

#: 기준선 마스크 중 사람 영역과 겹치는 비율이 이 값 미만이면 손댈 것이 없다.
CONTAMINATION_MIN = float(os.getenv("PERSON_PROMPT_CONTAMINATION_MIN", "0.02"))

MAX_POSITIVE_POINTS = int(os.getenv("PERSON_PROMPT_MAX_POSITIVE", "2"))
MAX_NEGATIVE_POINTS = int(os.getenv("PERSON_PROMPT_MAX_NEGATIVE", "3"))

#: 경계에서 떨어뜨릴 최소 거리 (이미지 짧은 변 대비 비율).
#: 음성 포인트가 손-털 경계에 붙으면 SAM2 가 털까지 지운다.
BOUNDARY_MARGIN_FRAC = float(os.getenv("PERSON_PROMPT_BOUNDARY_MARGIN", "0.02"))

#: 양성(펫) 영역에서 음성 포인트를 밀어낼 거리 비율.
PET_KEEPOUT_FRAC = float(os.getenv("PERSON_PROMPT_PET_KEEPOUT", "0.03"))

#: 보정이 제거해야 하는 최소/최대 비율 (기준선 마스크 대비).
MIN_REMOVAL_FRACTION = float(os.getenv("PERSON_PROMPT_MIN_REMOVAL", "0.01"))
MAX_REMOVAL_FRACTION = float(os.getenv("PERSON_PROMPT_MAX_REMOVAL", "0.45"))

#: 제거된 픽셀 중 사람 영역 안에 있어야 하는 최소 비율.
#: 이게 낮으면 "개를 깎아서 작아진 것"이므로 채택하지 않는다.
MIN_REMOVAL_PRECISION = float(os.getenv("PERSON_PROMPT_MIN_PRECISION", "0.60"))

#: 보정 점수가 기준선보다 이만큼까지 낮아지는 건 허용한다.
SCORE_TOLERANCE = float(os.getenv("PERSON_PROMPT_SCORE_TOLERANCE", "0.05"))

#: 최종 마스크에 이 비율 이상 사람 영역이 남아 있으면 오염 가능성을 표시한다.
RESIDUAL_CONTAMINATION_FLAG = float(
    os.getenv("PERSON_PROMPT_RESIDUAL_FLAG", "0.02")
)


@dataclass
class PersonAwareResult:
    """Phase 2B 진단 — 성공/실패/생략 모든 경우에 채워진다."""

    person_detected: bool = False
    person_boxes: list[list[int]] = field(default_factory=list)
    person_pet_overlap: float = 0.0
    person_aware_prompting_used: bool = False
    positive_points: list[list[int]] = field(default_factory=list)
    negative_points: list[list[int]] = field(default_factory=list)
    original_selected_score: Optional[float] = None
    corrected_selected_score: Optional[float] = None
    corrected_mask_selected: bool = False
    possible_human_contamination: bool = False
    # 부가 진단
    skipped_reason: Optional[str] = None
    rejected_reason: Optional[str] = None
    contaminated_fraction: Optional[float] = None
    removed_fraction: Optional[float] = None
    removal_precision: Optional[float] = None
    residual_contamination: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_detected": self.person_detected,
            "person_boxes": self.person_boxes,
            "person_pet_overlap": round(self.person_pet_overlap, 4),
            "person_aware_prompting_used": self.person_aware_prompting_used,
            "positive_points": self.positive_points,
            "negative_points": self.negative_points,
            "original_selected_score": self.original_selected_score,
            "corrected_selected_score": self.corrected_selected_score,
            "corrected_mask_selected": self.corrected_mask_selected,
            "possible_human_contamination": self.possible_human_contamination,
            "skipped_reason": self.skipped_reason,
            "rejected_reason": self.rejected_reason,
            "contaminated_fraction": self.contaminated_fraction,
            "removed_fraction": self.removed_fraction,
            "removal_precision": self.removal_precision,
            "residual_contamination": self.residual_contamination,
        }


def bbox_overlap_fraction(inner: BBox, outer: BBox) -> float:
    """inner 면적 중 outer 와 겹치는 비율 (IoU 아님 — '얼마나 물려 있나')."""
    iw = max(0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    ih = max(0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    inter = float(iw * ih)
    area = float(max(0, inner[2] - inner[0]) * max(0, inner[3] - inner[1]))
    if area <= 0:
        return 0.0
    return inter / area


def person_region_mask(shape: tuple[int, int], person_boxes: Sequence[BBox]) -> np.ndarray:
    """사람 bbox 들의 합집합을 이진 마스크로."""
    h, w = shape
    region = np.zeros((h, w), dtype=np.uint8)
    for x1, y1, x2, y2 in person_boxes:
        x1c, y1c = max(0, int(x1)), max(0, int(y1))
        x2c, y2c = min(w, int(x2)), min(h, int(y2))
        if x2c > x1c and y2c > y1c:
            region[y1c:y2c, x1c:x2c] = 255
    return region


def person_only_region_mask(
    shape: tuple[int, int], person_boxes: Sequence[BBox], pet_bbox: BBox
) -> np.ndarray:
    """
    **확실히 사람인 영역** = 사람 bbox 합집합 − 펫 bbox.

    사람 bbox 만으로 "여기는 사람"이라고 단정하면 안 된다. 사람이 개를 안고 있으면
    두 박스가 크게 겹치고, 그 겹친 영역에는 개의 몸이 그대로 들어 있다. 펫 bbox
    안쪽은 전부 '불확실'로 보고 제외해야, 음성 포인트가 개 위에 찍히는 사고를 막고
    (요구사항 6) 제거 정밀도 계산도 정직해진다.
    """
    region = person_region_mask(shape, person_boxes)
    h, w = shape
    x1, y1, x2, y2 = pet_bbox
    x1c, y1c = max(0, int(x1)), max(0, int(y1))
    x2c, y2c = min(w, int(x2)), min(h, int(y2))
    if x2c > x1c and y2c > y1c:
        region[y1c:y2c, x1c:x2c] = 0
    return region


def _deep_points(
    region: np.ndarray, max_points: int, min_distance: float
) -> list[Point]:
    """
    region(0/255) 안에서 경계로부터 가장 깊은 지점들을 고른다.

    거리변환의 최대점을 하나 뽑고, 그 주변을 지운 뒤 반복 — 포인트가 한 군데
    뭉치지 않게 한다. 각 포인트는 경계에서 최소 `min_distance` 이상 떨어진다.
    """
    if not CV2_AVAILABLE or region is None or not np.any(region):
        return []

    work = (region > 0).astype(np.uint8)
    points: list[Point] = []
    for _ in range(max_points):
        dist = cv2.distanceTransform(work, cv2.DIST_L2, 5)
        max_val = float(dist.max())
        if max_val < min_distance:
            break
        y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
        points.append((int(x), int(y)))
        # 뽑은 점 주변을 제거해 다음 점이 겹치지 않게 함
        radius = max(int(max_val), int(min_distance))
        cv2.circle(work, (int(x), int(y)), radius * 2, 0, -1)
        if not np.any(work):
            break
    return points


def build_prompt_points(
    base_mask: np.ndarray,
    person_only_region: np.ndarray,
    *,
    max_positive: int = MAX_POSITIVE_POINTS,
    max_negative: int = MAX_NEGATIVE_POINTS,
) -> tuple[list[Point], list[Point], Optional[str]]:
    """
    기준선 마스크 + **확실한 사람 영역** → (양성 포인트, 음성 포인트, 실패 사유).

    양성: 마스크 ∩ 사람영역 밖 의 깊은 곳 → "여긴 확실히 펫"
    음성: 마스크 ∩ 사람영역 안 의 깊은 곳 → "여긴 확실히 사람"

    두 종류 모두 거리변환 최대점이라 경계에서 최대한 멀고, 음성은 추가로 펫 코어를
    PET_KEEPOUT 만큼 팽창시켜 그 밖으로만 뽑는다 → 손-털 경계에 붙지 않는다.
    """
    if not CV2_AVAILABLE:
        return [], [], "cv2_unavailable"

    h, w = base_mask.shape[:2]
    short_edge = float(min(h, w))
    boundary_margin = max(2.0, short_edge * BOUNDARY_MARGIN_FRAC)
    keepout_px = max(2, int(short_edge * PET_KEEPOUT_FRAC))

    mask_bin = base_mask > 0
    person_bin = person_only_region > 0

    pet_only = (mask_bin & ~person_bin).astype(np.uint8) * 255
    contaminated = (mask_bin & person_bin).astype(np.uint8) * 255

    positives = _deep_points(pet_only, max_positive, boundary_margin)
    if not positives:
        return [], [], "no_confident_pet_interior"

    # 음성 후보에서 펫 코어 주변을 밀어낸다 — 손-털 경계에 점이 붙는 것을 방지.
    kernel = np.ones((keepout_px * 2 + 1, keepout_px * 2 + 1), np.uint8)
    pet_dilated = cv2.dilate((pet_only > 0).astype(np.uint8), kernel, iterations=1)
    negative_region = ((contaminated > 0) & (pet_dilated == 0)).astype(np.uint8) * 255

    negatives = _deep_points(negative_region, max_negative, boundary_margin)
    if not negatives:
        return positives, [], "no_safe_person_region"

    return positives, negatives, None


def _mask_area(mask: np.ndarray) -> int:
    return int(np.count_nonzero(mask > 0))


def evaluate_correction(
    base_mask: np.ndarray,
    corrected_mask: np.ndarray,
    person_only_region: np.ndarray,
    *,
    base_score: float,
    corrected_score: float,
    corrected_valid: bool,
    negative_points: Optional[Sequence[Point]] = None,
) -> tuple[bool, Optional[str], dict[str, float]]:
    """
    보정 마스크를 채택할지 판정.

    Returns: (채택 여부, 거절 사유, 지표)
    """
    base_area = _mask_area(base_mask)
    metrics: dict[str, float] = {}
    if base_area == 0:
        return False, "base_mask_empty", metrics

    corr_bin = corrected_mask > 0
    base_bin = base_mask > 0
    removed = base_bin & ~corr_bin
    removed_area = int(np.count_nonzero(removed))

    removed_fraction = removed_area / float(base_area)
    metrics["removed_fraction"] = round(removed_fraction, 4)

    # 제거된 픽셀 중 **확실한 사람 영역** 안에 있던 비율 —
    # 개를 깎은 게 아니라 사람을 지웠음을 증명하는 지표.
    if removed_area > 0:
        in_person = int(np.count_nonzero(removed & (person_only_region > 0)))
        precision = in_person / float(removed_area)
    else:
        precision = 0.0
    metrics["removal_precision"] = round(precision, 4)

    # 잔여 오염은 면적비가 아니라 **음성 포인트 생존율**로 잰다.
    # (면적비를 쓰면 사람 bbox 안에 들어간 개의 몸까지 오염으로 세게 된다)
    residual = 0.0
    if negative_points:
        h, w = corrected_mask.shape[:2]
        still_inside = sum(
            1
            for x, y in negative_points
            if 0 <= int(y) < h and 0 <= int(x) < w and corr_bin[int(y), int(x)]
        )
        residual = still_inside / float(len(negative_points))
    metrics["residual_contamination"] = round(residual, 4)

    if not corrected_valid:
        return False, "corrected_mask_invalid", metrics
    if removed_fraction <= MIN_REMOVAL_FRACTION:
        return False, "no_meaningful_change", metrics
    if removed_fraction > MAX_REMOVAL_FRACTION:
        return False, "removed_too_much", metrics
    if precision < MIN_REMOVAL_PRECISION:
        return False, "removal_not_person_specific", metrics
    if corrected_score < base_score - SCORE_TOLERANCE:
        return False, "score_regression", metrics

    return True, None, metrics


def apply_person_aware_prompting(
    *,
    base_mask: np.ndarray,
    base_score: Optional[float],
    base_valid: bool,
    pet_bbox: BBox,
    person_boxes: Sequence[BBox],
    frame_shape: tuple[int, int],
    run_sam2: Callable[..., list],
    select_candidate: Callable[[list], tuple[Any, str]],
) -> tuple[Optional[np.ndarray], Optional[list], PersonAwareResult]:
    """
    사람 인지 재프롬프팅 전체 절차.

    Args:
        base_mask: Phase 2A 박스 전용 마스크 (기준선)
        run_sam2: (positive_points, negative_points) 를 받아 후보 리스트를 내는 함수
        select_candidate: Phase 2A 채점 선택기

    Returns:
        (채택된 마스크 또는 None, 보정 후보 리스트 또는 None, 진단)
        마스크가 None 이면 호출자는 기준선을 그대로 쓴다.
    """
    result = PersonAwareResult(
        person_detected=bool(person_boxes),
        person_boxes=[list(map(int, b)) for b in person_boxes],
        original_selected_score=base_score,
    )

    if not person_boxes:
        result.skipped_reason = "no_person_detected"
        return None, None, result

    result.person_pet_overlap = max(
        (bbox_overlap_fraction(pet_bbox, b) for b in person_boxes), default=0.0
    )
    if result.person_pet_overlap < PERSON_OVERLAP_MIN:
        result.skipped_reason = "person_does_not_overlap_pet"
        return None, None, result

    if not CV2_AVAILABLE:
        result.skipped_reason = "cv2_unavailable"
        result.possible_human_contamination = True
        return None, None, result

    # 펫 bbox 안쪽은 '불확실'로 제외 — 사람이 개를 안고 있으면 두 박스가 크게
    # 겹치고, 그 안에는 개의 몸이 들어 있다.
    person_region = person_only_region_mask(frame_shape, person_boxes, pet_bbox)
    base_area = _mask_area(base_mask)
    if base_area == 0:
        result.skipped_reason = "base_mask_empty"
        return None, None, result

    contaminated_fraction = (
        int(np.count_nonzero((base_mask > 0) & (person_region > 0))) / float(base_area)
    )
    result.contaminated_fraction = round(contaminated_fraction, 4)

    if contaminated_fraction < CONTAMINATION_MIN:
        # 사람이 겹치긴 하지만 마스크가 사람을 거의 안 물었다 — 건드릴 이유 없음.
        result.skipped_reason = "no_contamination_in_mask"
        return None, None, result

    positives, negatives, point_error = build_prompt_points(base_mask, person_region)
    result.positive_points = [list(p) for p in positives]
    result.negative_points = [list(p) for p in negatives]

    if point_error or not negatives:
        result.skipped_reason = point_error or "no_safe_person_region"
        # 오염은 있는데 안전하게 지울 방법이 없다 → 그대로 표시.
        result.possible_human_contamination = True
        return None, None, result

    try:
        candidates = run_sam2(positive_points=positives, negative_points=negatives)
    except Exception as exc:
        logger.exception("person-aware SAM2 re-prompt failed")
        result.skipped_reason = f"sam2_reprompt_failed:{type(exc).__name__}"
        result.possible_human_contamination = True
        return None, None, result

    if not candidates:
        result.skipped_reason = "no_corrected_candidates"
        result.possible_human_contamination = True
        return None, None, result

    result.person_aware_prompting_used = True

    corrected, _reason = select_candidate(candidates)
    result.corrected_selected_score = getattr(corrected, "selection_score", None)

    accepted, rejected_reason, metrics = evaluate_correction(
        base_mask,
        corrected.mask,
        person_region,
        base_score=float(base_score or 0.0),
        corrected_score=float(result.corrected_selected_score or 0.0),
        corrected_valid=bool(getattr(corrected, "valid", False)),
        negative_points=negatives,
    )
    result.removed_fraction = metrics.get("removed_fraction")
    result.removal_precision = metrics.get("removal_precision")
    result.residual_contamination = metrics.get("residual_contamination")

    if not accepted:
        result.rejected_reason = rejected_reason
        result.corrected_mask_selected = False
        result.possible_human_contamination = contaminated_fraction >= RESIDUAL_CONTAMINATION_FLAG
        logger.info(
            "person-aware correction rejected (%s): removed=%.3f precision=%.3f",
            rejected_reason,
            metrics.get("removed_fraction", 0.0),
            metrics.get("removal_precision", 0.0),
        )
        return None, candidates, result

    result.corrected_mask_selected = True
    residual = result.residual_contamination or 0.0
    result.possible_human_contamination = residual >= RESIDUAL_CONTAMINATION_FLAG
    logger.info(
        "person-aware correction accepted: removed=%.3f precision=%.3f residual=%.3f "
        "(score %.3f -> %.3f)",
        metrics.get("removed_fraction", 0.0),
        metrics.get("removal_precision", 0.0),
        residual,
        base_score or 0.0,
        result.corrected_selected_score or 0.0,
    )
    return corrected.mask, candidates, result
