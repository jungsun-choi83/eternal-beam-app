"""
정본 후보 QA (Phase 4) — 생성된 **마스터 이미지** 전용. 이후의 영상 QA 가 아니다.

── 판정 철학 ───────────────────────────────────────────────────────────────
완벽한 동물 생체인식 QA 인 척하지 않는다. 컴포넌트 점수와 이유를 전부 돌려주고,
결론은 PASS / REVIEW / FAIL 삼값이다:

  * FAIL   — 명백한 반증 (신원 시그니처 괴리, 코트 색 계열 불일치, 빈 누끼,
             VLM 이 다른 펫/해부학 오류/사람 등장을 확언)
  * PASS   — 결정론 검사 전부 통과 **그리고 VLM 이 same_pet/anatomy 를 확언**.
             합성 캘리브레이션 임계값(Phase 3 한계 1)만으로는 절대 자동 승인하지
             않는다 — VLM 확언이 없으면 최대 REVIEW 다.
  * REVIEW — 그 외 전부 (unknown 이 남아 있거나 경계값)

qa_result 예:
  {"identity_similarity": 0.62, "checks": {...}, "reasons": [...],
   "decision": "REVIEW", "qa_version": "canonical-qa-v1"}
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

CANONICAL_QA_VERSION = "canonical-qa-v1"

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"

#: 코트 색 이름 → 계열 (canonical 후보와 프로필의 색 비교용).
_COLOR_FAMILY = {
    "black": "dark",
    "dark_gray": "dark",
    "dark_brown": "brown",
    "brown": "brown",
    "red_brown": "brown",
    "tan": "brown",
    "golden": "brown",
    "cream": "light",
    "white": "light",
    "gray": "gray",
}


def _f(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except ValueError:
        return default


def _identity_thresholds() -> tuple[float, float]:
    """(pass_min, fail_max) — 잠정값, env 로 조정 가능."""
    return _f("CANONICAL_QA_IDENTITY_PASS", 0.30), _f("CANONICAL_QA_IDENTITY_FAIL", 0.12)


def _families(color_entries: list[dict[str, Any]], *, min_fraction: float = 0.15) -> set[str]:
    out = set()
    for c in color_entries or []:
        if isinstance(c, dict) and float(c.get("fraction") or 0) >= min_fraction:
            fam = _COLOR_FAMILY.get(str(c.get("name") or ""))
            if fam:
                out.add(fam)
    return out


def evaluate_candidate(
    *,
    cutout_rgba: Optional[np.ndarray],
    profile: Any,
    reference_signatures: list[dict[str, Any]],
    vlm_qa: Optional[dict[str, Any]] = None,
    compare_structure: bool = True,
) -> dict[str, Any]:
    """
    정본 후보 1개 평가. 입력:
      cutout_rgba          후보의 누끼 RGBA (없으면 대부분 unknown → REVIEW 상한)
      profile              Phase 2 PetIdentityProfile
      reference_signatures 생성에 쓴 레퍼런스들의 시그니처
      vlm_qa               vlm_identity.qa_canonical_image 결과 (None = 미실행)
      compare_structure    프로필 bbox 비율과 비교할지. **포즈를 바꾸는** 키프레임
                           (LIE/SLEEP 등)은 비율이 정당하게 달라지므로 False —
                           그 경우 구조 검증은 VLM 해부학 확인이 담당한다.
    """
    from .pet_identity_service import (
        analyze_structural_identity,
        analyze_visual_identity,
        compute_reference_signature,
        signature_similarity,
        subject_mask,
    )

    checks: dict[str, str] = {}
    reasons: list[str] = []
    identity_similarity: Optional[float] = None
    pass_min, fail_max = _identity_thresholds()

    # ── 누끼/사용성 ───────────────────────────────────────────────────────
    if cutout_rgba is None:
        checks["cutout"] = "unknown"
        reasons.append("cutout_unavailable")
    else:
        mask = subject_mask(cutout_rgba)
        if int(mask.sum()) < 64:
            checks["cutout"] = FAIL
            reasons.append("cutout_empty")
        else:
            h, w = mask.shape
            frac = float(mask.sum()) / float(h * w)
            if min(h, w) < int(_f("CANONICAL_QA_MIN_RESOLUTION", 256)):
                checks["cutout"] = REVIEW
                reasons.append("low_resolution")
            elif not (0.05 <= frac <= 0.90):
                checks["cutout"] = REVIEW
                reasons.append("subject_size_out_of_band")
            else:
                checks["cutout"] = PASS

    # ── 시각 신원: 시그니처 유사도 + 코트 색 계열 ─────────────────────────
    if cutout_rgba is not None and checks.get("cutout") != FAIL:
        cand_sig = compute_reference_signature(cutout_rgba)
        sims = [
            s["hist_intersection"]
            for ref_sig in reference_signatures
            if ref_sig and cand_sig
            and (s := signature_similarity(cand_sig, ref_sig)).get("comparable")
        ]
        if sims:
            identity_similarity = round(float(np.mean(sims)), 4)
            if identity_similarity < fail_max:
                checks["identity_similarity"] = FAIL
                reasons.append(f"identity_similarity {identity_similarity} < {fail_max}")
            elif identity_similarity >= pass_min:
                checks["identity_similarity"] = PASS
            else:
                checks["identity_similarity"] = REVIEW
                reasons.append(f"identity_similarity {identity_similarity} borderline")
        else:
            checks["identity_similarity"] = "unknown"
            reasons.append("no_reference_signatures")

        cand_visual = analyze_visual_identity(cutout_rgba)
        prof_coat = ((getattr(profile, "visual_identity", None) or {}).get("coat")) or {}
        cand_fams = _families((cand_visual.get("coat") or {}).get("palette") or [])
        prof_fams = _families(
            (prof_coat.get("dominant_colors") or []) + (prof_coat.get("secondary_colors") or [])
        )
        if not prof_fams or not cand_fams:
            checks["coat_colors"] = "unknown"
            reasons.append("coat_families_unavailable")
        elif cand_fams & prof_fams:
            checks["coat_colors"] = PASS
        else:
            checks["coat_colors"] = FAIL
            reasons.append(f"coat_families_disjoint cand={sorted(cand_fams)} profile={sorted(prof_fams)}")

        # ── 구조: bbox 비율의 근사 일치 (포즈 유지 시에만) ─────────────────
        cand_struct = analyze_structural_identity(cutout_rgba)
        prof_sil = ((getattr(profile, "structural_identity", None) or {}).get("silhouette")) or {}
        cand_ar = ((cand_struct.get("silhouette") or {}).get("bbox_aspect_ratio"))
        prof_ar = prof_sil.get("bbox_aspect_ratio")
        if not compare_structure:
            checks["structure"] = "unknown"
            reasons.append("structure_comparison_skipped_pose_change")
        elif isinstance(cand_ar, (int, float)) and isinstance(prof_ar, (int, float)) and prof_ar:
            q = float(cand_ar) / float(prof_ar)
            if 0.55 <= q <= 1.8:
                checks["structure"] = PASS
            else:
                checks["structure"] = REVIEW
                reasons.append(f"aspect_ratio_quotient {round(q, 2)} outside [0.55, 1.8]")
        else:
            checks["structure"] = "unknown"
            reasons.append("structure_not_comparable")
    else:
        checks.setdefault("identity_similarity", "unknown")
        checks.setdefault("coat_colors", "unknown")
        checks.setdefault("structure", "unknown")

    # ── VLM 정본 확인 (없으면 unknown — PASS 불가) ───────────────────────
    def _vlm(key: str) -> str:
        return str((vlm_qa or {}).get(key) or "unknown")

    if vlm_qa:
        if _vlm("same_pet") == "no":
            checks["vlm_same_pet"] = FAIL
            reasons.append("vlm_says_different_pet")
        elif _vlm("same_pet") == "yes":
            checks["vlm_same_pet"] = PASS
        else:
            checks["vlm_same_pet"] = "unknown"

        if _vlm("anatomy_plausible") == "no":
            checks["vlm_anatomy"] = FAIL
            reasons.append("vlm_anatomy_implausible")
        elif _vlm("anatomy_plausible") == "yes":
            checks["vlm_anatomy"] = PASS
        else:
            checks["vlm_anatomy"] = "unknown"

        if _vlm("human_present") == "yes" or _vlm("single_pet") == "no":
            checks["vlm_composition"] = FAIL
            reasons.append("vlm_composition_contaminated")
        elif _vlm("major_occlusion") == "yes" or _vlm("background_neutral") == "no":
            checks["vlm_composition"] = REVIEW
            reasons.append("vlm_composition_not_canonical")
        elif _vlm("single_pet") == "yes" and _vlm("human_present") == "no":
            checks["vlm_composition"] = PASS
        else:
            checks["vlm_composition"] = "unknown"
    else:
        checks["vlm_same_pet"] = "unknown"
        checks["vlm_anatomy"] = "unknown"
        checks["vlm_composition"] = "unknown"
        reasons.append("vlm_qa_unavailable")

    # ── 판정 ─────────────────────────────────────────────────────────────
    values = list(checks.values())
    if FAIL in values:
        decision = FAIL
    elif all(v == PASS for v in values):
        # 여기 도달 = 결정론 검사 + VLM 확언 전부 PASS. 합성 임계값만으로는
        # 이 분기에 못 온다 (VLM unknown 이면 all-PASS 가 성립하지 않는다).
        decision = PASS
    else:
        decision = REVIEW

    return {
        "qa_version": CANONICAL_QA_VERSION,
        "identity_similarity": identity_similarity,
        "checks": checks,
        "reasons": reasons,
        "decision": decision,
        "vlm": (
            {k: vlm_qa[k] for k in ("source", "model") if vlm_qa and k in vlm_qa}
            if vlm_qa
            else None
        ),
    }
