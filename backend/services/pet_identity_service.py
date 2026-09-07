"""
펫 신원 프로필 빌더 (Visual + Structural Identity, Phase 2).

── 파이프라인 ──────────────────────────────────────────────────────────────
pet_reference_images 의 원본(role='original') 레퍼런스들을 읽어:

  1. 적격성 평가  — 저장된 누끼 진단(YOLO/SAM2/ViTMatte 메타) + 이미지 치수 +
                    (짝지어진 누끼의) 알파 경계 접촉으로 "신원 작업에 쓸 만한가"
  2. 시각 신원    — 결정론적 측정만: 코트 색(마스크 픽셀의 median-cut 팔레트),
                    명도 톤, 영역별 색 요약, 레퍼런스 시그니처(HSV 히스토그램
                    64빈 + pHash 64비트 — 이후 동일 펫 일관성/드리프트 검사용)
  3. 구조 신원    — 결정론적("measured"): bbox 기하·실루엣 지표.
                    휴리스틱("low"): pose_estimation_service 의 마스크 기하
                    18키포인트 — 그 모듈 스스로 placeholder 라 명시하므로
                    **절대 measured 로 승격하지 않는다.**
  4. (옵션) VLM   — vlm_identity 뒤에 격리. 기본 꺼짐. 자체 네임스페이스
                    (semantic_traits)에만 기록되고 결정론적 필드를 덮지 않는다.

결과는 pet_identity_profiles 에 **불변 버전**으로 쌓인다. 재분석 = 새 버전.

── 원칙 ────────────────────────────────────────────────────────────────────
* 원본이 정본이다. 이 모듈은 pet_reference_images 를 **읽기만** 하고,
  스토리지에 아무것도 올리지 않는다.
* 잴 수 없는 것은 UNKNOWN 이다. 예: 원본에 짝지어진 누끼(알파 마스크)가 없으면
  시각/구조 분석 자체가 불가능하고, 그렇게 기록한다 — 추측으로 채우지 않는다.
* 분석 substrate 는 **누끼(파생) RGBA** 다: 알파가 피사체 마스크이고 RGB 픽셀은
  원본에서 온 것이므로, 색·실루엣 측정이 원본 증거에 근거한다.

── 왜 학습 임베딩(CLIP/DINOv2)이 아닌가 ───────────────────────────────────
Phase 2 의 목표는 완벽한 펫 생체인식이 아니라 (a) 같은-펫 일관성 검사
(b) 레퍼런스 클러스터링 (c) 큰 신원 드리프트 감지다. HSV 히스토그램 + pHash 는
그 셋을 torch 없이(512MB Render 에서 불가능) 결정론적으로 감당한다.
signature_version 이 시그니처 스키마를 봉인하므로, 이후 Modal 워커에서 학습
임베딩으로 올릴 때 옛 시그니처와 섞이지 않는다.
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

ELIGIBILITY_ANALYZER_VERSION = "eligibility-v1"
VISUAL_ANALYZER_VERSION = "visual-v1-deterministic"
STRUCTURAL_ANALYZER_VERSION = "structural-v1"
SIGNATURE_VERSION = "sig-v1-hsv64-phash64"

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"

#: 마스크로 인정할 최소 픽셀 수 — 이보다 작으면 측정이 무의미하다.
_MIN_MASK_PIXELS = 64

#: 코트 색 이름 팔레트 (개·고양이에서 실제로 나오는 색만; RGB 최근접 매칭).
_NAMED_COAT_COLORS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("black", (25, 22, 20)),
    ("dark_brown", (74, 51, 34)),
    ("brown", (125, 84, 53)),
    ("red_brown", (155, 88, 49)),
    ("tan", (188, 145, 96)),
    ("golden", (204, 164, 92)),
    ("cream", (228, 212, 180)),
    ("white", (240, 238, 232)),
    ("gray", (128, 128, 126)),
    ("dark_gray", (70, 70, 70)),
)


class PetIdentityError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PET_IDENTITY_PROFILES_TABLE", "pet_identity_profiles")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_PROFILES: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_PROFILES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyzer_versions() -> dict[str, Any]:
    """이 코드가 지금 만들 값들의 분석기 버전 스탬프."""
    from . import vlm_identity

    return {
        "eligibility": ELIGIBILITY_ANALYZER_VERSION,
        "visual": VISUAL_ANALYZER_VERSION,
        "structural": STRUCTURAL_ANALYZER_VERSION,
        "signature": SIGNATURE_VERSION,
        "pose_backend": "heuristic_mask_geometry",
        "vlm": (vlm_identity.VLM_ANALYZER_VERSION if vlm_identity.is_enabled() else None),
        "vlm_model": (vlm_identity.model_name() if vlm_identity.is_enabled() else None),
    }


# ══════════════════════════════════════════════════════════════════════════
# 이미지 유틸 (순수 — 테스트는 합성 이미지로 실제 계약을 검증한다)
# ══════════════════════════════════════════════════════════════════════════


def load_rgba(data: bytes) -> Optional[np.ndarray]:
    """bytes → (H,W,4) uint8. 못 읽으면 None — 분석 실패는 UNKNOWN 으로 흐른다."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGBA"), dtype=np.uint8)
    except Exception:
        return None


