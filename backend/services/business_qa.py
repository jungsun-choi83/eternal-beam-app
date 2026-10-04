"""Business QA authority and customer-action contract.

The image/video analyzers still own their measurements and legacy
PASS/REVIEW/FAIL decisions.  This module is the single place where those
measurements acquire spending and delivery authority.

Phase 1 deliberately stores the receipt inside the existing ``qa_result``
JSON.  That keeps the legacy evidence intact and avoids a schema cut-over
while the individual analyzers migrate in later phases.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from enum import Enum
from typing import Any, Mapping, Optional


BUSINESS_QA_VERSION = "business-v1"
CANONICAL_IDENTITY_AUTHORITY_VERSION = "canonical-identity-v2"
KEYFRAME_IDENTITY_AUTHORITY_VERSION = "keyframe-identity-v1"
MOTION_CLASS_AUTHORITY_VERSION = "motion-class-v1"
# breathing-v2: whole-body sway/drift/settling is QUALITY_ADVISORY; only the
# catastrophic scale-pulse signal keeps INTEGRITY_HARD authority. Rollback
# without a code revert: BREATHING_AUTHORITY_PROFILE=breathing-v1 (read once
# at process start, when the motion_spec registry is built).
BREATHING_AUTHORITY_V1 = "breathing-v1"
BREATHING_AUTHORITY_V2 = "breathing-v2"


def breathing_authority_version() -> str:
    value = os.getenv("BREATHING_AUTHORITY_PROFILE", "").strip().lower()
    return BREATHING_AUTHORITY_V1 if value == BREATHING_AUTHORITY_V1 else BREATHING_AUTHORITY_V2


BREATHING_AUTHORITY_VERSION = breathing_authority_version()

# ── Legacy QA authority retirement (BREATHING motion stage only) ────────────
# "legacy"  : previous behavior — legacy PASS/REVIEW/FAIL can still decide where
#             no receipt exists (and via the severity gate / REVIEW branches).
# "business": the business-v1 receipt is the only authority. The legacy decision
#             is telemetry; no receipt -> safe default (block, then fallback).
# Read once at process start. Each generation run is stamped with the mode at
# creation (provider_state._business_qa_cutover.breathing_qa_authority) and
# keeps it for its whole life, so in-flight and old runs never switch mode.
QA_AUTHORITY_LEGACY = "legacy"
QA_AUTHORITY_BUSINESS = "business"
QA_AUTHORITY_STAMP_KEY = "breathing_qa_authority"
RETIRED_LEGACY_AUTHORITY_MOTION = "BREATHING"
#: Direct (no-run) publication only: legacy-PASS candidates without a receipt
#: created before this instant stay publishable. It is the creation time of the
#: first stored candidate carrying a business-v1 receipt.
LEGACY_PASS_CUTOVER_AT = "2026-10-02T15:52:00+00:00"


def breathing_qa_authority() -> str:
    value = os.getenv("BREATHING_QA_AUTHORITY", "").strip().lower()
    return QA_AUTHORITY_BUSINESS if value == QA_AUTHORITY_BUSINESS else QA_AUTHORITY_LEGACY


_PROCESS_QA_AUTHORITY = breathing_qa_authority()
_qa_authority_scope: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "breathing_qa_authority_scope", default=None
)


def current_qa_authority() -> str:
    """Mode in effect: the enclosing run's stamp, else the process flag."""

    scoped = _qa_authority_scope.get()
    return scoped if scoped in (QA_AUTHORITY_LEGACY, QA_AUTHORITY_BUSINESS) else _PROCESS_QA_AUTHORITY


@contextlib.contextmanager
def qa_authority_scope(mode: Optional[str]):
    """Run the block under one run's stamped mode (unstamped runs are legacy)."""

    resolved = QA_AUTHORITY_BUSINESS if mode == QA_AUTHORITY_BUSINESS else QA_AUTHORITY_LEGACY
    token = _qa_authority_scope.set(resolved)
    try:
        yield resolved
    finally:
        _qa_authority_scope.reset(token)


def run_qa_authority(provider_state: Optional[Mapping[str, Any]]) -> str:
    """The mode stamped on a run at creation. Unstamped (older) runs are legacy."""

    cutover = (provider_state or {}).get("_business_qa_cutover") or {}
    value = cutover.get(QA_AUTHORITY_STAMP_KEY) if isinstance(cutover, Mapping) else None
    return QA_AUTHORITY_BUSINESS if value == QA_AUTHORITY_BUSINESS else QA_AUTHORITY_LEGACY


