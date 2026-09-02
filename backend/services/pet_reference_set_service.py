"""
신뢰 레퍼런스 세트 빌더 (Multi-view Trusted References, Phase 3).

── 파이프라인 ──────────────────────────────────────────────────────────────
원본 레퍼런스들 + Phase 2 신원 프로필 →

  1. 레퍼런스 분석   — Phase 2 적격성/시그니처를 **재사용**하고(프로필에 박제된
                       값 우선), 품질 컴포넌트 점수를 더한다
  2. 뷰/포즈 분류    — VLM(vlm_identity.classify_reference, 켜져 있을 때)이
                       유일한 뷰 분류기다. 결정론 폴백은 정직하게 UNKNOWN 이다:
                       휴리스틱 head_side 는 placeholder 급이라 뷰 라벨의 근거가
                       될 수 없다 — 추측으로 없는 옆면을 만들지 않는다
  3. 같은-펫 일관성  — 시그니처(HSV 히스토그램) 교집합. CONSISTENT / UNCERTAIN /
                       LIKELY_MISMATCH. 의심 레퍼런스는 **보존**되고 기록만 된다
  4. 역할 선택       — 결정론적: selection_score = base_quality × role_fit,
                       동점은 created_at → id. 후보 없음 = MISSING (실패 아님)
  5. 커버리지/등급   — GOOD/PARTIAL/MISSING 보고 + 잠정 완성도 등급
  6. 불변 세트 저장  — pet_reference_sets version N+1. 선택에 쓰인 레퍼런스별
                       분석 전체를 세트에 박제한다 (결정론·근거 재현)

── 원칙 ────────────────────────────────────────────────────────────────────
* 원본만 선택 대상이다 (ORIGINAL 우선 원칙). 파생 누끼는 분석 보조로만 쓴다.
* 이 모듈은 레퍼런스 대장을 읽기만 하고 스토리지에 아무것도 올리지 않는다.
* 컴포넌트 점수와 최종 점수를 **둘 다** 저장한다 — 왜 선택됐는지 나중에 안다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

SELECTION_ANALYZER_VERSION = "refset-selection-v1"
QUALITY_ANALYZER_VERSION = "refset-quality-v1"
CONSISTENCY_ANALYZER_VERSION = "refset-consistency-v1-hsv"
DETERMINISTIC_CLASSIFIER_VERSION = "deterministic-unknown-v1"

ROLES = (
    "PRIMARY_FACE",
    "PRIMARY_FULL_BODY",
    "PRIMARY_FRONT",
    "PRIMARY_LEFT",
    "PRIMARY_RIGHT",
    "PRIMARY_BACK",
    "PRIMARY_3Q",
    "PRIMARY_TAIL",
    "PRIMARY_MARKINGS",
)

CONSISTENT = "CONSISTENT"
UNCERTAIN = "UNCERTAIN"
LIKELY_MISMATCH = "LIKELY_MISMATCH"

#: 잠정 임계값 (합성 캘리브레이션 기준; CONSISTENCY_ANALYZER_VERSION 이 봉인).
#: 같은 코트의 다른 사진끼리 히스토그램 교집합은 높게, 전혀 다른 코트는 0 근처로
#: 나온다 — 완벽한 생체인식이 아니라 "명백히 다른 동물" 감지가 목적이다.
_CONSISTENT_MIN_INTERSECTION = 0.30
_UNCERTAIN_MIN_INTERSECTION = 0.12

#: 커버리지 GOOD 판정 최소 선택 점수 (잠정).
_COVERAGE_GOOD_MIN_SCORE = 0.55

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"

_CONF_WEIGHT = {"high": 1.0, "medium": 0.75, "low": 0.5}

#: base_quality 가중치. 컴포넌트가 unknown(None)이면 그 가중치를 제외하고
#: 재정규화한다 — 모르는 값을 0 이나 0.5 로 뭉개지 않는다.
_QUALITY_WEIGHTS = {
    "resolution": 0.10,
    "sharpness": 0.20,
    "subject_size": 0.15,
    "detection": 0.15,
    "segmentation": 0.15,
    "person_free": 0.15,
    "not_cropped": 0.10,
}


class PetReferenceSetError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table() -> str:
    return os.getenv("PET_REFERENCE_SETS_TABLE", "pet_reference_sets")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_SETS: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_SETS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyzer_versions() -> dict[str, Any]:
    from . import pet_identity_service, vlm_identity

    return {
        **pet_identity_service.analyzer_versions(),
        "selection": SELECTION_ANALYZER_VERSION,
        "quality": QUALITY_ANALYZER_VERSION,
        "consistency": CONSISTENCY_ANALYZER_VERSION,
        "view_classifier": (
            vlm_identity.VLM_CLASSIFIER_VERSION
            if vlm_identity.is_enabled()
            else DETERMINISTIC_CLASSIFIER_VERSION
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
# 뷰/포즈 분류
# ══════════════════════════════════════════════════════════════════════════


def _unknown_classification() -> dict[str, Any]:
    """결정론 폴백 — 뷰를 추측하지 않는다. 없는 옆면을 만들어 내지 않는다."""
    return {
        "view_label": "UNKNOWN",
        "view_confidence": "low",
        "pose_label": "UNKNOWN",
        "pose_confidence": "low",
        "visibility": {
            k: UNKNOWN
            for k in (
                "face_visible",
                "full_body_visible",
                "left_side_visible",
                "right_side_visible",
                "paws_visible",
                "tail_visible",
                "ears_visible",
                "distinct_markings_visible",
                "heavy_occlusion",
                "person_obstruction",
            )
        },
        "source": DETERMINISTIC_CLASSIFIER_VERSION,
    }


def classify_reference_view_pose(
    original_bytes: Optional[bytes], mime_type: Optional[str]
) -> dict[str, Any]:
    """VLM 이 켜져 있으면 실제 분류, 아니면(또는 실패하면) 정직한 UNKNOWN."""
    from . import vlm_identity

    if vlm_identity.is_enabled() and original_bytes:
        result = vlm_identity.classify_reference(original_bytes, mime_type or "image/jpeg")
        if result:
            return result
    return _unknown_classification()


# ══════════════════════════════════════════════════════════════════════════
# 품질 컴포넌트
# ══════════════════════════════════════════════════════════════════════════


def _sharpness_score(cut_rgba: np.ndarray) -> Optional[float]:
    """마스크 영역 라플라시안 분산 → 0..1. cv2 없으면 None (unknown)."""
    try:
        import cv2

        from .pet_identity_service import subject_mask

        mask = subject_mask(cut_rgba)
        if int(mask.sum()) < 64:
            return None
        ys, xs = np.where(mask)
        crop = cut_rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1, :3]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return round(min(1.0, var / 300.0), 4)
    except Exception:
        return None


def _subject_size_score(area_fraction: Optional[float]) -> Optional[float]:
    """프레임 대비 피사체 크기 — 0.10~0.70 이 이상적 밴드."""
    if not isinstance(area_fraction, (int, float)):
        return None
    a = float(area_fraction)
    if 0.10 <= a <= 0.70:
        return 1.0
    dist = (0.10 - a) if a < 0.10 else (a - 0.70)
    return round(max(0.0, 1.0 - dist * 4.0), 4)


def quality_components(
    ref: Any, eligibility: dict[str, Any], cut_rgba: Optional[np.ndarray]
) -> dict[str, Any]:
    """컴포넌트별 점수 (모르면 None) + 재정규화 가중 평균 base_quality."""
    from .pet_identity_service import subject_mask

    resolution = None
    if isinstance(ref.width, int) and isinstance(ref.height, int) and ref.width and ref.height:
        resolution = round(min(1.0, (ref.width * ref.height) / 1_000_000.0), 4)

    area_fraction = None
    sharpness = None
    if cut_rgba is not None:
        mask = subject_mask(cut_rgba)
        h, w = mask.shape
        if int(mask.sum()) >= 64:
            area_fraction = float(mask.sum()) / float(h * w)
        sharpness = _sharpness_score(cut_rgba)
    if area_fraction is None:
        maf = eligibility.get("mask_area_fraction")
        area_fraction = float(maf) if isinstance(maf, (int, float)) else None

    detection = eligibility.get("detection_confidence")
    detection = round(float(detection), 4) if isinstance(detection, (int, float)) else None
    segmentation = eligibility.get("segmentation_quality_score")
    segmentation = (
        round(float(segmentation), 4) if isinstance(segmentation, (int, float)) else None
    )

    person = eligibility.get("person_contamination")
    person_free = None if person == UNKNOWN or person is None else (0.0 if person else 1.0)

    border = eligibility.get("border_contact")
    not_cropped = None
    if isinstance(border, list):
        not_cropped = 1.0 if not border else 0.4

    components: dict[str, Optional[float]] = {
        "resolution": resolution,
        "sharpness": sharpness,
        "subject_size": _subject_size_score(area_fraction),
        "detection": detection,
        "segmentation": segmentation,
        "person_free": person_free,
        "not_cropped": not_cropped,
    }

    total_w = sum(_QUALITY_WEIGHTS[k] for k, v in components.items() if v is not None)
    base = (
        round(
            sum(_QUALITY_WEIGHTS[k] * v for k, v in components.items() if v is not None)
            / total_w,
            4,
        )
        if total_w > 0
        else 0.0
    )
    return {
        "analyzer": QUALITY_ANALYZER_VERSION,
        "components": components,
        "known_components": sum(1 for v in components.values() if v is not None),
        "base_quality": base,
    }


# ══════════════════════════════════════════════════════════════════════════
# 같은-펫 일관성
# ══════════════════════════════════════════════════════════════════════════


def assess_consistency(
    signatures: dict[str, Optional[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    """
    레퍼런스별 같은-펫 일관성. 완벽한 생체인식이 아니다 — 명백히 다른 동물
    (코트 색 분포가 전혀 다른)을 보수적으로 표시하는 것이 목적이다.
    의심 레퍼런스도 삭제/수정되지 않는다 — 평가만 기록된다.
    """
    from .pet_identity_service import signature_similarity

    out: dict[str, dict[str, Any]] = {}
    with_sig = {rid: s for rid, s in signatures.items() if s}

    for rid, sig in signatures.items():
        if not sig:
            out[rid] = {
                "analyzer": CONSISTENCY_ANALYZER_VERSION,
                "label": UNCERTAIN,
                "reason": "no_signature",
                "mean_hist_intersection": None,
            }
            continue
        others = [s for orid, s in with_sig.items() if orid != rid]
        if not others:
            out[rid] = {
                "analyzer": CONSISTENCY_ANALYZER_VERSION,
                "label": CONSISTENT,
                "reason": "single_reference",
                "mean_hist_intersection": None,
            }
            continue
        inters = [
            sim["hist_intersection"]
            for o in others
            if (sim := signature_similarity(sig, o)).get("comparable")
        ]
        if not inters:
            out[rid] = {
                "analyzer": CONSISTENCY_ANALYZER_VERSION,
                "label": UNCERTAIN,
                "reason": "no_comparable_signature",
                "mean_hist_intersection": None,
            }
            continue
        mean = round(float(np.mean(inters)), 4)
        if mean >= _CONSISTENT_MIN_INTERSECTION:
            label, reason = CONSISTENT, "coat_distribution_overlap"
        elif mean >= _UNCERTAIN_MIN_INTERSECTION:
            label, reason = UNCERTAIN, "low_coat_distribution_overlap"
        else:
            label, reason = LIKELY_MISMATCH, "coat_distribution_disjoint"
        out[rid] = {
            "analyzer": CONSISTENCY_ANALYZER_VERSION,
            "label": label,
            "reason": reason,
            "mean_hist_intersection": mean,
        }
    return out


# ══════════════════════════════════════════════════════════════════════════
# 역할 선택
# ══════════════════════════════════════════════════════════════════════════


def _conf_w(classification: dict[str, Any], key: str = "view_confidence") -> float:
    return _CONF_WEIGHT.get(str(classification.get(key) or "low"), 0.5)


def role_fit(role: str, analysis: dict[str, Any]) -> tuple[float, str]:
    """
    (fit 0..1, 근거 문자열). fit 0 = 이 레퍼런스로 이 역할을 채울 수 없음.
    뷰 기반 판정은 분류 신뢰도로 감쇠된다. 근거 없는 역할 충족은 없다.
    """
    c = analysis["classification"]
    e = analysis["eligibility"]
    vis = c.get("visibility") or {}
    view = str(c.get("view_label") or "UNKNOWN")
    vw = _conf_w(c)

    penalty = 1.0
    if vis.get("heavy_occlusion") == "yes":
        penalty *= 0.5
    if vis.get("person_obstruction") == "yes":
        penalty *= 0.5

    def out(fit: float, basis: str) -> tuple[float, str]:
        return round(fit * penalty, 4), basis

    if role == "PRIMARY_FACE":
        if view == "FACE_CLOSEUP":
            return out(1.0 * vw, "view:FACE_CLOSEUP")
        if vis.get("face_visible") == "yes":
            return out(0.9, "visibility:face_visible")
        return 0.0, "face_not_evidenced"

    if role == "PRIMARY_FULL_BODY":
        if view == "FULL_BODY" or vis.get("full_body_visible") == "yes":
            return out(1.0, "visibility:full_body_visible")
        if e.get("full_body_visible") == "likely":
            # 결정론적 근거(프레임에 잘리지 않음)만 있을 때 — 낮은 fit 으로 인정.
            return out(0.6, "deterministic:no_border_contact")
        return 0.0, "full_body_not_evidenced"

    if role == "PRIMARY_FRONT":
        if view == "FRONT":
            return out(1.0 * vw, "view:FRONT")
        if view in ("FRONT_LEFT_3Q", "FRONT_RIGHT_3Q"):
            return out(0.7 * vw, f"view:{view}")
        return 0.0, "front_not_evidenced"

    if role == "PRIMARY_LEFT":
        if view == "LEFT":
            return out(1.0 * vw, "view:LEFT")
        if view == "FRONT_LEFT_3Q":
            return out(0.6 * vw, "view:FRONT_LEFT_3Q")
        return 0.0, "left_not_evidenced"

    if role == "PRIMARY_RIGHT":
        if view == "RIGHT":
            return out(1.0 * vw, "view:RIGHT")
        if view == "FRONT_RIGHT_3Q":
            return out(0.6 * vw, "view:FRONT_RIGHT_3Q")
        return 0.0, "right_not_evidenced"

    if role == "PRIMARY_BACK":
        if view == "BACK":
            return out(1.0 * vw, "view:BACK")
        return 0.0, "back_not_evidenced"

    if role == "PRIMARY_3Q":
        if view in ("FRONT_LEFT_3Q", "FRONT_RIGHT_3Q"):
            return out(1.0 * vw, f"view:{view}")
        return 0.0, "3q_not_evidenced"

    if role == "PRIMARY_TAIL":
        if vis.get("tail_visible") == "yes":
            return out(0.9, "visibility:tail_visible")
        return 0.0, "tail_not_evidenced"

    if role == "PRIMARY_MARKINGS":
        if vis.get("distinct_markings_visible") == "yes":
            return out(0.9, "visibility:distinct_markings")
        return 0.0, "markings_not_evidenced"

    return 0.0, "unknown_role"


def select_roles(
    analyses: dict[str, dict[str, Any]], order: list[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """
    결정론적 역할 선택. order = 레퍼런스 id 의 안정 정렬(created_at → id).

    반환: (items, coverage). 후보 없는 역할은 items 에 없고 coverage 에 MISSING.
    """
    items: list[dict[str, Any]] = []
    role_status: dict[str, str] = {}

    for role in ROLES:
        candidates: list[tuple[float, int, str, str]] = []
        for idx, rid in enumerate(order):
            a = analyses[rid]
            if not a["selectable"]:
                continue
            fit, basis = role_fit(role, a)
            if fit <= 0:
                continue
            score = round(a["quality"]["base_quality"] * fit, 4)
            candidates.append((score, idx, rid, basis))
        if not candidates:
            role_status[role] = "MISSING"
            continue
        # 점수 내림차순, 동점은 등록 순서(안정) — 결정론.
        candidates.sort(key=lambda t: (-t[0], t[1]))
        score, _, rid, basis = candidates[0]
        a = analyses[rid]
        items.append(
            {
                "reference_id": rid,
                "role": role,
                "view_label": a["classification"].get("view_label", "UNKNOWN"),
                "pose_label": a["classification"].get("pose_label", "UNKNOWN"),
                "view_confidence": a["classification"].get("view_confidence", "low"),
                "selection_score": score,
                "component_scores": a["quality"]["components"],
                "base_quality": a["quality"]["base_quality"],
                "rank": 1,
                "selection_reason": basis,
            }
        )
        fit_indirect = basis.startswith("deterministic:") or "3Q" in basis
        low_conf = a["classification"].get("view_confidence") == "low" and basis.startswith("view:")
        role_status[role] = (
            "GOOD"
            if score >= _COVERAGE_GOOD_MIN_SCORE and not fit_indirect and not low_conf
            else "PARTIAL"
        )

    coverage = {
        "face": role_status.get("PRIMARY_FACE", "MISSING"),
        "full_body": role_status.get("PRIMARY_FULL_BODY", "MISSING"),
        "front": role_status.get("PRIMARY_FRONT", "MISSING"),
        "left": role_status.get("PRIMARY_LEFT", "MISSING"),
        "right": role_status.get("PRIMARY_RIGHT", "MISSING"),
        "back": role_status.get("PRIMARY_BACK", "MISSING"),
        "tail": role_status.get("PRIMARY_TAIL", "MISSING"),
        "markings": role_status.get("PRIMARY_MARKINGS", "MISSING"),
    }
    return items, coverage


def completeness(coverage: dict[str, str]) -> tuple[str, float]:
    """
    잠정 등급 (제품 요구 확정 전 — 하류는 tier 가 아니라 coverage 자체를 봐야
    한다). 점수 = GOOD 1.0 / PARTIAL 0.5 평균.
    """
    def ok(key: str) -> bool:
        return coverage.get(key) in ("GOOD", "PARTIAL")

    score = round(
        sum(1.0 if v == "GOOD" else (0.5 if v == "PARTIAL" else 0.0) for v in coverage.values())
        / max(1, len(coverage)),
        4,
    )
    sides = [k for k in ("front", "left", "right", "back") if ok(k)]
    tier = "LIMITED"
    if ok("face") and ok("full_body"):
        tier = "MINIMUM"
        if sides:
            tier = "GOOD"
            if len(sides) >= 2 and (ok("tail") or ok("markings")):
                tier = "EXCELLENT"
    return tier, score


# ══════════════════════════════════════════════════════════════════════════
# 세트 빌드 / 조회
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PetReferenceSet:
    id: Optional[str]
    pet_id: str
    user_id: str
    version: int
    status: str
    identity_profile_id: Optional[str] = None
    identity_profile_version: Optional[int] = None
    source_reference_ids: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    reference_analysis: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, str] = field(default_factory=dict)
    completeness_tier: str = "LIMITED"
    completeness_score: float = 0.0
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    deduplicated: bool = False


_SELECT = (
    "id, pet_id, user_id, version, status, identity_profile_id, identity_profile_version, "
    "source_reference_ids, items, reference_analysis, coverage, completeness_tier, "
    "completeness_score, analyzer_versions, created_at"
)


def _to_set(row: dict[str, Any], *, deduplicated: bool = False) -> PetReferenceSet:
    return PetReferenceSet(
        id=(str(row["id"]) if row.get("id") else None),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_PARTIAL),
        identity_profile_id=(str(row["identity_profile_id"]) if row.get("identity_profile_id") else None),
        identity_profile_version=row.get("identity_profile_version"),
        source_reference_ids=list(row.get("source_reference_ids") or []),
        items=list(row.get("items") or []),
        reference_analysis=dict(row.get("reference_analysis") or {}),
        coverage=dict(row.get("coverage") or {}),
        completeness_tier=str(row.get("completeness_tier") or "LIMITED"),
        completeness_score=float(row.get("completeness_score") or 0.0),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        deduplicated=deduplicated,
    )


async def _set_rows(pet_id: str) -> list[dict[str, Any]]:
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
            logger.exception("레퍼런스 세트 조회 실패 (pet=%s)", pid)
            raise PetReferenceSetError(
                "REFERENCE_SETS_UNAVAILABLE", "레퍼런스 세트를 확인하지 못했습니다.", status=503
            ) from e
    return [r for r in _MOCK_SETS if r.get("pet_id") == pid]


async def _assert_owned(user_id: str, pet_id: str) -> None:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise PetReferenceSetError(e.code, e.message, status=e.status) from e


async def list_sets(*, user_id: str, pet_id: str) -> list[PetReferenceSet]:
    await _assert_owned(user_id, pet_id)
    return [_to_set(r) for r in await _set_rows(pet_id)]


async def get_set(
    *, user_id: str, pet_id: str, version: Optional[int] = None
) -> Optional[PetReferenceSet]:
    await _assert_owned(user_id, pet_id)
    rows = await _set_rows(pet_id)
    if not rows:
        return None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                return _to_set(r)
        return None
    return _to_set(max(rows, key=lambda r: int(r.get("version") or 0)))


async def _insert_set_row(row: dict[str, Any]) -> tuple[bool, Optional[Exception]]:
    if _use_db() and _supabase():
        try:
            _supabase().table(_table()).insert(row).execute()
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, e
    for r in _MOCK_SETS:
        if r["pet_id"] == row["pet_id"] and int(r["version"]) == int(row["version"]):
            return False, PetReferenceSetError("DUPLICATE", "duplicate version")
    _MOCK_SETS.append(dict(row))
    return True, None


async def build_reference_set(
    *,
    user_id: str,
    pet_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    skip_if_unchanged: bool = True,
) -> PetReferenceSet:
    """
    원본 + Phase 2 프로필 → 새 신뢰 레퍼런스 세트 버전.

    * Phase 2 프로필을 먼저 보장한다 (멱등 빌드 — 프로필의 레퍼런스별 적격성과
      시그니처를 그대로 재사용해 분석을 중복하지 않는다).
    * skip_if_unchanged=True: 최신 세트가 같은 원본 집합 + 같은 분석기 버전 +
      같은 프로필 버전이면 새 버전을 만들지 않는다.
    * 레퍼런스 대장과 스토리지는 읽기 전용이다.
    """
    from . import pet_identity_service, pet_reference_service

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise PetReferenceSetError("REFERENCE_SET_INVALID", "user_id 와 pet_id 가 필요합니다.")

    try:
        refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    except pet_reference_service.PetReferenceError as e:
        raise PetReferenceSetError(e.code, e.message, status=e.status) from e

    originals = [
        r
        for r in refs
        if r.role == pet_reference_service.ROLE_ORIGINAL
        and r.acceptance_state == pet_reference_service.STATE_ACCEPTED
    ]
    if not originals:
        raise PetReferenceSetError(
            "NO_ORIGINAL_REFERENCES",
            "세트를 만들 원본 레퍼런스가 없습니다.",
            status=409,
        )

    # ── Phase 2 프로필 보장 (멱등) — 적격성/시그니처의 단일 출처 ─────────
    try:
        profile = await pet_identity_service.build_identity_profile(
            user_id=uid, pet_id=pid, fetch_bytes=fetch_bytes, skip_if_unchanged=True
        )
    except pet_identity_service.PetIdentityError as e:
        raise PetReferenceSetError(e.code, e.message, status=e.status) from e

    versions = analyzer_versions()
    source_ids = sorted(str(r.id) for r in originals if r.id)

    if skip_if_unchanged:
        rows = await _set_rows(pid)
        if rows:
            latest = _to_set(max(rows, key=lambda r: int(r.get("version") or 0)))
            if (
                sorted(latest.source_reference_ids) == source_ids
                and latest.analyzer_versions == versions
                and latest.identity_profile_version == profile.version
            ):
                return _to_set(
                    max(rows, key=lambda r: int(r.get("version") or 0)), deduplicated=True
                )

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    pairing = pet_reference_service.pair_cutouts(refs)

    # ── 레퍼런스별 분석 (안정 순서: created_at → id) ─────────────────────
    ordered = sorted(originals, key=lambda r: (r.created_at or "", str(r.id)))
    order = [str(r.id) for r in ordered]

    analyses: dict[str, dict[str, Any]] = {}
    signatures: dict[str, Optional[dict[str, Any]]] = {}
    from . import vlm_identity

    for ref in ordered:
        rid = str(ref.id)
        eligibility = profile.reference_eligibility.get(rid)
        cut = pairing.get(rid)
        cut_rgba = None
        if cut is not None:
            cut_bytes = fetch(cut)
            if cut_bytes:
                cut_rgba = pet_identity_service.load_rgba(cut_bytes)
        if eligibility is None:
            eligibility = pet_identity_service.evaluate_reference_eligibility(ref, cut_rgba)
        signatures[rid] = eligibility.get("signature") or (
            pet_identity_service.compute_reference_signature(cut_rgba)
            if cut_rgba is not None
            else None
        )

        original_bytes = fetch(ref) if vlm_identity.is_enabled() else None
        classification = classify_reference_view_pose(original_bytes, ref.mime_type)

        analyses[rid] = {
            "reference_id": rid,
            "content_id": ref.content_id,
            "object_path": ref.object_path,
            "eligibility": eligibility,
            "classification": classification,
            "quality": quality_components(ref, eligibility, cut_rgba),
        }

    consistency = assess_consistency(signatures)
    for rid, a in analyses.items():
        a["consistency"] = consistency[rid]
        # 선택 대상: 적격 ∧ 명백한 불일치 아님. 의심 레퍼런스는 보존·기록되지만
        # 역할 선택에서는 제외된다.
        a["selectable"] = bool(
            a["eligibility"].get("usable_for_identity")
            and consistency[rid]["label"] != LIKELY_MISMATCH
        )
        if not a["selectable"]:
            a["excluded_reason"] = (
                "excluded_likely_mismatch"
                if consistency[rid]["label"] == LIKELY_MISMATCH
                else "not_usable_for_identity"
            )

    items, coverage = select_roles(analyses, order)
    tier, score = completeness(coverage)

    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "pet_id": pid,
        "user_id": uid,
        "version": 1,
        "status": STATUS_COMPLETE if items else STATUS_PARTIAL,
        "identity_profile_id": profile.id,
        "identity_profile_version": profile.version,
        "source_reference_ids": source_ids,
        "items": items,
        "reference_analysis": analyses,
        "coverage": coverage,
        "completeness_tier": tier,
        "completeness_score": score,
        "analyzer_versions": versions,
        "created_at": _now_iso(),
    }

    for _ in range(3):
        rows = await _set_rows(pid)
        row["version"] = (max((int(r.get("version") or 0) for r in rows), default=0)) + 1
        ok, err = await _insert_set_row(row)
        if ok:
            return _to_set(row)
        last_err = err

    logger.error("레퍼런스 세트 기록 실패 (pet=%s): %s", pid, last_err)
    raise PetReferenceSetError(
        "REFERENCE_SETS_UNAVAILABLE", "레퍼런스 세트를 저장하지 못했습니다.", status=503
    )