def subject_mask(rgba: np.ndarray) -> np.ndarray:
    """알파 채널 → bool 마스크. 누끼의 알파가 곧 피사체 마스크다."""
    return rgba[:, :, 3] > 128


def mask_border_contact(mask: np.ndarray) -> list[str]:
    """마스크가 프레임 가장자리에 닿은 변 목록 — 잘린 신체의 강한 신호."""
    if not mask.any():
        return []
    sides: list[str] = []
    if mask[0, :].any():
        sides.append("top")
    if mask[-1, :].any():
        sides.append("bottom")
    if mask[:, 0].any():
        sides.append("left")
    if mask[:, -1].any():
        sides.append("right")
    return sides


def _nearest_color_name(rgb: tuple[int, int, int]) -> str:
    best, best_d = UNKNOWN, float("inf")
    for name, ref in _NAMED_COAT_COLORS:
        d = sum((int(a) - int(b)) ** 2 for a, b in zip(rgb, ref))
        if d < best_d:
            best, best_d = name, d
    return best


def _dominant_colors(pixels: np.ndarray, *, max_colors: int = 5) -> list[dict[str, Any]]:
    """
    마스크 픽셀(N,3) → median-cut 팔레트. PIL quantize 라 결정론적이고
    torch/cv2 무관하다. 반환: [{hex, rgb, fraction, name}] (fraction 내림차순).
    """
    from PIL import Image

    if len(pixels) == 0:
        return []
    img = Image.fromarray(pixels.reshape(-1, 1, 3), mode="RGB")
    n = max(2, min(max_colors, len(np.unique(pixels, axis=0))))
    try:
        q = img.quantize(colors=n, method=Image.MEDIANCUT)
    except Exception:
        q = img.quantize(colors=n)
    palette = q.getpalette() or []
    counts = q.getcolors(maxcolors=n * 2) or []
    total = float(sum(c for c, _ in counts)) or 1.0

    out: list[dict[str, Any]] = []
    for count, idx in sorted(counts, reverse=True):
        rgb = tuple(int(v) for v in palette[idx * 3 : idx * 3 + 3])
        out.append(
            {
                "hex": "#%02x%02x%02x" % rgb,
                "rgb": list(rgb),
                "fraction": round(count / total, 4),
                "name": _nearest_color_name(rgb),  # type: ignore[arg-type]
            }
        )
    return out


def _unknown_field(reason: str) -> dict[str, Any]:
    return {"status": UNKNOWN, "reason": reason}


# ══════════════════════════════════════════════════════════════════════════
# 시각 신원
# ══════════════════════════════════════════════════════════════════════════

#: 결정론적으로 잴 수 없어 시맨틱 분석(VLM)이 필요한 카테고리 — v1 은 얼굴
#: 검출기가 없으므로(펫 얼굴 모델 부재) 추측 대신 unknown 을 적는다.
_SEMANTIC_ONLY_REASON = "requires_semantic_analysis"


