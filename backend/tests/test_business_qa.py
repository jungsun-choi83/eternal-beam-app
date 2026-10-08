from backend.services import business_qa as qa
from backend.services import motion_spec


def _legacy(checks, decision="REVIEW"):
    return {
        "qa_version": "legacy-test-v1",
        "decision": decision,
        "checks": dict(checks),
        "reasons": ["legacy_reason"],
        "vlm": {"notes": "preserve me"},
        "metrics": {"score": 0.42},
    }


def test_cosmetic_review_delivers_and_stops():
    result = qa.build_business_result(
        _legacy({"loop_return": "REVIEW"}),
        attempt_number=1,
        request_kind="MICRO",
    )

    assert result["integrity_status"] == "PASS"
    assert result["quality_status"] == "REVIEW"
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"
    assert result["terminal_state"] == qa.DELIVERED_GENERATED


def test_coat_color_mismatch_alone_does_not_regenerate():
    result = qa.build_business_result(
        _legacy({"coat_colors": "FAIL"}, decision="FAIL"),
        attempt_number=1,
        request_kind="CANONICAL",
    )

    assert result["authority_evidence"]["IDENTITY_SUPPORT"] == {"coat_colors": "FAIL"}
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_pixel_statistics_are_diagnostic_for_canonical_and_keyframe_only():
    legacy = _legacy(
        {"identity_similarity": "FAIL", "visual_embedding": "FAIL"},
        decision="FAIL",
    )
    canonical = qa.build_business_result(
        legacy,
        attempt_number=1,
        request_kind="CANONICAL",
    )
    keyframe = qa.build_business_result(
        legacy,
        attempt_number=1,
        request_kind="KEYFRAME",
    )

    assert canonical["authority_evidence"]["DIAGNOSTIC_ONLY"] == {
        "identity_similarity": "FAIL",
        "visual_embedding": "FAIL",
    }
    assert keyframe["authority_evidence"]["DIAGNOSTIC_ONLY"] == {
        "identity_similarity": "FAIL",
        "visual_embedding": "FAIL",
    }
    assert keyframe["retry_action"] == "STOP"


def test_keyframe_color_and_minor_morphology_are_non_blocking():
    legacy = _legacy(
        {
            "coat_colors": "FAIL",
            "structure": "REVIEW",
            "pose": "PASS",
            "keyframe_pose_integrity": "PASS",
            "keyframe_visibility": "PASS",
        },
        decision="FAIL",
    )
    result = qa.build_business_result(
        legacy,
        attempt_number=1,
        request_kind="KEYFRAME",
    )

    assert result["authority_profile"] == qa.KEYFRAME_IDENTITY_AUTHORITY_VERSION
    assert result["integrity_status"] == "PASS"
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_legacy_keyframe_pose_fail_stays_hard_without_vnext_signal():
    result = qa.build_business_result(
        _legacy({"pose": "FAIL"}, decision="FAIL"),
        attempt_number=1,
        request_kind="KEYFRAME",
    )

    assert result["authority_evidence"]["INTEGRITY_HARD"] == {"pose": "FAIL"}
    assert result["retry_action"] == "REGENERATE"


def test_quality_advisory_alone_never_blocks():
    result = qa.build_business_result(
        _legacy({"output_resolution": "FAIL"}, decision="FAIL"),
        attempt_number=1,
        request_kind="MOTION",
    )

    assert result["quality_status"] == "FAIL"
    assert result["delivery_action"] != "BLOCK"
    assert result["retry_action"] == "STOP"