def legacy_authority_retired(motion_id: Any) -> bool:
    """True when the receipt is the only authority for this motion right now."""

    return (
        str(motion_id or "").strip().upper() == RETIRED_LEGACY_AUTHORITY_MOTION
        and current_qa_authority() == QA_AUTHORITY_BUSINESS
    )
MOTION_REQUEST_KINDS = frozenset({"MICRO", "TRANSITION", "LOCOMOTION", "INTERACTION"})


class AuthorityClass(str, Enum):
    INTEGRITY_HARD = "INTEGRITY_HARD"
    IDENTITY_SUPPORT = "IDENTITY_SUPPORT"
    QUALITY_ADVISORY = "QUALITY_ADVISORY"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"


class DeliveryAction(str, Enum):
    DELIVER = "DELIVER"
    DELIVER_WITH_ADVISORY = "DELIVER_WITH_ADVISORY"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"


class RetryAction(str, Enum):
    STOP = "STOP"
    VLM_REVIEW = "VLM_REVIEW"
    REGENERATE = "REGENERATE"
    FALLBACK = "FALLBACK"


DELIVERED_GENERATED = "DELIVERED_GENERATED"
DELIVERED_FALLBACK = "DELIVERED_FALLBACK"
TRUE_INFRASTRUCTURE_FAILURE = "TRUE_INFRASTRUCTURE_FAILURE"

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"
UNKNOWN = "unknown"

# Customer automatic generation budget. Provider error recovery and explicit
# operator replacement requests are separate workflows and do not change this
# number.
MAX_AUTOMATIC_PAID_CANDIDATES = 2
BEST_AVAILABLE_POLICY_VERSION = "best-available-v1"

_STATUS_PRIORITY = {PASS: 0, REVIEW: 1, UNKNOWN: 1, FAIL: 2}
_LEGACY_DECISION_PRIORITY = {PASS: 0, REVIEW: 1, FAIL: 2, "ERROR": 3}


