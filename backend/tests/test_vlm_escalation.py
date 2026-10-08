from backend.services import business_qa, vlm_escalation


def _canonical(*, checks=None, signals=None, reasons=None):
    return {
        "decision": "REVIEW",
        "checks": {
            "cutout": "PASS",
            "structure": "PASS",
            "coat_pattern": "PASS",
            "coat_colors": "PASS",
            "vlm_same_pet": "unknown",
            "vlm_anatomy": "unknown",
            "vlm_composition": "unknown",
            **(checks or {}),
        },
        "business_signals": {
            "canonical_cutout_integrity": "PASS",
            "canonical_face_head_identity": "unknown",
            "canonical_ear_muzzle_identity": "unknown",
            "canonical_distinctive_markings_identity": "unknown",
            "canonical_persistent_morphology_identity": "unknown",
            **(signals or {}),
        },
        "reasons": list(reasons or []),
    }


def test_clear_canonical_pass_skips_vlm_and_can_deliver():
    qa = _canonical()
    decision = vlm_escalation.should_call_vlm(
        stage="CANONICAL", qa_result=qa, request_kind="CANONICAL"
    )
    assert decision["decision"] == vlm_escalation.SKIP
    assert decision["reason_codes"] == ["deterministic_canonical_clear"]
    qa["vlm_escalation"] = vlm_escalation.finalize_vlm_escalation(
        decision, called=False, result=None
    )
    result = business_qa.attach_business_result(
        qa, attempt_number=1, request_kind="CANONICAL"
    )["business_qa"]
    assert result["integrity_status"] == "PASS"
    assert result["delivery_action"] == "DELIVER"
    assert result["retry_action"] == "STOP"


def test_clear_deterministic_hard_fail_skips_vlm():
    qa = _canonical(
        checks={"cutout": "FAIL"},
        signals={"canonical_cutout_integrity": "FAIL"},
    )
    decision = vlm_escalation.should_call_vlm(
        stage="CANONICAL", qa_result=qa, request_kind="CANONICAL"
    )
    assert decision["decision"] == vlm_escalation.SKIP
    assert "deterministic_integrity_hard_fail" in decision["reason_codes"]


def test_identity_marking_conflict_calls_vlm():
    decision = vlm_escalation.should_call_vlm(
        stage="CANONICAL",
        qa_result=_canonical(checks={"coat_pattern": "FAIL"}),
        request_kind="CANONICAL",
    )
    assert decision["decision"] == vlm_escalation.CALL
    assert "distinctive_markings_identity" in decision["unresolved_questions"]
    assert decision["requested_tasks"] == [vlm_escalation.IDENTITY_VLM]
    assert vlm_escalation.ANATOMY_VLM not in decision["requested_tasks"]
    assert "canonical_face_head_identity" in decision[
        "non_authoritative_checks_when_unrequested"
    ]
    assert "canonical_distinctive_markings_identity" not in decision[
        "non_authoritative_checks_when_unrequested"
    ]


def test_inherited_identity_is_recorded_but_new_asset_conflict_still_escalates():
    inherited = {
        "valid": True,
        "fingerprint": "canonical-evidence",
        "domains": ["identity", "markings"],
    }
    clear = vlm_escalation.should_call_vlm(
        stage="KEYFRAME",
        qa_result=_canonical(
            signals={
                "keyframe_cutout_integrity": "PASS",
                "keyframe_pose_integrity": "PASS",
                "keyframe_visibility": "PASS",
            }
        ),
        request_kind="KEYFRAME",
        inherited_evidence=[inherited],
    )
    assert clear["decision"] == vlm_escalation.SKIP
    assert clear["inherited_domains"] == ["identity", "markings"]

    conflict = vlm_escalation.should_call_vlm(
        stage="KEYFRAME",
        qa_result=_canonical(
            checks={"coat_pattern": "FAIL"},
            signals={
                "keyframe_cutout_integrity": "PASS",
                "keyframe_pose_integrity": "PASS",
                "keyframe_visibility": "PASS",
            },
        ),
        request_kind="KEYFRAME",
        inherited_evidence=[inherited],
    )
    assert conflict["requested_tasks"] == [vlm_escalation.IDENTITY_VLM]