def analyze_visual_identity(rgba: np.ndarray) -> dict[str, Any]:
    """누끼 RGBA → 결정론적 시각 신원. 잴 수 없는 카테고리는 unknown."""
    mask = subject_mask(rgba)
    semantic_unknowns = {
        "face": _unknown_field(_SEMANTIC_ONLY_REASON),
        "eyes": _unknown_field(_SEMANTIC_ONLY_REASON),
        "ears": _unknown_field(_SEMANTIC_ONLY_REASON),
        "body_markings": _unknown_field(_SEMANTIC_ONLY_REASON),
        "paws": _unknown_field(_SEMANTIC_ONLY_REASON),
        "tail": _unknown_field(_SEMANTIC_ONLY_REASON),
        "unique_features": _unknown_field(_SEMANTIC_ONLY_REASON),
    }
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return {
            "status": UNKNOWN,
            "reason": "subject_mask_empty",
            "coat": _unknown_field("subject_mask_empty"),
            **semantic_unknowns,
        }

    pixels = rgba[:, :, :3][mask]
    colors = _dominant_colors(pixels)
    luminance = float(
        np.mean(
            0.299 * pixels[:, 0].astype(np.float64)
            + 0.587 * pixels[:, 1].astype(np.float64)
            + 0.114 * pixels[:, 2].astype(np.float64)
        )
    )
    tone = "dark" if luminance < 60 else ("light" if luminance > 170 else "medium")

    # 영역별 색 요약 — bbox 를 가로 3등분. 어느 쪽이 머리인지는 여기서 판정하지
    # 않는다(구조 분석의 head_side 가 low-confidence 로 따로 온다). 좌/중/우라는
    # 좌표 사실만 적는다.
    ys, xs = np.where(mask)
    xmin, xmax = int(xs.min()), int(xs.max())
    regions: dict[str, Any] = {}
    for label, lo, hi in (
        ("left_third", 0.0, 1 / 3),
        ("center_third", 1 / 3, 2 / 3),
        ("right_third", 2 / 3, 1.0),
    ):
        x0 = xmin + int((xmax - xmin) * lo)
        x1 = xmin + int((xmax - xmin) * hi)
        region_mask = np.zeros_like(mask)
        region_mask[:, x0 : max(x0 + 1, x1)] = mask[:, x0 : max(x0 + 1, x1)]
        region_pixels = rgba[:, :, :3][region_mask]
        if len(region_pixels) < _MIN_MASK_PIXELS:
            regions[label] = _unknown_field("region_too_small")
            continue
        top = _dominant_colors(region_pixels, max_colors=2)
        regions[label] = {"dominant": top[0]["name"] if top else UNKNOWN}

    dominant = [c for c in colors if c["fraction"] >= 0.10]
    return {
        "status": "measured",
        "coat": {
            "status": "measured",
            "dominant_colors": dominant[:2],
            "secondary_colors": dominant[2:],
            "palette": colors,
            "mean_luminance": round(luminance, 1),
            "tone": tone,
            # 길이/타입은 픽셀 통계로 신뢰성 있게 못 잰다 — VLM 영역.
            "length": _unknown_field(_SEMANTIC_ONLY_REASON),
            "texture": _unknown_field(_SEMANTIC_ONLY_REASON),
        },
        "region_color_summary": {
            "note": "horizontal thirds of subject bbox; orientation not asserted",
            **regions,
        },
        **semantic_unknowns,
    }


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 시그니처 (유사도/드리프트)
# ══════════════════════════════════════════════════════════════════════════