# Central authority registry.  A check not listed here is fail-closed only when
# it explicitly says FAIL; non-failing unknown future evidence is advisory.
# This avoids scattered reason-string spending rules in stage services.
CHECK_AUTHORITIES: dict[str, AuthorityClass] = {
    # Confirmed integrity contradictions.
    "vlm_same_pet": AuthorityClass.INTEGRITY_HARD,
    "vlm_anatomy": AuthorityClass.INTEGRITY_HARD,
    "vlm_composition": AuthorityClass.INTEGRITY_HARD,
    "vlm_motion": AuthorityClass.INTEGRITY_HARD,
    "vlm_target_pose": AuthorityClass.INTEGRITY_HARD,
    "pose": AuthorityClass.INTEGRITY_HARD,
    "starts_at_start_pose": AuthorityClass.INTEGRITY_HARD,
    "reaches_target_pose": AuthorityClass.INTEGRITY_HARD,
    "temporal_stability": AuthorityClass.INTEGRITY_HARD,
    # Phase 1 preserves BREATHING authority; its measurement changes later.
    "temporal_breathing": AuthorityClass.INTEGRITY_HARD,
    "structural_morphology_consistency": AuthorityClass.INTEGRITY_HARD,
    "anatomy_limb_count_placement": AuthorityClass.INTEGRITY_HARD,
    "anatomy_joint_plausibility": AuthorityClass.INTEGRITY_HARD,
    "anatomy_body_deformation": AuthorityClass.INTEGRITY_HARD,
    "output_aspect_ratio": AuthorityClass.INTEGRITY_HARD,
    "output_audio_disabled": AuthorityClass.INTEGRITY_HARD,
    "output_probe": AuthorityClass.INTEGRITY_HARD,
    # Canonical-v5 semantic identity contradictions. These checks are emitted
    # only after confidence/evidence gating in canonical_qa; weak or missing
    # evidence is REVIEW/unknown rather than FAIL.
    "canonical_face_head_identity": AuthorityClass.INTEGRITY_HARD,
    "canonical_ear_muzzle_identity": AuthorityClass.INTEGRITY_HARD,
    "canonical_distinctive_markings_identity": AuthorityClass.INTEGRITY_HARD,
    "canonical_persistent_morphology_identity": AuthorityClass.INTEGRITY_HARD,
    "canonical_cutout_integrity": AuthorityClass.INTEGRITY_HARD,
    # Keyframe-vNext semantic checks.  The legacy ``pose`` field combines a
    # true requested-pose contradiction with visibility uncertainty, so its
    # Keyframe authority is replaced contextually below by these separated
    # signals.
    "keyframe_face_head_identity": AuthorityClass.INTEGRITY_HARD,
    "keyframe_ear_muzzle_identity": AuthorityClass.INTEGRITY_HARD,
    "keyframe_distinctive_markings_identity": AuthorityClass.INTEGRITY_HARD,
    "keyframe_persistent_morphology_identity": AuthorityClass.INTEGRITY_HARD,
    "keyframe_cutout_integrity": AuthorityClass.INTEGRITY_HARD,
    "keyframe_pose_integrity": AuthorityClass.INTEGRITY_HARD,
    "motion_temporal_integrity": AuthorityClass.INTEGRITY_HARD,
    "motion_structural_integrity": AuthorityClass.INTEGRITY_HARD,
    "vlm_locomotion_form": AuthorityClass.INTEGRITY_HARD,
    "vlm_direction_travel": AuthorityClass.INTEGRITY_HARD,
    "vlm_interaction": AuthorityClass.INTEGRITY_HARD,
    "vlm_human_hand_policy": AuthorityClass.INTEGRITY_HARD,
    # Central default stays fail-closed for breathing-v1 receipts; the
    # breathing-v2 registry contract declares it QUALITY_ADVISORY.
    "breathing_global_motion_integrity": AuthorityClass.INTEGRITY_HARD,
    "breathing_catastrophic_motion_integrity": AuthorityClass.INTEGRITY_HARD,
    "breathing_composition_integrity": AuthorityClass.INTEGRITY_HARD,

    # Identity evidence that cannot spend again by itself.
    "identity_similarity": AuthorityClass.IDENTITY_SUPPORT,
    "identity_over_time": AuthorityClass.IDENTITY_SUPPORT,
    "coat_colors": AuthorityClass.IDENTITY_SUPPORT,
    "coat_pattern": AuthorityClass.IDENTITY_SUPPORT,
    "visual_embedding": AuthorityClass.IDENTITY_SUPPORT,
    "structure": AuthorityClass.IDENTITY_SUPPORT,

    # Presentation/quality evidence.
    "cutout": AuthorityClass.QUALITY_ADVISORY,
    "loop_return": AuthorityClass.QUALITY_ADVISORY,
    "output_resolution": AuthorityClass.QUALITY_ADVISORY,
    "output_duration": AuthorityClass.QUALITY_ADVISORY,
    "keyframe_visibility": AuthorityClass.QUALITY_ADVISORY,
    "breathing_motion_correctness": AuthorityClass.QUALITY_ADVISORY,
    "breathing_periodicity": AuthorityClass.QUALITY_ADVISORY,
    "breathing_modulation": AuthorityClass.QUALITY_ADVISORY,
    "breathing_head_motion": AuthorityClass.QUALITY_ADVISORY,
}


def _authority_profile(request_kind: str) -> str:
    kind = str(request_kind or "").upper()
    if kind == "CANONICAL":
        return CANONICAL_IDENTITY_AUTHORITY_VERSION
    if kind == "KEYFRAME":
        return KEYFRAME_IDENTITY_AUTHORITY_VERSION
    if kind in MOTION_REQUEST_KINDS:
        return f"{MOTION_CLASS_AUTHORITY_VERSION}:{kind.lower()}"
    return "phase1-default"


def authority_for_check(name: str, status: str) -> AuthorityClass:
    """Resolve one check through the central registry.

    Empty cutouts are unusable/corrupt and therefore integrity failures, while
    cutout resolution/occupancy reviews remain presentation advice.
    Unregistered explicit FAIL values remain fail-closed until intentionally
    classified; other unregistered evidence is analytics-only.
    """

    if name == "cutout" and str(status).upper() == FAIL:
        return AuthorityClass.INTEGRITY_HARD
    if name == "vlm_composition" and str(status).upper() != FAIL:
        return AuthorityClass.QUALITY_ADVISORY
    known = CHECK_AUTHORITIES.get(name)
    if known is not None:
        return known
    if str(status).upper() == FAIL:
        return AuthorityClass.INTEGRITY_HARD
    return AuthorityClass.DIAGNOSTIC_ONLY


def automatic_candidate_budget(request_kind: str) -> dict[str, Any]:
    """Return the business budget independently from provider routing limits."""

    return {
        "version": BUSINESS_QA_VERSION,
        "authority_profile": _authority_profile(request_kind),
        "request_kind": str(request_kind or "UNKNOWN"),
        "max_paid_candidates": MAX_AUTOMATIC_PAID_CANDIDATES,
        "candidate_1": "ALWAYS",
        "candidate_2": "INTEGRITY_HARD_FAILURE_ONLY",
        "after_candidate_2_integrity_failure": RetryAction.FALLBACK.value,
    }