def test_micro_unintended_large_motion_is_quality_not_contamination():
    legacy = _legacy({"vlm_composition": "FAIL"}, decision="FAIL")
    legacy["reasons"] = ["vlm_unintended_large_motion"]
    result = qa.build_business_result(
        legacy, attempt_number=1, request_kind="MICRO"
    )

    assert result["authority_evidence"]["QUALITY_ADVISORY"] == {
        "vlm_composition": "FAIL"
    }
    assert result["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["retry_action"] == "STOP"


def test_hard_wrong_identity_attempt_one_regenerates():
    legacy = _legacy({"vlm_same_pet": "FAIL"}, decision="FAIL")
    legacy["vlm"] = {"same_pet_confidence": "high"}
    result = qa.build_business_result(
        legacy,
        attempt_number=1,
        request_kind="CANONICAL",
    )

    assert result["integrity_status"] == "FAIL"
    assert result["delivery_action"] == "BLOCK"
    assert result["retry_action"] == "REGENERATE"
    assert result["terminal_state"] is None


def test_second_hard_failure_stops_paid_generation_and_falls_back():
    result = qa.build_business_result(
        _legacy({"vlm_anatomy": "FAIL"}, decision="FAIL"),
        attempt_number=2,
        request_kind="KEYFRAME",
    )

    assert result["integrity_status"] == "FAIL"
    assert result["retry_action"] == "FALLBACK"
    assert result["terminal_state"] == qa.DELIVERED_FALLBACK


def test_second_hard_failure_without_fallback_escalates_without_more_spend():
    result = qa.build_business_result(
        _legacy({"vlm_anatomy": "FAIL"}, decision="FAIL"),
        attempt_number=2,
        request_kind="KEYFRAME",
        fallback_available=False,
    )

    assert result["delivery_action"] == "ESCALATE"
    assert result["retry_action"] == "STOP"
    assert result["terminal_state"] is None


def test_deliverable_candidate_one_prevents_candidate_two():
    first = {"id": "one", "decision": "REVIEW", "qa_result": _legacy({"loop_return": "REVIEW"})}
    qa.attach_business_result(
        first["qa_result"], attempt_number=1, request_kind="MICRO"
    )

    assert qa.is_deliverable(first["qa_result"]) is True
    assert qa.may_generate_next_candidate([first]) is False


def test_only_hard_failure_can_open_second_paid_candidate():
    first = {"id": "one", "decision": "FAIL", "qa_result": _legacy({"vlm_same_pet": "FAIL"}, "FAIL")}
    first["qa_result"]["vlm"] = {"same_pet_confidence": "high"}
    qa.attach_business_result(
        first["qa_result"], attempt_number=1, request_kind="CANONICAL"
    )
    assert qa.may_generate_next_candidate([first]) is True

    second = {"id": "two", "decision": "FAIL", "qa_result": _legacy({"vlm_anatomy": "FAIL"}, "FAIL")}
    qa.attach_business_result(
        second["qa_result"], attempt_number=2, request_kind="CANONICAL"
    )
    assert qa.may_generate_next_candidate([first, second]) is False


def test_legacy_evidence_and_decision_survive_business_receipt_attachment():
    legacy = _legacy({"coat_colors": "FAIL"}, decision="FAIL")
    original_reasons = list(legacy["reasons"])
    original_vlm = dict(legacy["vlm"])
    original_metrics = dict(legacy["metrics"])

    qa.attach_business_result(
        legacy, attempt_number=1, request_kind="CANONICAL"
    )

    assert legacy["decision"] == "FAIL"
    assert legacy["reasons"] == original_reasons
    assert legacy["vlm"] == original_vlm
    assert legacy["metrics"] == original_metrics
    assert legacy["business_qa"]["version"] == qa.BUSINESS_QA_VERSION
    assert legacy["business_qa"]["legacy_decision"] == "FAIL"


def test_business_budget_is_one_normal_two_maximum():
    budget = qa.automatic_candidate_budget("PREMIUM_PRODUCT")
    assert budget["candidate_1"] == "ALWAYS"
    assert budget["candidate_2"] == "INTEGRITY_HARD_FAILURE_ONLY"
    assert budget["max_paid_candidates"] == 2


def _business_candidate(candidate_id, attempt, checks, *, decision="REVIEW"):
    qa_result = _legacy(checks, decision=decision)
    qa.attach_business_result(
        qa_result,
        attempt_number=attempt,
        request_kind="MICRO",
    )
    return {
        "id": candidate_id,
        "attempt": attempt,
        "decision": decision,
        "qa_result": qa_result,
    }


def test_best_available_uses_business_priority_before_legacy_decision():
    # Candidate A has the more attractive legacy decision, but uncertain pet
    # identity. Candidate B has clean identity and only anatomy uncertainty.
    # Identity is the first business selection dimension.
    candidate_a = _business_candidate(
        "identity-review",
        1,
        {"vlm_same_pet": "REVIEW", "vlm_anatomy": "PASS"},
        decision="PASS",
    )
    candidate_b = _business_candidate(
        "anatomy-review",
        2,
        {"vlm_same_pet": "PASS", "vlm_anatomy": "REVIEW"},
        decision="REVIEW",
    )

    selected = qa.best_available_candidate([candidate_a, candidate_b])

    assert selected["id"] == "anatomy-review"
    priority = qa.candidate_selection_priority(selected)
    assert priority["version"] == qa.BEST_AVAILABLE_POLICY_VERSION
    assert priority["identity_integrity"]["status"] == "PASS"
    assert priority["anatomy_integrity"]["status"] == "REVIEW"


def test_best_available_order_is_identity_anatomy_motion_technical_presentation():
    candidates = [
        _business_candidate("identity", 1, {"vlm_same_pet": "REVIEW"}),
        _business_candidate("anatomy", 1, {"vlm_anatomy": "REVIEW"}),
        _business_candidate("motion", 1, {"vlm_motion": "REVIEW"}),
        _business_candidate("technical", 1, {"output_probe": "REVIEW"}),
        _business_candidate("presentation", 1, {"coat_colors": "REVIEW"}),
    ]

    # A presentation-only advisory is preferred because every higher-priority
    # integrity/correctness dimension is clean.
    assert qa.best_available_candidate(candidates)["id"] == "presentation"


def test_no_legacy_pass_but_safe_candidate_is_selected():
    safe = _business_candidate(
        "safe-legacy-fail",
        1,
        {"coat_colors": "FAIL"},
        decision="FAIL",
    )

    assert qa.is_deliverable(safe["qa_result"]) is True
    assert qa.best_available_candidate([safe]) is safe


def test_second_hard_failure_authorizes_fallback_and_never_candidate_three():
    first = _business_candidate(
        "hard-one", 1, {"vlm_same_pet": "FAIL"}, decision="FAIL"
    )
    first["qa_result"]["vlm"] = {"same_pet_confidence": "high"}
    # Rebuild after adding the confidence evidence that confirms the hard
    # identity contradiction.
    qa.attach_business_result(
        first["qa_result"], attempt_number=1, request_kind="MICRO"
    )
    second = _business_candidate(
        "hard-two", 2, {"vlm_anatomy": "FAIL"}, decision="FAIL"
    )

    candidates = [first, second]
    fallback = qa.fallback_receipt(candidates)
    assert fallback["retry_action"] == "FALLBACK"
    assert fallback["attempt_number"] == 2
    assert qa.may_generate_next_candidate(candidates) is False


def _motion_result(motion_id, checks, *, signals=None, decision="REVIEW"):
    result = _legacy(checks, decision=decision)
    result["motion_business_contract"] = dict(
        motion_spec.MOTIONS[motion_id].requirements["qa"]["business"]
    )
    result["business_signals"] = dict(signals or {})
    return result


def test_motion_class_advisories_do_not_regenerate():
    cases = (
        ("BREATHING", {"temporal_stability": "REVIEW", "loop_return": "REVIEW"}),
        ("LIE_DOWN", {"structural_morphology_consistency": "REVIEW"}),
        ("RUN", {"structural_morphology_consistency": "REVIEW"}),
        ("PET_HEAD", {"structural_morphology_consistency": "REVIEW"}),
    )
    for motion_id, checks in cases:
        kind = motion_spec.MOTIONS[motion_id].motion_class
        qa_result = _motion_result(motion_id, checks)
        result = qa.build_business_result(
            qa_result,
            attempt_number=1,
            request_kind=kind,
        )
        assert result["integrity_status"] == "PASS", motion_id
        assert result["delivery_action"] == "DELIVER_WITH_ADVISORY", motion_id
        assert result["retry_action"] == "STOP", motion_id
        qa_result["business_qa"] = result
        candidate = {"id": motion_id, "decision": "REVIEW", "qa_result": qa_result}
        assert qa.may_generate_next_candidate([candidate]) is False, motion_id


def test_motion_class_true_integrity_failures_can_regenerate():
    cases = (
        ("LIE_DOWN", {"reaches_target_pose": "FAIL"}),
        ("RUN", {"vlm_direction_travel": "FAIL"}),
        ("PET_HEAD", {"vlm_interaction": "FAIL"}),
    )
    for motion_id, checks in cases:
        kind = motion_spec.MOTIONS[motion_id].motion_class
        result = qa.build_business_result(
            _motion_result(motion_id, checks, decision="FAIL"),
            attempt_number=1,
            request_kind=kind,
        )
        assert result["integrity_status"] == "FAIL", motion_id
        assert result["delivery_action"] == "BLOCK", motion_id
        assert result["retry_action"] == "REGENERATE", motion_id


def test_breathing_vlm_motion_no_and_global_pulse_are_advisory_but_catastrophic_is_hard():
    vlm_only = qa.build_business_result(
        _motion_result(
            "BREATHING",
            {"vlm_motion": "FAIL", "temporal_breathing": "PASS"},
            signals={
                "breathing_motion_correctness": "PASS",
                "breathing_global_motion_integrity": "PASS",
                "breathing_catastrophic_motion_integrity": "PASS",
                "breathing_composition_integrity": "PASS",
            },
            decision="FAIL",
        ),
        attempt_number=1,
        request_kind="MICRO",
    )
    global_pulse = qa.build_business_result(
        _motion_result(
            "BREATHING",
            {"vlm_motion": "PASS", "temporal_breathing": "FAIL"},
            signals={
                "breathing_motion_correctness": "REVIEW",
                "breathing_global_motion_integrity": "REVIEW",
                "breathing_catastrophic_motion_integrity": "PASS",
                "breathing_composition_integrity": "PASS",
            },
            decision="FAIL",
        ),
        attempt_number=1,
        request_kind="MICRO",
    )
    catastrophic = qa.build_business_result(
        _motion_result(
            "BREATHING",
            {"vlm_motion": "PASS", "temporal_breathing": "FAIL"},
            signals={
                "breathing_motion_correctness": "REVIEW",
                "breathing_global_motion_integrity": "REVIEW",
                "breathing_catastrophic_motion_integrity": "FAIL",
                "breathing_composition_integrity": "PASS",
            },
            decision="FAIL",
        ),
        attempt_number=1,
        request_kind="MICRO",
    )

    assert vlm_only["authority_profile"] == qa.BREATHING_AUTHORITY_VERSION == "breathing-v2"
    assert vlm_only["integrity_status"] == "PASS"
    assert vlm_only["delivery_action"] == "DELIVER"
    assert vlm_only["retry_action"] == "STOP"
    assert global_pulse["integrity_status"] == "PASS"
    assert global_pulse["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert global_pulse["retry_action"] == "STOP"
    assert catastrophic["integrity_status"] == "FAIL"
    assert catastrophic["retry_action"] == "REGENERATE"


def test_motion_contract_uses_phase1_authority_profile_and_budget():
    result = qa.build_business_result(
        _motion_result("RUN", {"vlm_direction_travel": "PASS"}, decision="PASS"),
        attempt_number=1,
        request_kind="LOCOMOTION",
    )

    assert result["version"] == qa.BUSINESS_QA_VERSION
    assert result["authority_profile"] == "motion-class-v1:locomotion"
    assert result["delivery_action"] == "DELIVER"
    assert result["retry_action"] == "STOP"
