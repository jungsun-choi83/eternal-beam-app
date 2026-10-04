"""Central Business QA policy for conditional VLM escalation.

Stage analyzers run once without VLM, pass that preserved evidence here, and
only invoke their existing cached VLM function when this policy returns CALL.
The policy does not route providers and never changes generation budgets.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from . import business_qa


VLM_ESCALATION_VERSION = "vlm-escalation-v3"
CALL = "CALL"
SKIP = "SKIP"

IDENTITY_VLM = "IDENTITY_VLM"
ANATOMY_VLM = "ANATOMY_VLM"
POSE_VLM = "POSE_VLM"
MOTION_VLM = "MOTION_VLM"
#: One combined identity+anatomy question for a generated BREATHING video.
IDENTITY_ANATOMY_VLM = "IDENTITY_ANATOMY_VLM"
VLM_TASKS = (IDENTITY_VLM, ANATOMY_VLM, POSE_VLM, IDENTITY_ANATOMY_VLM, MOTION_VLM)
#: Checks answered by a combined task. Kept out of _STAGE_TASK_CHECKS so a
#: full-stage review keeps its separate identity/anatomy calls unchanged.
_COMBINED_TASK_CHECKS = {
    IDENTITY_ANATOMY_VLM: {"vlm_same_pet", "vlm_anatomy", "vlm_composition"},
}

_QUESTION_TASK = {
    "distinctive_markings_identity": IDENTITY_VLM,
    "identity_markings": IDENTITY_VLM,
    "persistent_morphology": IDENTITY_VLM,
    "identity_continuity": IDENTITY_VLM,
    "anatomy": ANATOMY_VLM,
    "required_pose": POSE_VLM,
    "identity_anatomy": IDENTITY_ANATOMY_VLM,
    "motion_correctness": MOTION_VLM,
    "temporal_integrity": MOTION_VLM,
}

_STAGE_TASK_CHECKS = {
    "CANONICAL": {
        IDENTITY_VLM: {
            "vlm_same_pet",
            "canonical_face_head_identity",
            "canonical_ear_muzzle_identity",
            "canonical_distinctive_markings_identity",
            "canonical_persistent_morphology_identity",
        },
        ANATOMY_VLM: {"vlm_anatomy", "vlm_composition"},
    },
    "KEYFRAME": {
        IDENTITY_VLM: {
            "vlm_same_pet",
            "keyframe_face_head_identity",
            "keyframe_ear_muzzle_identity",
            "keyframe_distinctive_markings_identity",
            "keyframe_persistent_morphology_identity",
        },
        ANATOMY_VLM: {"vlm_anatomy", "vlm_composition"},
        POSE_VLM: {"keyframe_pose_integrity", "keyframe_visibility"},
    },
    "MOTION": {
        IDENTITY_VLM: {"vlm_same_pet"},
        ANATOMY_VLM: {"vlm_anatomy", "vlm_composition"},
        MOTION_VLM: {
            "vlm_motion",
            "vlm_target_pose",
            "vlm_locomotion_form",
            "vlm_direction_travel",
            "vlm_interaction",
            "vlm_human_hand_policy",
        },
    },
}


def targeted_tasks(stage: str, questions: Sequence[str]) -> list[str]:
    """Map Phase 8 unresolved questions to the minimum ordered VLM task set."""

    resolved_stage = str(stage or "").strip().upper()
    values = [str(value) for value in questions]
    if "full_stage_review" in values or "qa_resolution" in values:
        return list((_STAGE_TASK_CHECKS.get(resolved_stage) or {}).keys())
    requested = {_QUESTION_TASK.get(value) for value in values}
    return [task for task in VLM_TASKS if task in requested]


def _unrequested_vlm_checks(
    stage: str, tasks: Sequence[str], questions: Sequence[str]
) -> list[str]:
    resolved_stage = str(stage or "").strip().upper()
    task_checks = _STAGE_TASK_CHECKS.get(resolved_stage) or {}
    selected = {str(task) for task in tasks}
    all_checks = {check for checks in task_checks.values() for check in checks}
    if "full_stage_review" in questions or "qa_resolution" in questions:
        answered = {check for task in selected for check in task_checks.get(task, set())}
        return sorted(all_checks - answered)

    answered: set[str] = set()
    for task in selected:
        if task in _COMBINED_TASK_CHECKS:
            answered.update(_COMBINED_TASK_CHECKS[task])
            continue
        if task != IDENTITY_VLM or resolved_stage == "MOTION":
            answered.update(task_checks.get(task, set()))
            continue
        answered.add("vlm_same_pet")
        prefix = "canonical" if resolved_stage == "CANONICAL" else "keyframe"
        if any("marking" in question for question in questions):
            answered.add(f"{prefix}_distinctive_markings_identity")
        if "persistent_morphology" in questions:
            answered.add(f"{prefix}_persistent_morphology_identity")
    return sorted(all_checks - answered)


def _status(value: Any) -> str:
    value = str(value or business_qa.UNKNOWN).upper()
    return value if value in {business_qa.PASS, business_qa.REVIEW, business_qa.FAIL} else business_qa.UNKNOWN


def _is_vlm_derived(check: str) -> bool:
    return (
        check.startswith("vlm_")
        or check.startswith("canonical_face_head_")
        or check.startswith("canonical_ear_muzzle_")
        or check.startswith("canonical_distinctive_markings_")
        or check.startswith("canonical_persistent_morphology_")
        or check.startswith("keyframe_face_head_")
        or check.startswith("keyframe_ear_muzzle_")
        or check.startswith("keyframe_distinctive_markings_")
        or check.startswith("keyframe_persistent_morphology_")
        or check in {"keyframe_pose_integrity", "keyframe_visibility"}
    )


def _receipt(qa_result: Mapping[str, Any], request_kind: str) -> dict[str, Any]:
    return business_qa.build_business_result(
        qa_result,
        attempt_number=1,
        request_kind=request_kind,
        fallback_available=True,
    )


def _base_record(
    *,
    stage: str,
    request_kind: str,
    qa_result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    receipt = _receipt(qa_result, request_kind)
    authority_evidence = receipt.get("authority_evidence") or {}
    hard = dict(
        authority_evidence.get(
            business_qa.AuthorityClass.INTEGRITY_HARD.value
        )
        or {}
    )
    deterministic_hard = {
        name: _status(status)
        for name, status in hard.items()
        if not _is_vlm_derived(str(name))
    }
    non_authoritative = sorted(
        {
            str(name)
            for evidence in authority_evidence.values()
            for name in dict(evidence or {})
            if _is_vlm_derived(str(name))
        }
    )
    record = {
        "version": VLM_ESCALATION_VERSION,
        "stage": stage,
        "request_kind": request_kind,
        "decision": None,
        "called": False,
        "result_available": False,
        "sufficient_without_vlm": False,
        "reason_codes": [],
        "unresolved_questions": [],
        # Business QA reads this exact persisted list. Missing VLM evidence is
        # diagnostic only when this policy says deterministic evidence suffices.
        "non_authoritative_checks_when_skipped": non_authoritative,
        "deterministic_integrity_evidence": deterministic_hard,
    }
    hard_failures = [name for name, status in deterministic_hard.items() if status == business_qa.FAIL]
    return record, deterministic_hard, hard_failures


def _finish(
    record: dict[str, Any],
    *,
    decision: str,
    reasons: list[str],
    questions: Sequence[str] = (),
    sufficient: bool,
) -> dict[str, Any]:
    unique_questions = list(dict.fromkeys(questions))
    tasks = (
        targeted_tasks(str(record.get("stage") or ""), unique_questions)
        if decision == CALL
        else []
    )
    return {
        **record,
        "decision": decision,
        "sufficient_without_vlm": bool(sufficient),
        "reason_codes": list(dict.fromkeys(reasons)),
        "unresolved_questions": unique_questions,
        "requested_tasks": tasks,
        # A narrow call deliberately leaves the other legacy VLM checks unknown.
        # Keep those fields for shadow comparison, but remove their business
        # authority so an unasked question cannot manufacture a REVIEW/BLOCK.
        "non_authoritative_checks_when_unrequested": (
            _unrequested_vlm_checks(
                str(record.get("stage") or ""), tasks, unique_questions
            )
            if decision == CALL
            else []
        ),
    }


def should_call_vlm(
    *,
    stage: str,
    qa_result: Mapping[str, Any],
    request_kind: Optional[str] = None,
    motion_id: Optional[str] = None,
    pose_required: bool = True,
    force_call: bool = False,
    inherited_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return the authoritative CALL/SKIP receipt for one deterministic QA pass."""

    resolved_stage = str(stage or "").strip().upper()
    kind = str(request_kind or resolved_stage or "UNKNOWN").strip().upper()
    record, deterministic_hard, hard_failures = _base_record(
        stage=resolved_stage,
        request_kind=kind,
        qa_result=qa_result,
    )
    record["inherited_evidence_fingerprints"] = [
        str(item.get("fingerprint"))
        for item in inherited_evidence
        if item.get("valid") is True and item.get("fingerprint")
    ]
    record["inherited_domains"] = sorted(
        {
            str(domain)
            for item in inherited_evidence
            if item.get("valid") is True
            for domain in item.get("domains") or []
        }
    )
    if hard_failures:
        return _finish(
            record,
            decision=SKIP,
            reasons=["deterministic_integrity_hard_fail", *[f"hard_fail:{n}" for n in hard_failures]],
            sufficient=True,
        )
    if force_call:
        return _finish(
            record,
            decision=CALL,
            reasons=["explicit_vlm_cache_refresh"],
            questions=["full_stage_review"],
            sufficient=False,
        )

    checks = {str(name): _status(value) for name, value in dict(qa_result.get("checks") or {}).items()}
    reasons = {str(value) for value in (qa_result.get("reasons") or [])}

    if resolved_stage == "CANONICAL":
        questions: list[str] = []
        reason_codes: list[str] = []
        if checks.get("coat_pattern") == business_qa.FAIL:
            questions.append("distinctive_markings_identity")
            reason_codes.append("strong_marking_conflict")
        elif checks.get("coat_pattern") == business_qa.REVIEW and not (
            "coat_pattern_family_equivalent_not_exact" in reasons
            or "coat_pattern_mismatch_single_reference" in reasons
        ):
            questions.append("distinctive_markings_identity")
            reason_codes.append("marking_evidence_uncertain")
        if checks.get("structure") == business_qa.REVIEW:
            questions.extend(["persistent_morphology", "anatomy"])
            reason_codes.append("structure_evidence_uncertain")
        if questions:
            return _finish(
                record,
                decision=CALL,
                reasons=reason_codes,
                questions=questions,
                sufficient=False,
            )
        return _finish(
            record,
            decision=SKIP,
            reasons=["deterministic_canonical_clear"],
            sufficient=True,
        )

    if resolved_stage == "KEYFRAME":
        questions = []
        reason_codes = []
        signals = dict(qa_result.get("business_signals") or {})
        if pose_required and _status(signals.get("keyframe_pose_integrity")) != business_qa.PASS:
            questions.append("required_pose")
            reason_codes.append("pose_evidence_uncertain")
        if checks.get("coat_pattern") == business_qa.FAIL:
            questions.append("identity_markings")
            reason_codes.append("identity_evidence_conflict")
        if checks.get("structure") == business_qa.REVIEW:
            questions.extend(["persistent_morphology", "anatomy"])
            reason_codes.append("structure_evidence_uncertain")
        if questions:
            return _finish(
                record,
                decision=CALL,
                reasons=reason_codes,
                questions=questions,
                sufficient=False,
            )
        return _finish(
            record,
            decision=SKIP,
            reasons=["deterministic_keyframe_clear"],
            sufficient=True,
        )

    if resolved_stage == "MOTION":
        mid = str(motion_id or "").strip().upper()
        temporal = qa_result.get("temporal") or {}
        verdict = str(temporal.get("verdict") or "").lower()
        identity_status = checks.get("identity_over_time", business_qa.UNKNOWN)
        stability_status = checks.get("temporal_stability", business_qa.UNKNOWN)
        is_breathing = mid == "BREATHING"
        breathing_motion_resolved = is_breathing and verdict == "breathing_detected"
        if (
            breathing_motion_resolved
            and identity_status == business_qa.PASS
            and stability_status == business_qa.PASS
            and all(status == business_qa.PASS for status in deterministic_hard.values())
        ):
            # Deterministic evidence settles the motion question, but identity
            # and anatomy of the generated video are still confirmed by one
            # combined call. An unanswered call is REVIEW (deliver with
            # advisory), never a hard failure.
            return _finish(
                record,
                decision=CALL,
                reasons=["breathing_temporal_authority_clear", "breathing_identity_anatomy_required"],
                questions=["identity_anatomy"],
                sufficient=False,
            )

        questions = [] if breathing_motion_resolved else ["motion_correctness"]
        reason_codes = (
            ["breathing_motion_resolved_by_temporal_authority"]
            if breathing_motion_resolved
            else ["motion_requires_semantic_resolution"]
        )
        if is_breathing:
            # Covers identity continuity too — no separate IDENTITY_VLM call.
            questions.append("identity_anatomy")
            reason_codes.append("breathing_identity_anatomy_required")
        elif identity_status != business_qa.PASS:
            questions.append("identity_continuity")
            reason_codes.append("identity_evidence_uncertain")
        if stability_status != business_qa.PASS and not breathing_motion_resolved:
            questions.append("temporal_integrity")
            reason_codes.append("temporal_evidence_uncertain")
        if not questions:
            return _finish(
                record,
                decision=SKIP,
                reasons=reason_codes,
                sufficient=True,
            )
        return _finish(
            record,
            decision=CALL,
            reasons=reason_codes,
            questions=questions,
            sufficient=False,
        )

    return _finish(
        record,
        decision=CALL,
        reasons=["unknown_stage_fail_safe"],
        questions=["qa_resolution"],
        sufficient=False,
    )