def _status(value: Any) -> str:
    text = str(value or UNKNOWN)
    upper = text.upper()
    return upper if upper in (PASS, REVIEW, FAIL) else UNKNOWN


def _authority(value: AuthorityClass | str) -> AuthorityClass:
    if isinstance(value, AuthorityClass):
        return value
    return AuthorityClass(str(value))


def _worst(values: list[str]) -> str:
    if FAIL in values:
        return FAIL
    if REVIEW in values or UNKNOWN in values:
        return REVIEW
    return PASS


def decide_candidate_action(
    *,
    qa_checks: Mapping[str, Any],
    authority_by_check: Optional[Mapping[str, AuthorityClass | str]] = None,
    integrity_status: Optional[str] = None,
    quality_status: Optional[str] = None,
    attempt_number: int,
    request_kind: str,
    fallback_available: bool,
) -> dict[str, str]:
    """Authoritative delivery/spending decision for one generated candidate."""

    authorities = authority_by_check or {
        name: authority_for_check(name, _status(value))
        for name, value in qa_checks.items()
    }
    if integrity_status is None:
        hard = [
            _status(value)
            for name, value in qa_checks.items()
            if _authority(authorities[name]) == AuthorityClass.INTEGRITY_HARD
        ]
        integrity_status = _worst(hard) if hard else PASS
    else:
        integrity_status = _status(integrity_status)

    if quality_status is None:
        soft = [
            _status(value)
            for name, value in qa_checks.items()
            if _authority(authorities[name])
            in (AuthorityClass.IDENTITY_SUPPORT, AuthorityClass.QUALITY_ADVISORY)
        ]
        quality_status = _worst(soft) if soft else PASS
    else:
        quality_status = _status(quality_status)

    attempt = max(1, int(attempt_number or 1))
    if integrity_status == FAIL:
        if attempt < MAX_AUTOMATIC_PAID_CANDIDATES:
            return {
                "delivery_action": DeliveryAction.BLOCK.value,
                "retry_action": RetryAction.REGENERATE.value,
            }
        if fallback_available:
            return {
                "delivery_action": DeliveryAction.BLOCK.value,
                "retry_action": RetryAction.FALLBACK.value,
            }
        return {
            "delivery_action": DeliveryAction.ESCALATE.value,
            "retry_action": RetryAction.STOP.value,
        }

    if integrity_status == REVIEW or quality_status != PASS:
        return {
            "delivery_action": DeliveryAction.DELIVER_WITH_ADVISORY.value,
            "retry_action": RetryAction.STOP.value,
        }
    return {
        "delivery_action": DeliveryAction.DELIVER.value,
        "retry_action": RetryAction.STOP.value,
    }


def _flatten_checks(qa_result: Mapping[str, Any], *, request_kind: str) -> dict[str, str]:
    checks = {
        str(name): _status(value)
        for name, value in dict(qa_result.get("checks") or {}).items()
    }
    conformance = qa_result.get("output_conformance") or {}
    for name, value in dict(conformance.get("checks") or {}).items():
        checks[f"output_{name}"] = _status(value)
    # Stage migrations add semantic business signals beside legacy checks.
    # Keeping them out of qa_result["checks"] preserves the historical
    # PASS/REVIEW/FAIL calculation while making their authority stage-specific.
    if str(request_kind or "").upper() in (
        "CANONICAL",
        "KEYFRAME",
        *MOTION_REQUEST_KINDS,
    ):
        for name, value in dict(qa_result.get("business_signals") or {}).items():
            checks[str(name)] = _status(value)
    return checks