def compute_reference_signature(rgba: np.ndarray) -> Optional[dict[str, Any]]:
    """
    피사체 시그니처: HSV 4×4×4 히스토그램(마스크 픽셀) + 64비트 pHash
    (피사체 bbox 크롭의 그레이스케일, numpy DCT). 마스크가 없으면 None.
    """
    from PIL import Image

    mask = subject_mask(rgba)
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return None

    # ── HSV 히스토그램 ────────────────────────────────────────────────────
    hsv = np.asarray(
        Image.fromarray(rgba[:, :, :3], mode="RGB").convert("HSV"), dtype=np.uint8
    )
    px = hsv[mask].astype(np.float64)
    hist, _ = np.histogramdd(px, bins=(4, 4, 4), range=((0, 256), (0, 256), (0, 256)))
    hist = (hist / max(1.0, hist.sum())).reshape(-1)

    # ── pHash: bbox 크롭 → 회색조 32×32 → DCT-II → 좌상 8×8(DC 제외) 중앙값 비트 ─
    ys, xs = np.where(mask)
    crop = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    gray_img = Image.fromarray(crop[:, :, :3], mode="RGB").convert("L")
    alpha = crop[:, :, 3].astype(np.float64) / 255.0
    gray = np.asarray(gray_img, dtype=np.float64) * alpha + 128.0 * (1.0 - alpha)
    small = np.asarray(
        Image.fromarray(gray.astype(np.uint8), mode="L").resize((32, 32)), dtype=np.float64
    )

    n = 32
    k = np.arange(n)
    dct_m = np.cos(np.pi / n * (k[:, None] + 0.5) * k[None, :]).T  # (freq, sample)
    freq = dct_m @ small @ dct_m.T
    low = freq[:8, :8].reshape(-1)[1:]  # DC 제외 63비트 → 64비트 정렬 위해 패딩
    median = float(np.median(low))
    bits = "".join("1" if v > median else "0" for v in low) + "0"
    phash = "%016x" % int(bits, 2)

    return {
        "version": SIGNATURE_VERSION,
        "hsv_hist": [round(float(v), 5) for v in hist],
        "phash": phash,
    }