def test_keyframe_pose_uncertainty_calls_vlm():
    qa = _canonical(
        signals={
            "keyframe_cutout_integrity": "PASS",
            "keyframe_pose_integrity": "REVIEW",
            "keyframe_visibility": "REVIEW",
        }
    )
    decision = vlm_escalation.should_call_vlm(
        stage="KEYFRAME", qa_result=qa, request_kind="KEYFRAME", pose_required=True
    )
    assert decision["decision"] == vlm_escalation.CALL
    assert "required_pose" in decision["unresolved_questions"]
    assert decision["requested_tasks"] == [vlm_escalation.POSE_VLM]


def test_breathing_temporal_pass_does_not_require_motion_vlm():
    qa = {
        "decision": "REVIEW",
        "checks": {
            "identity_over_time": "PASS",
            "temporal_stability": "PASS",
            "temporal_breathing": "PASS",
            "vlm_same_pet": "unknown",
            "vlm_anatomy": "unknown",
            "vlm_motion": "PASS",
            "vlm_composition": "unknown",
        },
        "business_signals": {
            "motion_temporal_integrity": "PASS",
            "breathing_motion_correctness": "PASS",
            "breathing_global_motion_integrity": "PASS",
            "breathing_composition_integrity": "PASS",
        },
        "motion_business_contract": {"check_authority": {}},
        "temporal": {"verdict": "breathing_detected"},
        "reasons": ["vlm_qa_unavailable", "vlm_motion_resolved_by_temporal_evidence"],
    }
    decision = vlm_escalation.should_call_vlm(
        stage="MOTION",
        qa_result=qa,
        request_kind="MICRO",
        motion_id="BREATHING",
    )
    # Motion is settled by temporal authority, but identity and anatomy of the
    # generated video are confirmed by one combined call.
    assert decision["decision"] == vlm_escalation.CALL
    assert decision["reason_codes"] == [
        "breathing_temporal_authority_clear",
        "breathing_identity_anatomy_required",
    ]
    assert decision["requested_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    unrequested = decision["non_authoritative_checks_when_unrequested"]
    assert "vlm_motion" in unrequested
    assert not {"vlm_same_pet", "vlm_anatomy", "vlm_composition"} & set(unrequested)


def test_breathing_temporal_pass_with_identity_uncertainty_calls_identity_only():
    qa = {
        "decision": "REVIEW",
        "checks": {
            "identity_over_time": "REVIEW",
            "temporal_stability": "PASS",
            "temporal_breathing": "PASS",
            "vlm_same_pet": "unknown",
            "vlm_anatomy": "unknown",
            "vlm_motion": "PASS",
            "vlm_composition": "unknown",
        },
        "business_signals": {
            "motion_temporal_integrity": "PASS",
            "breathing_motion_correctness": "PASS",
            "breathing_global_motion_integrity": "PASS",
            "breathing_composition_integrity": "PASS",
        },
        "motion_business_contract": {"check_authority": {}},
        "temporal": {"verdict": "breathing_detected"},
        "reasons": ["identity_over_time_uncertain"],
    }
    decision = vlm_escalation.should_call_vlm(
        stage="MOTION",
        qa_result=qa,
        request_kind="MICRO",
        motion_id="BREATHING",
    )
    assert decision["decision"] == vlm_escalation.CALL
    assert decision["requested_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert vlm_escalation.MOTION_VLM not in decision["requested_tasks"]


def test_explicit_refresh_calls_vlm_unless_hard_fail_is_already_clear():
    called = vlm_escalation.should_call_vlm(
        stage="CANONICAL",
        qa_result=_canonical(),
        request_kind="CANONICAL",
        force_call=True,
    )
    assert called["decision"] == vlm_escalation.CALL
    assert called["reason_codes"] == ["explicit_vlm_cache_refresh"]

    failed = vlm_escalation.should_call_vlm(
        stage="CANONICAL",
        qa_result=_canonical(
            checks={"cutout": "FAIL"},
            signals={"canonical_cutout_integrity": "FAIL"},
        ),
        request_kind="CANONICAL",
        force_call=True,
    )
    assert failed["decision"] == vlm_escalation.SKIP