def _canonical_authority_overrides(
    qa_result: Mapping[str, Any],
    checks: Mapping[str, str],
    authorities: dict[str, AuthorityClass],
) -> None:
    """Apply Canonical-v5 authority without changing legacy measurements."""

    # Both are color/pixel-statistic descriptors, not individual-pet models.
    # They remain persisted in full but have no delivery or retry authority.
    for name in ("identity_similarity", "visual_embedding"):
        if name in checks:
            authorities[name] = AuthorityClass.DIAGNOSTIC_ONLY

    # Exact coat color and bbox geometry remain useful supporting evidence, but
    # cannot independently become an integrity contradiction.
    for name in ("coat_colors", "coat_pattern", "structure"):
        if name in checks:
            authorities[name] = AuthorityClass.IDENTITY_SUPPORT

    vlm = qa_result.get("vlm") or {}
    confidence = str(vlm.get("same_pet_confidence") or UNKNOWN).lower()
    presentation_only = str(vlm.get("presentation_difference_only") or "no").lower() == "yes"
    # A low/unknown-confidence VLM disagreement is uncertainty, not a confirmed
    # wrong individual. The raw FAIL remains visible in legacy evidence.
    if checks.get("vlm_same_pet") == FAIL and (confidence != "high" or presentation_only):
        authorities["vlm_same_pet"] = AuthorityClass.IDENTITY_SUPPORT


def _keyframe_authority_overrides(
    qa_result: Mapping[str, Any],
    checks: Mapping[str, str],
    authorities: dict[str, AuthorityClass],
) -> None:
    """Apply Keyframe-vNext authority without changing legacy QA evidence."""

    # These compare photography/pixel statistics rather than continuity from
    # the approved Canonical.  They remain persisted for shadow analysis only.
    for name in ("identity_similarity", "visual_embedding"):
        if name in checks:
            authorities[name] = AuthorityClass.DIAGNOSTIC_ONLY

    # Exact color/exposure-sensitive presentation is advisory.  Pattern and
    # bbox morphology remain identity support, but cannot independently spend
    # another attempt or block delivery.
    if "coat_colors" in checks:
        authorities["coat_colors"] = AuthorityClass.QUALITY_ADVISORY
    for name in ("coat_pattern", "structure"):
        if name in checks:
            authorities[name] = AuthorityClass.IDENTITY_SUPPORT

    # Legacy Keyframe pose conflates requested-pose correctness, orientation,
    # and required-region visibility.  The separated keyframe_* signals own
    # business authority; the old value stays available for comparison.
    if "pose" in checks and "keyframe_pose_integrity" in checks:
        authorities["pose"] = AuthorityClass.DIAGNOSTIC_ONLY

    vlm = qa_result.get("vlm") or {}
    confidence = str(vlm.get("same_pet_confidence") or UNKNOWN).lower()
    presentation_only = str(vlm.get("presentation_difference_only") or "no").lower() == "yes"
    if checks.get("vlm_same_pet") == FAIL and (confidence != "high" or presentation_only):
        authorities["vlm_same_pet"] = AuthorityClass.IDENTITY_SUPPORT


def _motion_authority_overrides(
    qa_result: Mapping[str, Any],
    checks: Mapping[str, str],
    authorities: dict[str, AuthorityClass],
) -> None:
    """Apply the authority mapping persisted from the motion_spec registry."""

    contract = qa_result.get("motion_business_contract") or {}
    if not isinstance(contract, Mapping):
        return
    declared = contract.get("check_authority") or {}
    if not isinstance(declared, Mapping):
        return
    for name, value in declared.items():
        key = str(name)
        if key not in checks:
            continue
        try:
            authorities[key] = AuthorityClass(str(value))
        except ValueError:
            # Registry validation catches current bad values. Historical or
            # externally malformed receipts retain the central fail-closed map.
            continue