def signature_similarity(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """두 시그니처의 유사도 — 드리프트/클러스터링 검사의 원자 연산."""
    if not a or not b or a.get("version") != b.get("version"):
        return {"comparable": False}
    ha = int(str(a["phash"]), 16)
    hb = int(str(b["phash"]), 16)
    hamming = bin(ha ^ hb).count("1")
    va = np.asarray(a.get("hsv_hist") or [], dtype=np.float64)
    vb = np.asarray(b.get("hsv_hist") or [], dtype=np.float64)
    inter = float(np.minimum(va, vb).sum()) if va.shape == vb.shape and len(va) else 0.0
    return {
        "comparable": True,
        "phash_hamming": hamming,  # 0 = 동일, 64 = 완전 상이
        "hist_intersection": round(inter, 4),  # 1.0 = 동일 분포
    }


# ══════════════════════════════════════════════════════════════════════════
# 구조 신원
# ══════════════════════════════════════════════════════════════════════════


def analyze_structural_identity(rgba: np.ndarray) -> dict[str, Any]:
    """
    결정론적 실루엣 지표("measured") + 휴리스틱 포즈("low").

    pose_estimation_service 의 휴리스틱 백엔드는 스스로 placeholder 라 명시한다 —
    그 출력(키포인트·head_side·머리/몸 비율)은 전부 confidence "low" 로 격리되고,
    measured 지표와 절대 섞이지 않는다.
    """
    mask = subject_mask(rgba)
    if int(mask.sum()) < _MIN_MASK_PIXELS:
        return {"status": UNKNOWN, "reason": "subject_mask_empty"}

    ys, xs = np.where(mask)
    h, w = mask.shape
    bbox_w = int(xs.max() - xs.min() + 1)
    bbox_h = int(ys.max() - ys.min() + 1)
    area = int(mask.sum())

    measured: dict[str, Any] = {
        "confidence": "measured",
        "image_size": [int(w), int(h)],
        "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        "bbox_aspect_ratio": round(bbox_w / max(1, bbox_h), 3),
        "area_fraction": round(area / float(h * w), 4),
        "bbox_fill_ratio": round(area / float(bbox_w * bbox_h), 4),
        "border_contact": mask_border_contact(mask),
    }
    try:
        import cv2

        contours, _ = cv2.findContours(
            mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if contours:
            biggest = max(contours, key=cv2.contourArea)
            hull_area = float(cv2.contourArea(cv2.convexHull(biggest)))
            if hull_area > 0:
                measured["silhouette_solidity"] = round(
                    float(cv2.contourArea(biggest)) / hull_area, 4
                )
    except Exception:
        pass  # solidity 는 부가 지표 — 없으면 없는 대로 둔다

    # ── 휴리스틱 포즈 (low confidence, 격리) ──────────────────────────────
    pose_out: dict[str, Any]
    try:
        from .pose_estimation_service import estimate_pose, keypoints_to_dict

        pose = estimate_pose(
            rgba[:, :, :3], (mask.astype(np.uint8) * 255), backend="heuristic"
        )
        head_top = pose.get("head_top")
        neck = pose.get("neck")
        head_height_fraction = None
        if head_top and neck and bbox_h > 0:
            head_height_fraction = round(abs(neck.y - head_top.y) / bbox_h, 3)
        pose_out = {
            "confidence": "low",
            "backend": pose.backend,
            "head_side": pose.head_side,
            "keypoints": keypoints_to_dict(pose),
            "head_height_fraction": head_height_fraction,
            "warnings": list(pose.warnings),
            "caveat": "heuristic silhouette geometry — placeholder per its own docs; "
            "never treat as authoritative skeleton",
        }
    except Exception as e:
        pose_out = _unknown_field(f"pose_estimation_failed: {type(e).__name__}")

    return {
        "status": "measured",
        "silhouette": measured,
        # 측면 사진 가정 하의 몸 길이/키 비율 근사 — bbox 기반이라 measured 로
        # 표기하되, 방향성 주의를 함께 남긴다.
        "body_length_height_ratio": {
            "value": measured["bbox_aspect_ratio"],
            "confidence": "measured",
            "caveat": "bbox aspect ratio; assumes near-side view, pose-dependent",
        },
        "pose": pose_out,
        # 휴리스틱은 꼬리 가시성을 판정할 수 없다(항상 꼬리 좌표를 만들어 낸다).
        "tail_visibility": _unknown_field("not_determinable_from_silhouette_v1"),
        "leg_proportions": _unknown_field("near_far_leg_ambiguity_v1"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 레퍼런스 적격성
# ══════════════════════════════════════════════════════════════════════════


def _diag_lookup(diagnostics: Optional[dict[str, Any]], *keys: str) -> Any:
    """진단 dict 에서 키를 방어적으로 찾는다 — 평면/중첩 두 형태 모두."""
    if not isinstance(diagnostics, dict):
        return None
    for k in keys:
        if k in diagnostics:
            return diagnostics[k]
    nested = diagnostics.get("diagnostics")
    if isinstance(nested, dict):
        for k in keys:
            if k in nested:
                return nested[k]
    return None


def evaluate_reference_eligibility(
    ref: Any, cutout_rgba: Optional[np.ndarray]
) -> dict[str, Any]:
    """
    원본 레퍼런스 1건의 신원 작업 적격성.

    입력은 (a) Phase 1 이 저장한 누끼 진단 메타 (b) 짝지어진 누끼의 알파 뿐이다 —
    여기서 모델을 새로 돌리지 않는다. 뷰(FRONT/LEFT/…) 라벨은 **판정하지 않는다**:
    근거가 될 분석이 없으므로 unknown 으로 남긴다.
    """
    diag = getattr(ref, "diagnostics", None)

    subject_detected = _diag_lookup(diag, "subject_detected")
    confidence = _diag_lookup(diag, "detection_confidence", "confidence")
    animal_class = _diag_lookup(diag, "subject_class", "animal_class")
    mask_fraction = _diag_lookup(diag, "mask_area_fraction", "alpha_area_fraction")
    rectangle_like = _diag_lookup(diag, "rectangle_like_mask", "rectangle_like")
    quality_score = _diag_lookup(diag, "quality_score")

    # 사람 오염 — 진단에 사람 관련 신호가 있으면 그걸 쓰고, 없으면 unknown.
    person = getattr(ref, "person_detected", None)
    if person is None:
        person_boxes = _diag_lookup(diag, "person_boxes", "person_bbox_count")
        if isinstance(person_boxes, list):
            person = len(person_boxes) > 0
        elif isinstance(person_boxes, (int, float)):
            person = person_boxes > 0

    border: Any = UNKNOWN
    full_body: str = UNKNOWN
    if cutout_rgba is not None:
        mask = subject_mask(cutout_rgba)
        if int(mask.sum()) >= _MIN_MASK_PIXELS:
            border = mask_border_contact(mask)
            # 몸이 프레임에 잘렸다는 직접 증거만 쓴다. 접촉 없음 = "잘리지는
            # 않았다"이지 "전신이 보인다"의 증명은 아니므로 likely 로만 적는다.
            full_body = "unlikely" if border else "likely"
        else:
            border = UNKNOWN

    reasons: list[str] = []
    if subject_detected is False:
        reasons.append("subject_not_detected")
    if rectangle_like is True:
        reasons.append("rectangle_like_mask")
    if person is True:
        reasons.append("person_contamination")
    if full_body == "unlikely":
        reasons.append("subject_cropped_by_frame")
    if cutout_rgba is None:
        reasons.append("no_segmentation_available")

    usable = subject_detected is not False and rectangle_like is not True and cutout_rgba is not None

    return {
        "analyzer": ELIGIBILITY_ANALYZER_VERSION,
        "subject_detected": subject_detected if subject_detected is not None else UNKNOWN,
        "animal_class": animal_class or UNKNOWN,
        "detection_confidence": confidence if confidence is not None else UNKNOWN,
        "mask_area_fraction": mask_fraction if mask_fraction is not None else UNKNOWN,
        "rectangle_like_mask": rectangle_like if rectangle_like is not None else UNKNOWN,
        "segmentation_quality_score": quality_score if quality_score is not None else UNKNOWN,
        "person_contamination": person if person is not None else UNKNOWN,
        "border_contact": border,
        "full_body_visible": full_body,
        # 근거 있는 분석이 없으므로 추측하지 않는다 (요구사항 6).
        "view_label_estimate": UNKNOWN,
        "face_usable": UNKNOWN,  # 펫 얼굴 검출기 부재 (v1)
        "tail_visible": UNKNOWN,
        "usable_for_identity": usable,
        "reasons": reasons,
    }


# ══════════════════════════════════════════════════════════════════════════
# 프로필 빌드 / 조회
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PetIdentityProfile:
    id: Optional[str]
    pet_id: str
    user_id: str
    content_id: Optional[str]
    version: int
    status: str
    source_reference_ids: list[str] = field(default_factory=list)
    reference_eligibility: dict[str, Any] = field(default_factory=dict)
    visual_identity: dict[str, Any] = field(default_factory=dict)
    structural_identity: dict[str, Any] = field(default_factory=dict)
    completeness: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    #: 이번 호출이 새 버전을 만들지 않고 기존 최신 프로필을 돌려준 것인가.
    deduplicated: bool = False


_SELECT = (
    "id, pet_id, user_id, content_id, version, status, source_reference_ids, "
    "reference_eligibility, visual_identity, structural_identity, completeness, "
    "analyzer_versions, created_at"
)


def _to_profile(row: dict[str, Any], *, deduplicated: bool = False) -> PetIdentityProfile:
    return PetIdentityProfile(
        id=(str(row["id"]) if row.get("id") else None),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        content_id=(row.get("content_id") or None),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_PARTIAL),
        source_reference_ids=list(row.get("source_reference_ids") or []),
        reference_eligibility=dict(row.get("reference_eligibility") or {}),
        visual_identity=dict(row.get("visual_identity") or {}),
        structural_identity=dict(row.get("structural_identity") or {}),
        completeness=dict(row.get("completeness") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        deduplicated=deduplicated,
    )


async def _profile_rows(pet_id: str) -> list[dict[str, Any]]:
    pid = (pet_id or "").strip()
    if not pid:
        return []
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_table())
                .select(_SELECT)
                .eq("pet_id", pid)
                .order("version", desc=False)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("신원 프로필 조회 실패 (pet=%s)", pid)
            raise PetIdentityError(
                "IDENTITY_PROFILES_UNAVAILABLE", "신원 프로필을 확인하지 못했습니다.", status=503
            ) from e
    return [r for r in _MOCK_PROFILES if r.get("pet_id") == pid]


async def get_profile(
    *, user_id: str, pet_id: str, version: Optional[int] = None
) -> Optional[PetIdentityProfile]:
    """소유권이 확인된 호출자의 프로필 조회. version 없으면 최신."""
    from . import pet_reference_service

    # 소유권은 레퍼런스 대장과 같은 규칙으로 확인한다 (레지스트리 우선 TOFU).
    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise PetIdentityError(e.code, e.message, status=e.status) from e

    rows = await _profile_rows(pet_id)
    if not rows:
        return None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                return _to_profile(r)
        return None
    return _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))


def _default_fetch_bytes(ref: Any) -> Optional[bytes]:
    """스토리지에서 레퍼런스 바이트를 내려받는다 — 서명이 곧 존재 확인이다."""
    try:
        from .asset_url_refresh import StorageObject, default_bucket, sign_object

        # bucket 미기록 레퍼런스(과거 행/파생 payload)는 기본 버킷으로 서명한다.
        url = sign_object(
            StorageObject(bucket=(getattr(ref, "bucket", "") or default_bucket()), path=ref.object_path)
        )
        if not url:
            return None
        import httpx

        r = httpx.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return None
        return r.content
    except Exception:
        logger.warning("레퍼런스 다운로드 실패 (path=%s)", getattr(ref, "object_path", "?"), exc_info=True)
        return None


def _completeness(visual: dict[str, Any], structural: dict[str, Any], semantic_status: str) -> dict[str, Any]:
    def count(d: dict[str, Any]) -> tuple[int, int]:
        known = unknown = 0
        for v in d.values():
            if isinstance(v, dict) and v.get("status") == UNKNOWN:
                unknown += 1
            elif isinstance(v, dict) or v is not None:
                known += 1
        return known, unknown

    vk, vu = count({k: v for k, v in visual.items() if k not in ("status", "semantic_traits")})
    sk, su = count({k: v for k, v in structural.items() if k != "status"})
    return {
        "visual": {"known": vk, "unknown": vu},
        "structural": {"known": sk, "unknown": su},
        "semantic": semantic_status,
    }


async def _insert_profile_row(row: dict[str, Any]) -> tuple[bool, Optional[Exception]]:
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, e
    for r in _MOCK_PROFILES:
        if r["pet_id"] == row["pet_id"] and int(r["version"]) == int(row["version"]):
            return False, PetIdentityError("DUPLICATE", "duplicate version")
    _MOCK_PROFILES.append(dict(row))
    return True, None


async def build_identity_profile(
    *,
    user_id: str,
    pet_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    skip_if_unchanged: bool = True,
) -> PetIdentityProfile:
    """
    원본 레퍼런스 → 새 신원 프로필 버전.

    * 소유권은 레퍼런스 대장과 같은 규칙 (레지스트리 우선, TOFU).
    * skip_if_unchanged=True(기본): 최신 프로필이 같은 원본 집합 + 같은 분석기
      버전으로 만들어졌으면 새 버전을 만들지 않고 그것을 돌려준다 (멱등).
    * 이 함수는 pet_reference_images 를 **읽기만** 하고 스토리지에 쓰지 않는다.
    """
    from . import pet_reference_service, vlm_identity

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetIdentityError("IDENTITY_INVALID", "user_id 와 pet_id 가 필요합니다.")

    try:
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    except pet_reference_service.PetReferenceError as e:
        raise PetIdentityError(e.code, e.message, status=e.status) from e

    originals = [
        r
        for r in refs
        if r.role == pet_reference_service.ROLE_ORIGINAL
        and r.acceptance_state == pet_reference_service.STATE_ACCEPTED
    ]
    if not originals:
        raise PetIdentityError(
            "NO_ORIGINAL_REFERENCES",
            "분석할 원본 레퍼런스가 없습니다 — 먼저 사진을 등록하세요.",
            status=409,
        )

    cutout_by_original = pet_reference_service.pair_cutouts(refs)

    source_ids = sorted(str(r.id) for r in originals if r.id)
    versions = analyzer_versions()

    if skip_if_unchanged:
        rows = await _profile_rows(pid)
        if rows:
            latest = _to_profile(max(rows, key=lambda r: int(r.get("version") or 0)))
            if (
                sorted(latest.source_reference_ids) == source_ids
                and latest.analyzer_versions == versions
            ):
                return _to_profile(
                    max(rows, key=lambda r: int(r.get("version") or 0)), deduplicated=True
                )

    fetch = fetch_bytes or _default_fetch_bytes

    # ── 레퍼런스별 적격성 + 시그니처, 프로필 수준 시각/구조는 primary 에서 ──
    eligibility: dict[str, Any] = {}
    visual: dict[str, Any] = {}
    structural: dict[str, Any] = {}
    primary_reference_id: Optional[str] = None
    original_bytes_for_vlm: list[tuple[bytes, str]] = []

    for ref in originals:
        cut = cutout_by_original.get(str(ref.id))
        cut_rgba = None
        if cut is not None:
            cut_bytes = fetch(cut)
            if cut_bytes:
                cut_rgba = load_rgba(cut_bytes)

        entry = evaluate_reference_eligibility(ref, cut_rgba)
        if cut_rgba is not None:
            sig = compute_reference_signature(cut_rgba)
            if sig:
                entry["signature"] = sig
        eligibility[str(ref.id)] = entry

        if cut_rgba is not None and primary_reference_id is None and entry["usable_for_identity"]:
            primary_reference_id = str(ref.id)
            visual = analyze_visual_identity(cut_rgba)
            structural = analyze_structural_identity(cut_rgba)

        if vlm_identity.is_enabled() and len(original_bytes_for_vlm) < vlm_identity.MAX_IMAGES:
            orig_bytes = fetch(ref)
            if orig_bytes:
                original_bytes_for_vlm.append((orig_bytes, ref.mime_type or "image/jpeg"))

    if not visual:
        visual = {
            "status": UNKNOWN,
            "reason": "no_analyzable_reference",
            "coat": _unknown_field("no_segmentation_available"),
        }
    if not structural:
        structural = {"status": UNKNOWN, "reason": "no_analyzable_reference"}

    # ── VLM 시맨틱 패스 (자체 네임스페이스; 결정론적 필드를 덮지 않는다) ──
    semantic_status = "skipped_vlm_disabled"
    if vlm_identity.is_enabled():
        result = vlm_identity.analyze_semantic_traits(original_bytes_for_vlm)
        if result:
            visual["semantic_traits"] = {
                "status": "vlm",
                "source_reference_ids": source_ids[: result.get("image_count", 0)],
                **result,
            }
            semantic_status = "present"
        else:
            visual["semantic_traits"] = _unknown_field("vlm_analysis_failed")
            semantic_status = "failed"
    else:
        visual["semantic_traits"] = _unknown_field("vlm_disabled")

    status = STATUS_COMPLETE if primary_reference_id else STATUS_PARTIAL
    if primary_reference_id:
        visual["primary_reference_id"] = primary_reference_id
        structural["primary_reference_id"] = primary_reference_id

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "user_id": uid,
        "content_id": (originals[0].content_id or None),
        "version": 1,
        "status": status,
        "source_reference_ids": source_ids,
        "reference_eligibility": eligibility,
        "visual_identity": visual,
        "structural_identity": structural,
        "completeness": _completeness(visual, structural, semantic_status),
        "analyzer_versions": versions,
        "created_at": _now_iso(),
    }

    for _ in range(3):
        rows = await _profile_rows(pid)
        row["version"] = (max((int(r.get("version") or 0) for r in rows), default=0)) + 1
        ok, err = await _insert_profile_row(row)
        if ok:
            return _to_profile(row)
        last_err = err

    logger.error("신원 프로필 기록 실패 (pet=%s): %s", pid, last_err)
    raise PetIdentityError(
        "IDENTITY_PROFILES_UNAVAILABLE", "신원 프로필을 저장하지 못했습니다.", status=503
    )
