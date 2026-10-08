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

코트 패턴은 이름까지 일치(좌우 반전 포함)할 때만 PASS 다. 계열(_COLOR_FAMILY)
정규화 후에도 모순이 남고, 프로필 신뢰도가 high 이며, 레퍼런스 2장 이상이 그 패턴을
지지할 때만 하드페일한다. 그 사이(계열만 일치, 저신뢰, 단일 레퍼런스)는 전부 REVIEW 다.
임베딩은 끝까지 보조 신호로만 쓴다 — 다른 검사 결과를 덮어쓰지 않는다.

coat_pattern 은 **자문(advisory)** 검사다 (v4). REVIEW 로 남은 coat_pattern 하나가
핵심 검사(CORE_CHECKS) 전부 PASS 인 후보를 REVIEW 로 끌어내리지 않는다. 대신
검사값(REVIEW)과 이유/증거는 그대로 남겨 사람이 볼 수 있게 한다 — PASS 로 고쳐
쓰지 않는다. 단 coat_pattern=FAIL (강한 모순)은 여전히 전체를 FAIL 로 막는다.

qa_result 예:
  {"identity_similarity": 0.62, "checks": {...}, "reasons": [...],
   "vlm": {...}, "decision": "REVIEW", "qa_version": "canonical-qa-v5"}

v5 adds confidence-gated ``business_signals`` for Business QA vNext. The
legacy checks and legacy decision are deliberately still calculated by the v4
rules so old/new authority can be compared side by side.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

CANONICAL_QA_VERSION = "canonical-qa-v5"

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"

#: 자동 승인(PASS)에 전부 PASS 여야 하는 핵심 검사 — 신원/해부학/구도의 본체다.
CORE_CHECKS = (
    "cutout",
    "structure",
    "coat_colors",
    "vlm_anatomy",
    "vlm_same_pet",
    "vlm_composition",
    "visual_embedding",
    "identity_similarity",
)

#: 자문 검사 — REVIEW 여도 PASS 를 막지 않는다. FAIL 은 (다른 검사와 똑같이) 막는다.
ADVISORY_CHECKS = ("coat_pattern",)


def pass_requires_vlm() -> bool:
    """핵심 검사에 VLM 확언이 들어 있는가 — 그렇다면 VLM 없이는 PASS 가 불가능하다."""
    return any(key.startswith("vlm_") for key in CORE_CHECKS)


def decide(checks: dict[str, Any]) -> str:
    """검사표 → 삼값 판정.

    * 어디서든 FAIL 이 하나라도 있으면 (자문 검사 포함) FAIL.
    * 그 외엔 핵심 검사(CORE_CHECKS)와 자문이 아닌 나머지 검사가 전부 PASS 여야 PASS.
    * 자문 검사(ADVISORY_CHECKS)의 REVIEW/unknown 은 PASS 를 막지 않는다.
    """
    if FAIL in checks.values():
        return FAIL
    if any(checks.get(key) != PASS for key in CORE_CHECKS):
        return REVIEW
    if any(value != PASS for key, value in checks.items() if key not in ADVISORY_CHECKS):
        return REVIEW
    return PASS

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


def _identity_pass_threshold() -> float:
    """시그니처 PASS 하한 — 잠정값, env 로 조정 가능."""
    return _f("CANONICAL_QA_IDENTITY_PASS", 0.30)


def _signature_fail_threshold(*, gate_ready: bool, gate_sig_min: float) -> tuple[float, str]:
    """
    시그니처 하드페일 임계값의 **유일한** 출처.

    프로필의 same_individual_gate 가 준비되면 그 임계값이 권위를 가지되, env 기본선
    아래로는 내려가지 않는다. 임계값 판정이 여러 군데로 흩어지지 않도록 호출부는
    이 함수만 쓴다.
    """
    env_fail = _f("CANONICAL_QA_IDENTITY_FAIL", 0.12)
    if gate_ready:
        return max(env_fail, float(gate_sig_min)), "same_individual_gate"
    return env_fail, "canonical_qa_default"


def _embedding_threshold() -> float:
    return _f("CANONICAL_QA_EMBEDDING_MIN", 0.72)