def build_business_result(
    qa_result: Mapping[str, Any],
    *,
    attempt_number: int,
    request_kind: str,
    fallback_available: bool = True,
) -> dict[str, Any]:
    """Normalize legacy evidence without modifying its legacy decision."""

    checks = _flatten_checks(qa_result, request_kind=request_kind)
    authorities = {
        name: authority_for_check(name, status)
        for name, status in checks.items()
    }
    kind = str(request_kind or "").upper()
    if kind == "CANONICAL":
        _canonical_authority_overrides(qa_result, checks, authorities)
    elif kind == "KEYFRAME":
        _keyframe_authority_overrides(qa_result, checks, authorities)
    elif kind in MOTION_REQUEST_KINDS:
        _motion_authority_overrides(qa_result, checks, authorities)
    escalation = qa_result.get("vlm_escalation") or {}
    if (
        isinstance(escalation, Mapping)
        and escalation.get("decision") == "SKIP"
        and escalation.get("sufficient_without_vlm") is True
    ):
        # Preserve legacy unknown checks, but do not let a deliberately skipped
        # VLM manufacture an integrity REVIEW after deterministic evidence was
        # already sufficient. The centralized escalation receipt owns the
        # exact list; stage services do not scatter prefix checks.
        for name in escalation.get("non_authoritative_checks_when_skipped") or []:
            key = str(name)
            if key in authorities and checks.get(key) != FAIL:
                authorities[key] = AuthorityClass.DIAGNOSTIC_ONLY
    if isinstance(escalation, Mapping) and escalation.get("decision") == "CALL":
        # Targeted VLM intentionally does not answer unrelated questions. The
        # legacy aggregators still expose those missing fields as unknown for
        # shadow testing; they have no delivery/retry authority for this run.
        for name in escalation.get("non_authoritative_checks_when_unrequested") or []:
            key = str(name)
            if key in authorities and checks.get(key) != FAIL:
                authorities[key] = AuthorityClass.DIAGNOSTIC_ONLY
    legacy_reasons = [str(reason) for reason in (qa_result.get("reasons") or [])]
    # The legacy motion QA folds two different meanings into vlm_composition:
    # contamination/duplication/scene-cut (integrity) and MICRO motion size
    # presentation (quality). Keep that distinction centralized here.
    if (
        checks.get("vlm_composition") == FAIL
        and any(reason.startswith("vlm_unintended_large_motion") for reason in legacy_reasons)
        and not any(reason == "vlm_composition_contaminated" for reason in legacy_reasons)
    ):
        authorities["vlm_composition"] = AuthorityClass.QUALITY_ADVISORY
    hard_values = [
        status
        for name, status in checks.items()
        if authorities[name] == AuthorityClass.INTEGRITY_HARD
    ]
    quality_values = [
        status
        for name, status in checks.items()
        if authorities[name]
        in (AuthorityClass.IDENTITY_SUPPORT, AuthorityClass.QUALITY_ADVISORY)
    ]
    integrity_status = _worst(hard_values) if hard_values else PASS
    quality_status = _worst(quality_values) if quality_values else PASS
    action = decide_candidate_action(
        qa_checks=checks,
        authority_by_check=authorities,
        integrity_status=integrity_status,
        quality_status=quality_status,
        attempt_number=attempt_number,
        request_kind=request_kind,
        fallback_available=fallback_available,
    )

    evidence: dict[str, dict[str, str]] = {
        authority.value: {} for authority in AuthorityClass
    }
    reasons: list[str] = []
    for name, status in checks.items():
        authority = authorities[name]
        evidence[authority.value][name] = status
        if status != PASS:
            reasons.append(f"{authority.value.lower()}:{name}={status}")

    terminal_state = None
    if action["delivery_action"] in (
        DeliveryAction.DELIVER.value,
        DeliveryAction.DELIVER_WITH_ADVISORY.value,
    ):
        terminal_state = DELIVERED_GENERATED
    elif action["retry_action"] == RetryAction.FALLBACK.value:
        terminal_state = DELIVERED_FALLBACK

    authority_profile = _authority_profile(request_kind)
    motion_contract = qa_result.get("motion_business_contract") or {}
    if kind in MOTION_REQUEST_KINDS and isinstance(motion_contract, Mapping):
        declared_profile = motion_contract.get("authority_profile")
        if isinstance(declared_profile, str) and declared_profile:
            authority_profile = declared_profile

    return {
        "version": BUSINESS_QA_VERSION,
        "authority_profile": authority_profile,
        "integrity_status": integrity_status,
        "quality_status": quality_status,
        **action,
        "reasons": reasons,
        "authority_evidence": evidence,
        "legacy_decision": qa_result.get("decision"),
        "attempt_number": max(1, int(attempt_number or 1)),
        "request_kind": str(request_kind or "UNKNOWN"),
        "fallback_available": bool(fallback_available),
        "terminal_state": terminal_state,
    }


def attach_business_result(
    qa_result: dict[str, Any],
    *,
    attempt_number: int,
    request_kind: str,
    fallback_available: bool = True,
) -> dict[str, Any]:
    qa_result["business_qa"] = build_business_result(
        qa_result,
        attempt_number=attempt_number,
        request_kind=request_kind,
        fallback_available=fallback_available,
    )
    return qa_result