def finalize_vlm_escalation(
    decision: Mapping[str, Any],
    *,
    called: bool,
    result: Optional[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Persist actual execution without embedding or rewriting the VLM output.

    ``failures`` are the per-call failure records captured from the VLM client
    (class: api_error / timeout / refusal / parse_failure / sdk_missing).
    """

    requested = [str(task) for task in (decision.get("requested_tasks") or [])]
    # called_tasks keeps its meaning (tasks attempted); answered_tasks are the
    # ones that actually produced a result.
    called_tasks = (
        list((result or {}).get("called_tasks") or requested) if called else []
    )
    answered_tasks = (
        list((result or {}).get("called_tasks") or (requested if result else []))
        if called
        else []
    )
    failure_records = [dict(item) for item in failures] if called else []
    failed_tasks = [task for task in requested if task not in answered_tasks] if called else []
    if failed_tasks and not failure_records:
        # The client returned nothing without reporting why (disabled, no
        # frames, or an injected test double).
        failure_records = [{"failure_class": "no_result"}]
    return {
        **dict(decision),
        "called": bool(called),
        "result_available": bool(result),
        "result_source": (str(result.get("source")) if result and result.get("source") else None),
        "result_model": (str(result.get("model")) if result and result.get("model") else None),
        "called_tasks": called_tasks,
        "answered_tasks": answered_tasks,
        "failed_tasks": failed_tasks,
        "result_failure_class": (
            str(failure_records[0].get("failure_class")) if failure_records else None
        ),
        "result_failures": failure_records,
    }