def _support_count(field: Any) -> int:
    """융합 트레잇의 지지 레퍼런스 수 (단일 레퍼런스 안전장치용)."""
    if not isinstance(field, dict):
        return 0
    return len(field.get("support_reference_ids") or [])


def _pattern_tokens(value: Any) -> Optional[tuple[str, str, str]]:
    """'left|center|right' → 토큰 3개. 미지/형식 불일치는 None."""
    parts = [p.strip() for p in str(value or "").split("|")]
    if len(parts) != 3 or not all(_known_text(p) for p in parts):
        return None
    return (parts[0], parts[1], parts[2])


def _pattern_families(tokens: tuple[str, str, str]) -> tuple[str, str, str]:
    """색 이름 → 계열. 계열 미등록 이름은 이름 그대로 두어 비교 가능성을 유지한다."""
    return tuple(_COLOR_FAMILY.get(t, t) for t in tokens)  # type: ignore[return-value]


def _pattern_relation(profile_value: Any, candidate_value: Any) -> str:
    """
    코트 패턴 비교 결과:
      exact / mirror                — 이름까지 동일 (좌우 반전 포함) → PASS
      family / family_mirror        — 계열 정규화 후에만 동일 (white↔cream,
                                      golden↔tan 등) → REVIEW
      contradiction                 — 계열 정규화 후에도 남는 모순
      unknown                       — 한쪽이라도 측정 불가
    """
    prof = _pattern_tokens(profile_value)
    cand = _pattern_tokens(candidate_value)
    if not prof or not cand:
        return "unknown"
    if cand == prof:
        return "exact"
    if (cand[2], cand[1], cand[0]) == prof:
        return "mirror"
    prof_fams, cand_fams = _pattern_families(prof), _pattern_families(cand)
    if cand_fams == prof_fams:
        return "family"
    if (cand_fams[2], cand_fams[1], cand_fams[0]) == prof_fams:
        return "family_mirror"
    return "contradiction"


def _known_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() and value.strip().lower() != "unknown"


#: qa_result["vlm"] 로 보존하는 VLM 판정 필드 — 나중에 왜 그렇게 판정했는지
#: 되짚으려면 source/model 만으로는 부족하다.
_VLM_EVIDENCE_KEYS = (
    "same_pet",
    "same_pet_confidence",
    "face_head_consistent",
    "ear_muzzle_consistent",
    "distinctive_markings_consistent",
    "persistent_morphology_consistent",
    "presentation_difference_only",
    "anatomy_plausible",
    "single_pet",
    "human_present",
    "accessories_present",
    "background_neutral",
    "pose_neutral",
    "full_body_visible",
    "major_occlusion",
    "identity_notes",
    "source",
    "model",
    "analyzer",
    "called_tasks",
    "targeted_vlm_evidence",
)