def receipt(qa_result: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    value = (qa_result or {}).get("business_qa")
    if not isinstance(value, dict) or value.get("version") != BUSINESS_QA_VERSION:
        return None
    return value


def is_deliverable(
    qa_result: Optional[Mapping[str, Any]], *, legacy_fallback: bool = True
) -> bool:
    current = receipt(qa_result)
    if current is not None:
        return current.get("delivery_action") in (
            DeliveryAction.DELIVER.value,
            DeliveryAction.DELIVER_WITH_ADVISORY.value,
        ) and (legacy_fallback or current.get("integrity_status") != FAIL)
    if not legacy_fallback:
        # Legacy authority retired: no receipt is never deliverable.
        return False
    # Historical rows have no business receipt. Preserve their old contract.
    return str((qa_result or {}).get("decision") or "").upper() == PASS


SAFE_DEFAULT_REASON_MISSING = "business_qa_receipt_missing"
SAFE_DEFAULT_REASON_ERROR = "business_qa_error"


def safe_default_receipt(
    *, reason: str, request_kind: str, attempt_number: int = 1, detail: Optional[str] = None
) -> dict[str, Any]:
    """Receipt used when Business QA produced no decision.

    Block the generated candidate, spend nothing further, and resolve through
    the still/fallback ladder. It never consults the legacy decision.
    """

    return {
        "version": BUSINESS_QA_VERSION,
        "authority_profile": "safe-default-v1",
        "integrity_status": REVIEW,
        "quality_status": REVIEW,
        "delivery_action": DeliveryAction.BLOCK.value,
        "retry_action": RetryAction.FALLBACK.value,
        "reasons": [f"safe_default:{reason}"],
        "authority_evidence": {authority.value: {} for authority in AuthorityClass},
        "legacy_decision": None,
        "attempt_number": max(1, int(attempt_number or 1)),
        "request_kind": str(request_kind or "UNKNOWN"),
        "fallback_available": True,
        "terminal_state": DELIVERED_FALLBACK,
        "safe_default": True,
        "safe_default_reason": reason,
        **({"safe_default_detail": str(detail)[:300]} if detail else {}),
    }


def customer_qa_decision(
    qa_result: Optional[Mapping[str, Any]], legacy_decision: Optional[str]
) -> Optional[str]:
    """Customer-facing QA label for a delivered asset.

    A business-v1 receipt that authorizes delivery (and has no integrity
    failure) reports PASS; the advisory flag and the stored legacy decision stay
    internal. Without such a receipt the legacy value is returned unchanged.
    """

    current = receipt(qa_result)
    if (
        current is not None
        and current.get("delivery_action")
        in (DeliveryAction.DELIVER.value, DeliveryAction.DELIVER_WITH_ADVISORY.value)
        and current.get("integrity_status") != FAIL
    ):
        return PASS
    return (str(legacy_decision) if legacy_decision else None)


def should_regenerate(qa_result: Optional[Mapping[str, Any]]) -> bool:
    current = receipt(qa_result)
    return bool(current and current.get("retry_action") == RetryAction.REGENERATE.value)


def evaluated_candidates(candidates: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        candidate
        for candidate in candidates
        if str(candidate.get("decision") or "").upper() in (PASS, REVIEW, FAIL)
        and isinstance(candidate.get("qa_result"), Mapping)
        and bool(candidate.get("qa_result"))
    ]


def automatic_attempt_number(
    candidates: list[Mapping[str, Any]], *, current_candidate_id: Optional[str] = None
) -> int:
    prior = [
        candidate
        for candidate in evaluated_candidates(candidates)
        if str(candidate.get("id") or "") != str(current_candidate_id or "")
    ]
    return len(prior) + 1


def may_generate_next_candidate(
    candidates: list[Mapping[str, Any]], *, legacy_fallback: bool = True
) -> bool:
    """Whether another automatic paid candidate is authorized.

    Historical candidates without a business-v1 receipt retain their legacy
    behavior. Newly evaluated candidates are governed solely by the receipt.
    With ``legacy_fallback=False`` a missing receipt never buys another
    candidate: only an explicit REGENERATE receipt does.
    """

    evaluated = evaluated_candidates(candidates)
    if not evaluated:
        return True
    if len(evaluated) >= MAX_AUTOMATIC_PAID_CANDIDATES:
        return False
    latest = evaluated[-1]
    current = receipt(latest.get("qa_result"))
    if current is None:
        if not legacy_fallback:
            return False
        return str(latest.get("decision") or "").upper() != PASS
    return current.get("retry_action") == RetryAction.REGENERATE.value


def _candidate_field(candidate: Any, name: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(name, default)
    return getattr(candidate, name, default)


def _selection_dimension(check_name: str) -> str:
    """Map persisted evidence onto the ordered business selection dimensions."""

    name = str(check_name or "").lower()
    if name.startswith("output_") or "cutout" in name or name in {
        "vlm_composition",
        "breathing_composition_integrity",
    } or any(token in name for token in ("scene_cut", "contamination", "human_hand")):
        return "technical_validity"
    if any(
        token in name
        for token in (
            "same_pet",
            "identity",
            "face_head",
            "ear_muzzle",
            "marking",
            "coat_pattern",
            "single_pet",
            "duplicated_pet",
        )
    ):
        return "identity_integrity"
    if any(
        token in name
        for token in ("anatomy", "morphology", "structural", "limb", "joint", "deformation")
    ):
        return "anatomy_integrity"
    if any(
        token in name
        for token in (
            "motion",
            "pose",
            "temporal",
            "loop",
            "direction",
            "locomotion",
            "interaction",
            "breathing",
            "gait",
        )
    ):
        return "motion_correctness"
    return "presentation_quality"


def candidate_selection_priority(candidate: Any, *, legacy_tiebreak: bool = True) -> dict[str, Any]:
    """Return the deterministic best-available ranking receipt for one candidate.

    Ranking is lexicographic in the product order: identity, anatomy, motion,
    technical validity, then presentation. This function does not decide
    deliverability; it only ranks candidates already authorized by business-v1.
    """

    qa_result = _candidate_field(candidate, "qa_result", {}) or {}
    current = receipt(qa_result)
    evidence = (current or {}).get("authority_evidence") or {}
    dimensions: dict[str, list[str]] = {
        "identity_integrity": [],
        "anatomy_integrity": [],
        "motion_correctness": [],
        "technical_validity": [],
        "presentation_quality": [],
    }
    for checks in evidence.values():
        if not isinstance(checks, Mapping):
            continue
        for name, status in checks.items():
            dimensions[_selection_dimension(str(name))].append(_status(status))

    def dimension_receipt(name: str) -> dict[str, Any]:
        statuses = dimensions[name]
        status = _worst(statuses) if statuses else PASS
        return {
            "status": status,
            "non_pass_count": sum(1 for value in statuses if value != PASS),
        }

    ordered_names = (
        "identity_integrity",
        "anatomy_integrity",
        "motion_correctness",
        "technical_validity",
        "presentation_quality",
    )
    ordered = {name: dimension_receipt(name) for name in ordered_names}
    sort_key: list[Any] = []
    for name in ordered_names:
        item = ordered[name]
        sort_key.extend((_STATUS_PRIORITY[item["status"]], item["non_pass_count"]))

    legacy_decision = str(_candidate_field(candidate, "decision", "ERROR") or "ERROR").upper()
    similarity = qa_result.get("identity_similarity")
    identity_similarity = float(similarity) if isinstance(similarity, (int, float)) else -1.0
    attempt = int(_candidate_field(candidate, "attempt", 1) or 1)
    if legacy_tiebreak:
        sort_key.extend(
            (
                _LEGACY_DECISION_PRIORITY.get(legacy_decision, 9),
                -identity_similarity,
                attempt,
                str(_candidate_field(candidate, "id", "") or ""),
            )
        )
    else:
        # Legacy authority retired: the legacy decision and the pixel-statistic
        # similarity are reported below but do not order candidates.
        sort_key.extend((attempt, str(_candidate_field(candidate, "id", "") or "")))
    return {
        "version": BEST_AVAILABLE_POLICY_VERSION,
        **ordered,
        "legacy_decision": legacy_decision,
        "identity_similarity": identity_similarity,
        "attempt": attempt,
        "sort_key": sort_key,
    }


def best_available_candidate(
    candidates: list[Any], *, legacy_fallback: bool = True
) -> Optional[Any]:
    """Select the best business-deliverable candidate, including safe REVIEWs."""

    deliverable = [
        candidate
        for candidate in candidates
        if is_deliverable(
            _candidate_field(candidate, "qa_result", {}) or {}, legacy_fallback=legacy_fallback
        )
    ]
    if not deliverable:
        return None
    return min(
        deliverable,
        key=lambda candidate: tuple(
            candidate_selection_priority(candidate, legacy_tiebreak=legacy_fallback)["sort_key"]
        ),
    )


def fallback_receipt(candidates: list[Any]) -> Optional[dict[str, Any]]:
    """Return the latest explicit FALLBACK authorization after budget exhaustion."""

    authorized: list[tuple[int, dict[str, Any]]] = []
    for candidate in candidates:
        current = receipt(_candidate_field(candidate, "qa_result", {}) or {})
        if current and current.get("retry_action") == RetryAction.FALLBACK.value:
            authorized.append((int(current.get("attempt_number") or 0), current))
    return max(authorized, key=lambda item: item[0])[1] if authorized else None