def _vlm_evidence(vlm_qa: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """VLM 근거를 qa_result 에 보존한다 (미실행이면 None)."""
    if not vlm_qa:
        return None
    return {k: vlm_qa[k] for k in _VLM_EVIDENCE_KEYS if k in vlm_qa}


def _strong_trait(field: Any) -> bool:
    """Whether a fused profile trait has strong multi-reference support."""

    return bool(
        isinstance(field, dict)
        and str(field.get("confidence") or "").lower() == "high"
        and _support_count(field) >= 2
    )


def _strong_marking_evidence(profile_visual: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Summarize existing persistent marking evidence without inventing traits."""

    facial = profile_visual.get("facial_markings") or {}
    body = profile_visual.get("body_markings") or {}
    pattern = profile_visual.get("coat_pattern") or {}
    distinctive = profile_visual.get("distinctive_features") or {}
    strong_features = [
        item
        for item in (distinctive.get("items") or [])
        if _strong_trait(item)
    ] if isinstance(distinctive, dict) else []
    evidence = {
        "facial_markings": {
            "confidence": facial.get("confidence") if isinstance(facial, dict) else None,
            "support_reference_count": _support_count(facial),
        },
        "body_markings": {
            "confidence": body.get("confidence") if isinstance(body, dict) else None,
            "support_reference_count": _support_count(body),
        },
        "coat_pattern": {
            "confidence": pattern.get("confidence") if isinstance(pattern, dict) else None,
            "support_reference_count": _support_count(pattern),
        },
        "distinctive_features": [
            {
                "value": item.get("value"),
                "confidence": item.get("confidence"),
                "support_reference_count": _support_count(item),
            }
            for item in strong_features
        ],
    }
    return bool(
        _strong_trait(facial)
        or _strong_trait(body)
        or _strong_trait(pattern)
        or strong_features
    ), evidence


def _semantic_identity_signal(
    value: Any,
    *,
    confidence: str,
    presentation_only: bool,
    strong_profile_evidence: bool = True,
) -> str:
    """Translate VLM identity evidence into a confidence-gated business signal."""

    answer = str(value or "unknown").lower()
    if answer == "yes":
        return PASS
    if answer != "no":
        return "unknown"
    if presentation_only or confidence != "high" or not strong_profile_evidence:
        return REVIEW
    return FAIL


def _canonical_business_identity_signals(
    vlm_qa: Optional[dict[str, Any]],
    profile_visual: dict[str, Any],
    *,
    reference_evidence_count: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build semantic identity authority while leaving legacy checks untouched."""

    vlm = vlm_qa or {}
    confidence = str(vlm.get("same_pet_confidence") or "unknown").lower()
    presentation_only = str(vlm.get("presentation_difference_only") or "no").lower() == "yes"
    strong_markings, marking_evidence = _strong_marking_evidence(profile_visual)
    same_gate = profile_visual.get("same_individual_gate") or {}
    raw_profile_count = (
        same_gate.get("strict_lineage_reference_count")
        if isinstance(same_gate, dict)
        else 0
    )
    try:
        profile_reference_count = max(0, int(raw_profile_count or 0))
    except (TypeError, ValueError):
        profile_reference_count = 0
    multi_reference = max(reference_evidence_count, profile_reference_count) >= 2
    signals = {
        "canonical_face_head_identity": _semantic_identity_signal(
            vlm.get("face_head_consistent"),
            confidence=confidence,
            presentation_only=presentation_only,
            strong_profile_evidence=multi_reference,
        ),
        "canonical_ear_muzzle_identity": _semantic_identity_signal(
            vlm.get("ear_muzzle_consistent"),
            confidence=confidence,
            presentation_only=presentation_only,
            strong_profile_evidence=multi_reference,
        ),
        "canonical_distinctive_markings_identity": _semantic_identity_signal(
            vlm.get("distinctive_markings_consistent"),
            confidence=confidence,
            presentation_only=presentation_only,
            strong_profile_evidence=strong_markings,
        ),
        "canonical_persistent_morphology_identity": _semantic_identity_signal(
            vlm.get("persistent_morphology_consistent"),
            confidence=confidence,
            presentation_only=presentation_only,
            strong_profile_evidence=multi_reference,
        ),
    }
    return signals, {
        "same_pet_confidence": confidence,
        "presentation_difference_only": presentation_only,
        "strong_profile_marking_evidence": strong_markings,
        "reference_evidence_count": max(reference_evidence_count, profile_reference_count),
        "multi_reference_evidence": multi_reference,
        "profile_marking_evidence": marking_evidence,
        "signals": dict(signals),
    }


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
        compute_visual_embedding,
        compute_reference_signature,
        embedding_similarity,
        signature_similarity,
        subject_mask,
    )

    checks: dict[str, str] = {}
    reasons: list[str] = []
    identity_similarity: Optional[float] = None
    pass_min = _identity_pass_threshold()
    profile_visual = (getattr(profile, "visual_identity", None) or {})
    same_gate = (profile_visual.get("same_individual_gate") if isinstance(profile_visual, dict) else {}) or {}
    gate_thresholds = (same_gate.get("thresholds") if isinstance(same_gate, dict) else {}) or {}
    gate_ready = str((same_gate or {}).get("status") or "") == "ready"
    gate_sig_min = float(gate_thresholds.get("min_signature_hist_intersection") or 0.16)
    gate_emb_min = float(gate_thresholds.get("min_embedding_cosine_similarity") or _embedding_threshold())
    sig_fail_min, sig_fail_source = _signature_fail_threshold(gate_ready=gate_ready, gate_sig_min=gate_sig_min)

    identity_evidence: dict[str, Any] = {
        "profile": {
            "same_individual_gate": same_gate,
            "coat_pattern": profile_visual.get("coat_pattern") if isinstance(profile_visual, dict) else None,
            "facial_markings": profile_visual.get("facial_markings") if isinstance(profile_visual, dict) else None,
            "body_markings": profile_visual.get("body_markings") if isinstance(profile_visual, dict) else None,
            "distinctive_features": (
                profile_visual.get("distinctive_features") if isinstance(profile_visual, dict) else None
            ),
            "visual_embedding": profile_visual.get("visual_embedding") if isinstance(profile_visual, dict) else None,
        },
        "candidate": {},
        "comparisons": {},
    }

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
            identity_evidence["comparisons"]["signature_hist_intersection"] = {
                "mean": identity_similarity,
                "min": round(float(np.min(sims)), 4),
                "max": round(float(np.max(sims)), 4),
                "reference_count": len(sims),
                "fail_below": round(sig_fail_min, 4),
                "fail_threshold_source": sig_fail_source,
                "pass_at_or_above": round(pass_min, 4),
            }
            # 하드페일 임계값은 _signature_fail_threshold 한 곳에서만 나온다.
            if identity_similarity < sig_fail_min:
                checks["identity_similarity"] = FAIL
                label = (
                    "same_individual_gate_signature_contradiction"
                    if sig_fail_source == "same_individual_gate"
                    else "identity_similarity_below_minimum"
                )
                reasons.append(f"{label} {identity_similarity} < {round(sig_fail_min, 4)}")
            elif identity_similarity >= pass_min:
                checks["identity_similarity"] = PASS
            else:
                checks["identity_similarity"] = REVIEW
                reasons.append(f"identity_similarity {identity_similarity} borderline")
        else:
            checks["identity_similarity"] = "unknown"
            reasons.append("no_reference_signatures")

        cand_visual = analyze_visual_identity(cutout_rgba)
        identity_evidence["candidate"]["coat"] = cand_visual.get("coat")
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

        # ── same-individual 보강: 프로필의 융합 신호(coat_pattern/embedding) 소비 ──
        prof_visual = getattr(profile, "visual_identity", None) or {}
        prof_pattern = prof_visual.get("coat_pattern") or {}
        cand_pattern = (cand_visual.get("region_color_summary") or {})
        left = ((cand_pattern.get("left_third") or {}).get("dominant")) if isinstance(cand_pattern, dict) else None
        center = ((cand_pattern.get("center_third") or {}).get("dominant")) if isinstance(cand_pattern, dict) else None
        right = ((cand_pattern.get("right_third") or {}).get("dominant")) if isinstance(cand_pattern, dict) else None
        cand_pattern_value = (
            f"{left}|{center}|{right}"
            if isinstance(left, str) and left and left != "unknown"
            and isinstance(center, str) and center and center != "unknown"
            and isinstance(right, str) and right and right != "unknown"
            else None
        )
        identity_evidence["candidate"]["coat_pattern"] = cand_pattern_value
        prof_pattern_value = str(prof_pattern.get("value") or "").strip() if isinstance(prof_pattern, dict) else ""
        prof_pattern_conf = str(prof_pattern.get("confidence") or "low") if isinstance(prof_pattern, dict) else "low"
        prof_pattern_support = _support_count(prof_pattern)
        # 계열(_COLOR_FAMILY) 정규화 후에도 남는 모순만 진짜 모순으로 본다 —
        # white↔cream, golden↔tan 같은 이름 차이로 하드페일하지 않는다.
        pattern_relation = _pattern_relation(prof_pattern_value, cand_pattern_value)
        prof_pattern_tokens = _pattern_tokens(prof_pattern_value)
        cand_pattern_tokens = _pattern_tokens(cand_pattern_value)
        identity_evidence["comparisons"]["coat_pattern"] = {
            "profile": (prof_pattern_value or None),
            "candidate": cand_pattern_value,
            "profile_confidence": prof_pattern_conf,
            "profile_support_reference_count": prof_pattern_support,
            "profile_families": list(_pattern_families(prof_pattern_tokens)) if prof_pattern_tokens else None,
            "candidate_families": list(_pattern_families(cand_pattern_tokens)) if cand_pattern_tokens else None,
            "relation": pattern_relation,
        }

        if pattern_relation == "unknown":
            checks["coat_pattern"] = "unknown"
        elif pattern_relation in ("exact", "mirror"):
            checks["coat_pattern"] = PASS
        elif pattern_relation in ("family", "family_mirror"):
            # 계열은 같지만 이름이 다르다 — 하드페일할 모순은 아니지만 자동 승인도
            # 아니다. white↔cream, golden↔tan 은 사람이 확인한다.
            checks["coat_pattern"] = REVIEW
            reasons.append("coat_pattern_family_equivalent_not_exact")
        elif prof_pattern_conf == "high" and prof_pattern_support >= 2:
            # 고신뢰 + 레퍼런스 2장 이상이 지지하는 **계열 수준** 모순만 하드페일.
            checks["coat_pattern"] = FAIL
            reasons.append("coat_pattern_strong_mismatch")
        else:
            # 단일 레퍼런스이거나 신뢰도가 낮으면 사람 판단으로 보낸다.
            checks["coat_pattern"] = REVIEW
            reasons.append(
                "coat_pattern_mismatch_single_reference"
                if prof_pattern_support <= 1
                else "coat_pattern_mismatch"
            )

        prof_embed = prof_visual.get("visual_embedding") or {}
        cand_embed = compute_visual_embedding(cutout_rgba)
        identity_evidence["candidate"]["visual_embedding"] = cand_embed
        emb_sim = (
            embedding_similarity(cand_embed or {}, prof_embed if isinstance(prof_embed, dict) else {})
            if cand_embed is not None
            else {"comparable": False}
        )
        prof_embed_support = _support_count(prof_embed)
        if emb_sim.get("comparable"):
            emb = float(emb_sim.get("cosine_similarity") or 0.0)
            emb_min = max(_embedding_threshold(), gate_emb_min)
            identity_evidence["comparisons"]["visual_embedding"] = {
                "cosine_similarity": round(emb, 4),
                "required_min": round(emb_min, 4),
                "profile_support_reference_count": prof_embed_support,
            }
            if emb >= emb_min:
                checks["visual_embedding"] = PASS
            elif emb < max(0.55, emb_min - 0.18):
                # 임베딩은 보조 신호다. 레퍼런스 1장짜리 프로필의 임베딩만으로는
                # 하드페일시키지 않는다 — 다른 증거를 덮어쓰면 안 된다.
                if prof_embed_support <= 1:
                    checks["visual_embedding"] = REVIEW
                    reasons.append(
                        f"embedding_similarity {round(emb, 4)} too_low_single_reference"
                    )
                else:
                    checks["visual_embedding"] = FAIL
                    reasons.append(f"embedding_similarity {round(emb, 4)} too_low")
            else:
                checks["visual_embedding"] = REVIEW
                reasons.append(f"embedding_similarity {round(emb, 4)} borderline")
        else:
            checks["visual_embedding"] = "unknown"
            reasons.append("embedding_not_comparable")

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
        checks.setdefault("coat_pattern", "unknown")
        checks.setdefault("visual_embedding", "unknown")
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
    decision = decide(checks)
    if decision == PASS:
        advisory_not_pass = [k for k in ADVISORY_CHECKS if checks.get(k) not in (PASS, None)]
        if advisory_not_pass:
            # 검사값(REVIEW)과 기존 이유는 그대로 두고, 승인이 자문 검사 위에서
            # 났다는 사실만 한 줄 남긴다 — coat_pattern 을 PASS 로 고쳐 쓰지 않는다.
            reasons.append("advisory_checks_not_blocking:" + ",".join(advisory_not_pass))

    business_signals, business_identity_evidence = _canonical_business_identity_signals(
        vlm_qa,
        profile_visual if isinstance(profile_visual, dict) else {},
        reference_evidence_count=len(reference_signatures),
    )
    business_signals["canonical_cutout_integrity"] = (
        FAIL if cutout_rgba is None or checks.get("cutout") == FAIL else PASS
    )
    identity_evidence["business_identity"] = business_identity_evidence

    return {
        "qa_version": CANONICAL_QA_VERSION,
        "identity_similarity": identity_similarity,
        "checks": checks,
        "reasons": reasons,
        "identity_evidence": identity_evidence,
        "decision": decision,
        "vlm": _vlm_evidence(vlm_qa),
        "business_signals": business_signals,
    }
