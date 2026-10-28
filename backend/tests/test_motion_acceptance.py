"""Acceptance policy regression tests. All VLM/provider responses are simulated."""
import asyncio
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from backend.services import business_qa, motion_spec, motion_video_qa, motion_video_service
from backend.services import vlm_escalation, vlm_identity

PREMIUM_MICRO_IDLE_MOTIONS = (
    "BLINKING", "EAR_TWITCHING", "HEAD_TILTING", "TAIL_WAGGING",
)


def micro_video_evidence(**changes):
    """Structured video-evidence fixture matching the acceptance VLM schema."""
    result = {
        "same_pet_all_frames": "yes",
        "anatomy_plausible_all_frames": "yes",
        "requested_motion_occurs": "yes",
        "single_pet": "yes",
        "duplicated_pet": "no",
        "human_present": "no",
        "scene_cut": "no",
        "major_flicker": "no",
        "camera_stable": "yes",
        "background_neutral": "yes",
        "unintended_large_motion": "no",
        "severe_motion_defect": "no",
        "critical_state_violation": "no",
        "final_position_verified": "yes",
        "wrong_primary_action_observed": "no",
        "interaction_correct": "yes",
        "human_hand_policy_ok": "yes",
        "notes": "natural breathing and harmless incidental living motion",
    }
    result.update(changes)
    return result


def judge_micro_video(mid, vlm_evidence, *, temporal_stability="PASS"):
    result = motion_video_qa.apply_judgement(
        checks={
            "identity_over_time": "PASS",
            "temporal_stability": temporal_stability,
            "loop_return": "PASS",
        },
        reasons=[],
        spec_contract=motion_spec.motion_snapshot(motion_spec.MOTIONS[mid]),
        vlm_qa=vlm_evidence,
        temporal_qa=None,
        structural_evidence={},
    )
    business_qa.attach_business_result(result, attempt_number=1, request_kind="MICRO")
    return result


def judge_pet_head(vlm_evidence, *, temporal_stability="PASS"):
    result = motion_video_qa.apply_judgement(
        checks={
            "identity_over_time": "PASS",
            "temporal_stability": temporal_stability,
            "loop_return": "PASS",
            "structural_morphology_consistency": "PASS",
        },
        reasons=[],
        spec_contract=motion_spec.motion_snapshot(motion_spec.MOTIONS["PET_HEAD"]),
        vlm_qa=vlm_evidence,
        temporal_qa=None,
        structural_evidence={},
    )
    business_qa.attach_business_result(result, attempt_number=1, request_kind="INTERACTION")
    return result


def judge_paw_hello(vlm_evidence, *, temporal_stability="PASS"):
    result = motion_video_qa.apply_judgement(
        checks={
            "identity_over_time": "PASS",
            "temporal_stability": temporal_stability,
            "loop_return": "PASS",
            "structural_morphology_consistency": "PASS",
        },
        reasons=[],
        spec_contract=motion_spec.motion_snapshot(motion_spec.MOTIONS["PAW_HELLO"]),
        vlm_qa=vlm_evidence,
        temporal_qa=None,
        structural_evidence={},
    )
    business_qa.attach_business_result(result, attempt_number=1, request_kind="INTERACTION")
    return result


def paw_hello_evidence(**changes):
    result = micro_video_evidence(
        paw_lift_visible="yes",
        paw_offer_toward_viewer="yes",
        paw_hold_visible="yes",
        supporting_paws_grounded="yes",
        natural_balance="yes",
        limb_count_correct="yes",
        paw_anatomy_plausible="yes",
        smooth_return_to_stand_ready="yes",
    )
    result.update(changes)
    return result


def evidence(mid="COME_CLOSER", **changes):
    spec = motion_spec.MOTIONS[mid]
    contract = deepcopy(spec.requirements["qa"]["business"])
    checks = dict.fromkeys(contract["acceptance"]["required_checks"], "PASS")
    checks.update(changes)
    decision = ("FAIL" if "FAIL" in checks.values() else
                "REVIEW" if any(value in {"REVIEW", "unknown"} for value in checks.values()) else
                "PASS")
    return {"decision": decision,
            "checks": checks, "motion_business_contract": contract}


def classify(mid="COME_CLOSER", **changes):
    result = evidence(mid, **changes)
    business_qa.attach_business_result(result, attempt_number=1,
                                      request_kind=motion_spec.MOTIONS[mid].motion_class)
    return result


@pytest.mark.parametrize("scenario,checks", [
    ("natural approach", {}), ("playful bounding", {}),
    ("imperfect gait", {"vlm_locomotion_form": "FAIL"}),
    ("unobservable footfalls", {"vlm_locomotion_form": "unknown"}),
    ("brief paw cropping", {"vlm_locomotion_form": "REVIEW"}),
    ("reorientation", {}), ("stationary ending", {}),
    ("uncertain closer position", {"vlm_final_position": "REVIEW"}),
])
def test_usable_approach_never_buys_replacement(scenario, checks):
    result = classify(**checks)
    expected = ("FAIL" if "FAIL" in checks.values() else
                "REVIEW" if any(value in {"REVIEW", "unknown"} for value in checks.values()) else
                "PASS")
    assert result["decision"] == expected, scenario
    assert result["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable({"motion_id": "COME_CLOSER", "qa_result": result})
    assert not business_qa.may_generate_next_candidate([{"id": "existing", "qa_result": result, "decision": "PASS"}])
    if checks:
        assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"


@pytest.mark.parametrize("mid", ["BREATHING", "COME_CLOSER", "RETURN_HOME"])
@pytest.mark.parametrize("check", ["vlm_motion", "vlm_same_pet", "vlm_anatomy", "vlm_motion_integrity",
                                  "vlm_critical_state", "motion_temporal_integrity"])
def test_demonstrated_mandatory_failures_remain_blocking(mid, check):
    result = classify(mid, **{check: "FAIL"})
    assert result["decision"] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "BLOCK"
    assert not motion_video_service.candidate_is_publishable({"motion_id": mid, "qa_result": result})


@pytest.mark.parametrize("mid", ["COME_CLOSER", "RETURN_HOME"])
def test_wrong_direction_is_not_gait_advice(mid):
    assert classify(mid, vlm_direction_travel="FAIL")["decision"] == "FAIL"


@pytest.mark.parametrize("mid", ["BREATHING", "COME_CLOSER", "RETURN_HOME"])
def test_missing_or_unknown_mandatory_evidence_publishes_with_advisory_without_spend(mid):
    result = evidence(mid)
    result["checks"].pop("vlm_anatomy")
    result["decision"] = "REVIEW"
    # Even historical escalation metadata must not erase required evidence.
    result["vlm_escalation"] = {"decision": "CALL", "non_authoritative_checks_when_unrequested": ["vlm_anatomy"]}
    business_qa.attach_business_result(result, attempt_number=1, request_kind=motion_spec.MOTIONS[mid].motion_class)
    assert result["decision"] == "REVIEW"
    assert result["business_qa"]["retry_action"] == "STOP"
    assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert motion_video_service.candidate_is_publishable({"motion_id": mid, "qa_result": result})
    assert not business_qa.may_generate_next_candidate([{"id": "review", "qa_result": result, "decision": "REVIEW"}])


@pytest.mark.parametrize("verdict,delivery,publishable", [
    ("PASS", "DELIVER", True),
    ("REVIEW", "DELIVER_WITH_ADVISORY", True),
    ("FAIL", "BLOCK", False),
])
def test_acceptance_pass_review_and_fail_publication_rule(verdict, delivery, publishable):
    result = evidence("BREATHING", vlm_composition=verdict)
    result["decision"] = verdict
    business_qa.attach_business_result(
        result, attempt_number=1, request_kind=motion_spec.MOTIONS["BREATHING"].motion_class
    )
    assert result["decision"] == verdict
    assert result["business_qa"]["delivery_action"] == delivery
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": "BREATHING", "qa_result": result}
    ) is publishable
    if verdict == "REVIEW":
        assert result["business_qa"]["quality_status"] == "REVIEW"
        assert result["business_qa"]["integrity_status"] == "PASS"
        assert result["business_qa"]["authority_evidence"]["QUALITY_ADVISORY"]["vlm_composition"] == "REVIEW"


def test_unregistered_uncertainty_cannot_silently_pass():
    assert classify(new_unclassified_check="unknown")["decision"] == "REVIEW"


@pytest.mark.parametrize("checks", [
    {}, {"breathing_periodicity": "REVIEW"}, {"breathing_head_motion": "REVIEW"},
    {"loop_return": "REVIEW"},
])
def test_subtle_living_motion_and_minor_loop_imperfection(checks):
    result = classify("BREATHING", **checks)
    assert result["decision"] == ("REVIEW" if checks else "PASS")
    assert result["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": "BREATHING", "qa_result": result}
    )


@pytest.mark.parametrize("checks", [
    {}, {"vlm_locomotion_form": "FAIL"}, {"reaches_target_pose": "FAIL", "vlm_target_pose": "REVIEW"},
])
def test_reasonable_return_tolerates_minor_gait_and_pixel_endpoint_difference(checks):
    result = classify("RETURN_HOME", **checks)
    assert result["decision"] == ("FAIL" if "FAIL" in checks.values() else "PASS")
    assert result["business_qa"]["delivery_action"] in {"DELIVER", "DELIVER_WITH_ADVISORY"}
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": "RETURN_HOME", "qa_result": result}
    )


def test_unknown_critical_return_compatibility_is_review():
    result = classify("RETURN_HOME", vlm_critical_state="unknown")
    assert result["decision"] == "REVIEW"
    assert result["business_qa"]["retry_action"] == "STOP"


def test_output_corruption_remains_blocking():
    result = evidence()
    result["output_conformance"] = {"status": "FAIL", "checks": {"aspect_ratio": "FAIL"}}
    result["decision"] = "FAIL"  # The live evaluator applies conformance before Business QA.
    business_qa.attach_business_result(result, attempt_number=1, request_kind="LOCOMOTION")
    assert result["decision"] == "FAIL"


def test_beta_escalation_requests_identity_anatomy_and_motion():
    result = evidence()
    escalation = vlm_escalation.should_call_vlm(stage="MOTION", qa_result=result,
                                               motion_id="COME_CLOSER", request_kind="LOCOMOTION")
    assert set(escalation["requested_tasks"]) == {vlm_escalation.IDENTITY_ANATOMY_VLM, vlm_escalation.MOTION_VLM}


def test_generation_description_is_independent_and_other_motion_authority_unchanged():
    targeted = {
        "BREATHING", "COME_CLOSER", "RETURN_HOME", "PET_HEAD", "PAW_HELLO",
        *PREMIUM_MICRO_IDLE_MOTIONS,
    }
    for mid, spec in motion_spec.MOTIONS.items():
        assert spec.qa_acceptance_spec["motion_id"] == mid
        if mid not in targeted:
            assert spec.qa_acceptance_spec["description"] == spec.description
            assert "review_policy" not in spec.qa_acceptance_spec
    spec = motion_spec.MOTIONS["COME_CLOSER"]
    changed = replace(spec, description="An impossibly perfect running gait")
    assert changed.qa_acceptance_spec == spec.qa_acceptance_spec
    assert motion_spec.MOTIONS["RUN"].requirements["qa"]["business"]["check_authority"]["vlm_locomotion_form"] == "INTEGRITY_HARD"
    assert "runs naturally" in spec.description


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_contract_correct_motion_with_living_behavior_passes(mid):
    result = judge_micro_video(mid, micro_video_evidence())
    assert result["decision"] == "PASS"
    assert result["business_qa"]["integrity_status"] == "PASS"
    assert result["business_qa"]["delivery_action"] == "DELIVER"
    assert result["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": mid, "decision": result["decision"], "qa_result": result}
    )


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_contract_harmless_incidental_motion_is_not_a_failure(mid):
    evidence = micro_video_evidence(
        notes="requested action is primary; breathing, blink, ear adjustment and small body response are incidental"
    )
    result = judge_micro_video(mid, evidence)
    assert result["checks"]["vlm_motion"] == "PASS"
    assert result["decision"] == "PASS"
    assert result["business_qa"]["delivery_action"] == "DELIVER"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_contract_ambiguous_video_evidence_is_publishable_review(mid):
    result = judge_micro_video(
        mid,
        micro_video_evidence(
            requested_motion_occurs="unknown",
            final_position_verified="unknown",
            notes="occlusion/perspective/sparse temporal samples make the action uncertain",
        ),
    )
    assert result["decision"] == "REVIEW"
    assert result["checks"]["vlm_motion"] == "unknown"
    assert result["business_qa"]["integrity_status"] == "REVIEW"
    assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": mid, "decision": result["decision"], "qa_result": result}
    )
    assert not business_qa.may_generate_next_candidate(
        [{"id": "review", "decision": result["decision"], "qa_result": result}]
    )


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_contract_confirmed_wrong_primary_motion_fails(mid):
    result = judge_micro_video(
        mid,
        micro_video_evidence(
            requested_motion_occurs="no",
            notes="adequate chronological evidence shows a different primary action",
        ),
    )
    assert result["decision"] == "FAIL"
    assert result["business_qa"]["integrity_status"] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "BLOCK"
    assert result["business_qa"]["retry_action"] == "REGENERATE"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
@pytest.mark.parametrize(
    "evidence_change",
    [
        {"same_pet_all_frames": "no"},
        {"anatomy_plausible_all_frames": "no"},
        {"severe_motion_defect": "yes"},
    ],
)
def test_premium_micro_contract_serious_identity_anatomy_integrity_failure_blocks(
    mid, evidence_change
):
    result = judge_micro_video(mid, micro_video_evidence(**evidence_change))
    assert result["decision"] == "FAIL"
    assert result["business_qa"]["integrity_status"] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "BLOCK"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_sparse_temporal_heuristic_is_advisory_not_regeneration(mid):
    result = judge_micro_video(mid, micro_video_evidence(), temporal_stability="FAIL")
    # The legacy observation remains visible, but the targeted authority says a
    # sparse pixel heuristic alone cannot prove a severe temporal failure.
    assert result["checks"]["temporal_stability"] == "FAIL"
    assert result["business_qa"]["authority_evidence"]["QUALITY_ADVISORY"][
        "motion_temporal_integrity"
    ] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["business_qa"]["retry_action"] == "STOP"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_contract_contains_motion_specific_acceptance_language(mid):
    acceptance = motion_spec.MOTIONS[mid].qa_acceptance_spec
    assert acceptance["version"] == motion_spec.MOTION_ACCEPTANCE_VERSION
    assert acceptance["policy_revision"] == "premium-micro-idle-v1"
    assert acceptance["review_policy"] == "publish_with_advisory"
    assert "natural breathing" in acceptance["allowed_secondary_motion"]
    assert acceptance["temporal_evidence_policy"]["absence_requires_reliable_coverage"] is True
    assert acceptance["check_authority"]["vlm_motion"] == "INTEGRITY_HARD"
    assert acceptance["check_authority"]["vlm_anatomy"] == "INTEGRITY_HARD"
    assert acceptance["check_authority"]["motion_temporal_integrity"] == "QUALITY_ADVISORY"


def test_pet_head_acceptance_contract_is_affectionate_living_motion_without_a_hand():
    spec = motion_spec.MOTIONS["PET_HEAD"]
    acceptance = spec.qa_acceptance_spec
    assert acceptance["version"] == motion_spec.MOTION_ACCEPTANCE_VERSION
    assert acceptance["policy_revision"] == "pet-head-v1"
    assert acceptance["review_policy"] == "publish_with_advisory"
    assert "natural breathing" in acceptance["allowed_secondary_motion"]
    assert acceptance["temporal_evidence_policy"]["absence_requires_reliable_coverage"] is True
    assert {"vlm_interaction", "vlm_human_hand_policy"} <= set(acceptance["required_checks"])
    assert acceptance["check_authority"]["vlm_interaction"] == "INTEGRITY_HARD"
    assert acceptance["check_authority"]["motion_temporal_integrity"] == "QUALITY_ADVISORY"
    assert spec.video_compat["allow_generated_hand"] is False


def test_pet_head_correct_affectionate_response_with_living_motion_passes():
    result = judge_pet_head(micro_video_evidence(
        notes="visible gentle head lean, softened eyes, breathing and a small ear adjustment",
    ))
    assert result["decision"] == "PASS"
    assert result["business_qa"]["delivery_action"] == "DELIVER"


def test_pet_head_uncertain_sparse_response_reviews_and_publishes_without_retry():
    sampling = {
        "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
        "strategy": "adaptive_dense_unavailable",
        "absence_reliable": False,
    }
    adjusted = motion_video_service._enforce_temporal_evidence_reliability(
        micro_video_evidence(
            requested_motion_occurs="no",
            interaction_correct="no",
            notes="the subtle response is not visible in the sparse samples",
        ),
        motion_id="PET_HEAD",
        sampling=sampling,
    )
    assert adjusted["requested_motion_occurs"] == "unknown"
    assert adjusted["interaction_correct"] == "unknown"
    assert adjusted["requested_motion_occurs_raw"] == "no"
    assert adjusted["interaction_correct_raw"] == "no"
    result = judge_pet_head(adjusted)
    assert result["decision"] == "REVIEW"
    assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": "PET_HEAD", "decision": result["decision"], "qa_result": result}
    )


def test_pet_head_reliably_absent_or_wrong_response_fails():
    for evidence in (
        micro_video_evidence(requested_motion_occurs="no", interaction_correct="no"),
        micro_video_evidence(
            requested_motion_occurs="no",
            interaction_correct="no",
            wrong_primary_action_observed="yes",
        ),
    ):
        result = judge_pet_head(evidence)
        assert result["decision"] == "FAIL"
        assert result["business_qa"]["delivery_action"] == "BLOCK"


def test_paw_hello_dedicated_vlm_checks_cover_pass_review_and_fail():
    passed = judge_paw_hello(paw_hello_evidence())
    assert passed["decision"] == "PASS"
    assert passed["checks"]["vlm_paw_lift"] == "PASS"
    assert passed["checks"]["vlm_paw_balance"] == "PASS"
    assert passed["checks"]["vlm_paw_limb_count"] == "PASS"
    assert passed["checks"]["vlm_paw_anatomy"] == "PASS"
    assert passed["checks"]["vlm_paw_return"] == "PASS"

    sampling = {
        "version": motion_video_service.PAW_HELLO_VLM_SAMPLING_VERSION,
        "strategy": "adaptive_dense_unavailable",
        "absence_reliable": False,
    }
    uncertain = motion_video_service._enforce_temporal_evidence_reliability(
        paw_hello_evidence(
            requested_motion_occurs="no", interaction_correct="no",
            paw_lift_visible="no", paw_offer_toward_viewer="no", paw_hold_visible="no",
        ),
        motion_id="PAW_HELLO",
        sampling=sampling,
    )
    reviewed = judge_paw_hello(uncertain)
    assert reviewed["decision"] == "REVIEW"
    assert reviewed["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert reviewed["business_qa"]["retry_action"] == "STOP"
    assert motion_video_service.candidate_is_publishable(
        {"motion_id": "PAW_HELLO", "decision": "REVIEW", "qa_result": reviewed}
    )

    for defect in (
        {"limb_count_correct": "no"},
        {"paw_anatomy_plausible": "no"},
        {"natural_balance": "no"},
        {"smooth_return_to_stand_ready": "no"},
    ):
        failed = judge_paw_hello(paw_hello_evidence(**defect))
        assert failed["decision"] == "FAIL", defect
        assert failed["business_qa"]["delivery_action"] == "BLOCK"


def test_paw_hello_targeted_vlm_schema_and_version_are_motion_scoped():
    acceptance = motion_spec.MOTIONS["PAW_HELLO"].qa_acceptance_spec
    specs = vlm_identity._motion_targeted_specs(
        questions=["motion_correctness", "temporal_integrity"],
        motion_description=motion_spec.MOTIONS["PAW_HELLO"].description,
        motion_class="INTERACTION",
        expected_direction=None,
        interaction_type="paw_hello",
        allow_generated_hand=False,
        has_target=True,
        acceptance_spec=acceptance,
    )
    schema, prompt = specs[vlm_escalation.MOTION_VLM]
    assert set(vlm_identity.PAW_HELLO_QA_FIELDS) <= set(schema["properties"])
    assert "exactly one front paw visibly lifts" in prompt
    assert (
        vlm_identity.motion_acceptance_evaluator_version(acceptance)
        == vlm_identity.VLM_PAW_HELLO_QA_VERSION
    )
    assert (
        vlm_identity.motion_acceptance_evaluator_version(
            motion_spec.MOTIONS["PET_HEAD"].qa_acceptance_spec
        )
        != vlm_identity.VLM_PAW_HELLO_QA_VERSION
    )


@pytest.mark.parametrize("evidence_change", [
    {"same_pet_all_frames": "no"},
    {"anatomy_plausible_all_frames": "no"},
    {"human_present": "yes", "human_hand_policy_ok": "no"},
])
def test_pet_head_serious_identity_anatomy_or_hand_failure_blocks(evidence_change):
    result = judge_pet_head(micro_video_evidence(**evidence_change))
    assert result["decision"] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "BLOCK"


def _motion_frame(*, changed=False):
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    if changed:
        frame[4:24, 14:50] = 255
    return frame


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_visible_motion_in_original_samples_reuses_nine_frames(mid, monkeypatch):
    base = [_motion_frame() for _ in motion_video_qa.SAMPLE_FRACTIONS]
    base[4] = _motion_frame(changed=True)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("clear base evidence must not trigger dense decoding")

    monkeypatch.setattr(motion_video_qa, "sample_dense_frames", unexpected)
    temporal, frames, fractions = asyncio.run(
        motion_video_service._breathing_evidence(mid, b"stored-video", None, base)
    )
    assert temporal is None
    assert len(frames) == len(fractions) == 9
    assert frames.provenance["strategy"] == "base_reused_salient"
    assert frames.provenance["dense_scan_attempted"] is False
    assert frames.provenance["absence_reliable"] is (mid not in {"BLINKING", "EAR_TWITCHING"})


@pytest.mark.parametrize("mid", ["BLINKING", "EAR_TWITCHING", "PET_HEAD", "PAW_HELLO"])
def test_targeted_event_between_original_timestamps_reaches_vlm_evidence(mid, monkeypatch):
    base = [_motion_frame() for _ in motion_video_qa.SAMPLE_FRACTIONS]
    dense_fractions = [index / 50 for index in range(50)]
    dense_frames = [_motion_frame() for _ in dense_fractions]
    # 0.20 lies between original 0.125 and 0.25 samples.
    dense_frames[10] = _motion_frame(changed=True)
    calls = []

    def dense(_video_bytes, *, fps, max_frames):
        calls.append((fps, max_frames))
        return {
            "frames": dense_frames,
            "fractions": dense_fractions,
            "requested_fps": fps,
            "decoded_frames": len(dense_frames),
            "duration_sec": 5.0,
            "coverage_ratio": 0.98,
        }

    monkeypatch.setattr(motion_video_qa, "sample_dense_frames", dense)
    temporal, frames, fractions = asyncio.run(
        motion_video_service._breathing_evidence(mid, b"stored-video", None, base)
    )
    assert temporal is None
    assert calls == [(
        motion_video_service.MICRO_ADAPTIVE_SCAN_FPS,
        motion_video_service.MICRO_ADAPTIVE_SCAN_MAX_FRAMES,
    )]
    assert len(frames) == len(fractions) <= 12
    assert any(abs(fraction - 0.20) < 0.001 for fraction in fractions)
    assert frames.provenance["strategy"] == "adaptive_dense_salience"
    assert frames.provenance["dense_coverage_ratio"] == 0.98
    assert frames.provenance["absence_reliable"] is True


@pytest.mark.parametrize("mid,motion_class", [
    ("BLINKING", "MICRO"), ("PET_HEAD", "INTERACTION"),
    ("PAW_HELLO", "INTERACTION"),
])
def test_adaptive_frames_and_provenance_reach_existing_vlm_call(mid, motion_class, monkeypatch):
    base = [_motion_frame() for _ in motion_video_qa.SAMPLE_FRACTIONS]
    dense_fractions = [index / 50 for index in range(50)]
    dense_frames = [_motion_frame() for _ in dense_fractions]
    dense_frames[10] = _motion_frame(changed=True)
    monkeypatch.setattr(
        motion_video_qa,
        "sample_dense_frames",
        lambda *_args, **_kwargs: {
            "frames": dense_frames,
            "fractions": dense_fractions,
            "requested_fps": 12.0,
            "decoded_frames": len(dense_frames),
            "duration_sec": 5.0,
            "coverage_ratio": 0.98,
        },
    )
    captured = {}

    def vlm(frame_images, **kwargs):
        captured["call_count"] = captured.get("call_count", 0) + 1
        captured["frame_count"] = len(frame_images)
        captured.update(kwargs)
        return micro_video_evidence()

    contract = motion_spec.motion_snapshot(motion_spec.MOTIONS[mid])
    result = asyncio.run(motion_video_service._evaluate_candidate_qa(
        video_bytes=b"already-paid-generated-video",
        contract=contract,
        output_spec={},
        motion_description=motion_spec.MOTIONS[mid].description,
        motion_class=motion_class,
        start_rgb=base[0],
        target_rgb=None,
        start_bytes=b"start-reference",
        target_bytes=None,
        frame_sampler=lambda _video: base,
        vlm_qa_fn=vlm,
        conformance_fn=lambda _video, _spec: {
            "status": "PASS", "checks": {}, "reasons": []
        },
    ))

    sampling = captured["evidence_context"]["temporal_sampling"]
    assert captured["call_count"] == 1
    assert captured["frame_count"] <= 12
    assert any(abs(fraction - 0.20) < 0.001 for fraction in captured["sample_fractions"])
    assert sampling["strategy"] == "adaptive_dense_salience"
    assert sampling["selected_extra_fractions"]
    assert result["vlm_sampling"]["evidence_fractions"] == list(captured["sample_fractions"])
    assert result["vlm"]["requested_motion_occurs"] == "yes"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_incomplete_adaptive_evidence_changes_vlm_no_to_preserved_unknown(mid):
    sampling = {
        "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
        "strategy": "adaptive_dense_unavailable",
        "absence_reliable": False,
    }
    adjusted = motion_video_service._enforce_temporal_evidence_reliability(
        micro_video_evidence(requested_motion_occurs="no"),
        motion_id=mid,
        sampling=sampling,
    )
    assert adjusted["requested_motion_occurs"] == "unknown"
    assert adjusted["requested_motion_occurs_raw"] == "no"
    assert adjusted["temporal_evidence_adjustment"]["reason"] == \
        "insufficient_temporal_coverage_for_absence"
    result = judge_micro_video(mid, adjusted)
    assert result["decision"] == "REVIEW"
    assert result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert result["business_qa"]["retry_action"] == "STOP"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_reliably_captured_wrong_action_substitution_remains_fail(mid):
    sampling = {
        "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
        "strategy": "base_reused_salient",
        "absence_reliable": False,
    }
    observed = micro_video_evidence(
        requested_motion_occurs="no",
        wrong_primary_action_observed="yes",
        notes="a clearly visible different primary action replaces the requested action",
    )
    unchanged = motion_video_service._enforce_temporal_evidence_reliability(
        observed, motion_id=mid, sampling=sampling
    )
    assert unchanged["requested_motion_occurs"] == "no"
    result = judge_micro_video(mid, unchanged)
    assert result["decision"] == "FAIL"
    assert result["business_qa"]["delivery_action"] == "BLOCK"


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_premium_micro_prompt_requires_unknown_for_sparse_non_observation(mid):
    prompt = vlm_identity._acceptance_prompt(
        motion_spec.MOTIONS[mid].qa_acceptance_spec
    )
    assert "Natural breathing is allowed" in prompt
    assert "Do not demand complete body stillness" in prompt
    assert "insufficient proof that it never occurred" in prompt
    assert "requested_motion_occurs=no only" in prompt

    specs = vlm_identity._motion_targeted_specs(
        questions=["motion_correctness"],
        motion_description="generation text is not acceptance evidence",
        motion_class="MICRO",
        expected_direction=None,
        interaction_type=None,
        allow_generated_hand=False,
        has_target=False,
        acceptance_spec=motion_spec.MOTIONS[mid].qa_acceptance_spec,
        evidence_context={
            "temporal_sampling": {
                "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
                "strategy": "adaptive_dense_salience",
                "evidence_fractions": [0.0, 0.18, 0.2, 0.22, 1.0],
                "absence_reliable": False,
            }
        },
    )
    _schema, motion_prompt = specs[vlm_escalation.MOTION_VLM]
    assert "TEMPORAL EVIDENCE PROVENANCE" in motion_prompt
    assert '"strategy": "adaptive_dense_salience"' in motion_prompt
    assert "absence_reliable is false" in motion_prompt


@pytest.mark.parametrize("mid", ["BREATHING", "COME_CLOSER", "RETURN_HOME"])
def test_existing_targeted_contract_prompt_does_not_receive_new_micro_guidance(mid):
    acceptance = motion_spec.MOTIONS[mid].qa_acceptance_spec
    prompt = vlm_identity._acceptance_prompt(acceptance)
    assert "policy_revision" not in acceptance
    assert "allowed_secondary_motion" not in acceptance
    assert "Apply temporal_evidence_policy strictly" not in prompt


@pytest.mark.parametrize("mid", PREMIUM_MICRO_IDLE_MOTIONS)
def test_old_micro_acceptance_is_stale_for_qa_only_reevaluation(mid):
    current = evidence(mid)
    current.update({
        "qa_version": motion_video_qa.active_qa_version(),
        "sampling_version": motion_video_qa.FRAME_SAMPLING_VERSION,
        "vlm": {
            "source": vlm_identity.motion_acceptance_evaluator_version(
                motion_spec.MOTIONS[mid].qa_acceptance_spec
            )
        },
        "vlm_sampling": {
            "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
            "strategy": "adaptive_dense_salience",
        },
    })
    business_qa.attach_business_result(current, attempt_number=1, request_kind="MICRO")
    assert motion_video_service.candidate_qa_is_current(current, mid)

    stale = deepcopy(current)
    stale["motion_business_contract"]["acceptance"] = {
        "version": "motion-acceptance-legacy-v1",
        "motion_id": mid,
        "description": motion_spec.MOTIONS[mid].description,
    }
    assert not motion_video_service.candidate_qa_is_current(stale, mid)


def test_existing_pet_head_candidate_is_qa_only_reevaluable_with_adaptive_sampling():
    mid = "PET_HEAD"
    current = evidence(mid)
    current.update({
        "qa_version": motion_video_qa.active_qa_version(),
        "sampling_version": motion_video_qa.FRAME_SAMPLING_VERSION,
        "vlm": {
            "source": vlm_identity.motion_acceptance_evaluator_version(
                motion_spec.MOTIONS[mid].qa_acceptance_spec
            )
        },
        "vlm_sampling": {
            "version": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
            "strategy": "adaptive_dense_salience",
        },
    })
    business_qa.attach_business_result(current, attempt_number=1, request_kind="INTERACTION")
    assert motion_video_service.candidate_qa_is_current(current, mid)

    old_qa = deepcopy(current)
    old_qa["motion_business_contract"]["acceptance"] = {
        "version": "motion-acceptance-legacy-v1",
        "motion_id": mid,
        "description": motion_spec.MOTIONS[mid].description,
    }
    old_qa.pop("vlm_sampling")
    assert not motion_video_service.candidate_qa_is_current(old_qa, mid)


def test_motion_qa_contract_version_is_excluded_from_generation_fingerprint():
    generated = {
        "motion_builder": "same",
        "prompt": "same",
        "providers": ["same"],
        "motion_qa_contract": "motion-qa-contract-old",
        "sampling": "sampling-old",
        "vlm_motion_qa": "vlm-old",
        "motion_vlm_sampling": "motion-vlm-sampling-old",
    }
    requalified = {
        **generated,
        "motion_qa_contract": motion_spec.MOTION_QA_CONTRACT_VERSION,
        "sampling": motion_video_qa.FRAME_SAMPLING_VERSION,
        "vlm_motion_qa": vlm_identity.VLM_PREMIUM_MICRO_TEMPORAL_QA_VERSION,
        "motion_vlm_sampling": motion_video_service.MOTION_VLM_SAMPLING_VERSION,
    }
    assert motion_video_service.generation_versions(generated) == \
        motion_video_service.generation_versions(requalified)


def test_targeted_acceptance_prompt_schema_and_cache_invalidation(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    records = []
    def cached(**kwargs):
        records.append(kwargs)
        return {"requested_motion_occurs": "yes"}
    monkeypatch.setattr(vlm_identity, "_cached_qa_call", cached)
    contract = deepcopy(motion_spec.MOTIONS["COME_CLOSER"].qa_acceptance_spec)
    kwargs = dict(motion_description="GENERATION_ONLY_SENTINEL", motion_class="LOCOMOTION",
                  sample_fractions=[0, 1], tasks=[vlm_escalation.MOTION_VLM],
                  unresolved_questions=["motion_correctness"], expected_direction="TOWARD_CAMERA")
    for acceptance in (None, contract, {**contract, "description": "Changed acceptance behavior"}):
        vlm_identity.qa_motion_video([(b"first", "image/png"), (b"last", "image/png")],
                                     acceptance_spec=acceptance, **kwargs)
    legacy, beta, changed = records
    assert legacy["version"] != beta["version"]
    assert beta["params"] != changed["params"]
    prompt = beta["params"]["prompt"]
    assert "GENERATION_ONLY_SENTINEL" not in prompt
    assert "COME_CLOSER" in prompt and "HOME_IDLE" in prompt and "CLOSE_POSITION" in prompt
    assert "Unobservable foot-ground contact" in prompt
    assert "Walking, trotting, running" in prompt
    specs = vlm_identity._motion_targeted_specs(questions=["motion_correctness"],
        motion_description="GENERATION_ONLY_SENTINEL", motion_class="LOCOMOTION", expected_direction="TOWARD_CAMERA",
        interaction_type=None, allow_generated_hand=False, has_target=False, acceptance_spec=contract)
    schema, _ = specs[vlm_escalation.MOTION_VLM]
    assert {
        "severe_motion_defect", "critical_state_violation", "final_position_verified",
        "wrong_primary_action_observed",
    } <= set(schema["required"])


@pytest.mark.parametrize("field,expected", [("severe_motion_defect", "vlm_motion_integrity"),
                                            ("critical_state_violation", "vlm_critical_state")])
def test_semantic_severe_defect_is_mapped_to_hard_check(field, expected):
    contract = motion_spec.motion_snapshot(motion_spec.MOTIONS["COME_CLOSER"])
    result = motion_video_qa.apply_judgement(checks={"temporal_stability": "PASS"}, reasons=[],
        spec_contract=contract, vlm_qa={field: "yes", "requested_motion_occurs": "yes"},
        temporal_qa=None, structural_evidence={})
    assert result["checks"][expected] == "FAIL"


def test_return_home_critical_endpoint_uses_reference_aware_task(monkeypatch):
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    records = []
    def cached(**kwargs):
        records.append(kwargs)
        if kwargs["params"]["task"] == vlm_escalation.IDENTITY_ANATOMY_VLM:
            return {"critical_state_violation": "yes", "same_pet_all_frames": "yes",
                    "anatomy_plausible_all_frames": "yes"}
        return {"requested_motion_occurs": "yes", "severe_motion_defect": "no"}
    monkeypatch.setattr(vlm_identity, "_cached_qa_call", cached)
    acceptance = motion_spec.MOTIONS["RETURN_HOME"].qa_acceptance_spec
    result = vlm_identity.qa_motion_video([(b"first", "image/png"), (b"last", "image/png")],
        motion_description="GENERATION_ONLY", motion_class="LOCOMOTION", sample_fractions=[0, 1],
        reference_image=(b"START", "image/png"), target_image=(b"HOME", "image/png"),
        tasks=[vlm_escalation.IDENTITY_ANATOMY_VLM, vlm_escalation.MOTION_VLM],
        unresolved_questions=["identity_anatomy", "motion_correctness"], acceptance_spec=acceptance)
    assert result["critical_state_violation"] == "yes"
    identity, motion = records
    assert [data for data, _ in identity["images"]] == [b"first", b"last", b"START", b"HOME"]
    assert [data for data, _ in motion["images"]] == [b"first", b"last"]
    specs = vlm_identity._motion_targeted_specs(questions=["identity_anatomy", "motion_correctness"],
        motion_description="GENERATION_ONLY", motion_class="LOCOMOTION", expected_direction="AWAY_FROM_CAMERA",
        interaction_type=None, allow_generated_hand=False, has_target=True, acceptance_spec=acceptance)
    assert "critical_state_violation" in specs[vlm_escalation.IDENTITY_ANATOMY_VLM][0]["required"]
    assert "critical_state_violation" not in specs[vlm_escalation.MOTION_VLM][0]["required"]


def test_breathing_significant_drift_is_still_a_deterministic_hard_failure():
    spec = motion_spec.MOTIONS["BREATHING"]
    result = motion_video_qa.apply_judgement(
        checks={"temporal_stability": "PASS", "identity_over_time": "PASS"}, reasons=[],
        spec_contract=motion_spec.motion_snapshot(spec),
        vlm_qa={"same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes",
                "requested_motion_occurs": "yes", "single_pet": "yes", "duplicated_pet": "no",
                "human_present": "no", "scene_cut": "no", "major_flicker": "no",
                "camera_stable": "yes", "background_neutral": "yes", "unintended_large_motion": "no",
                "severe_motion_defect": "no", "critical_state_violation": "no", "final_position_verified": "yes"},
        temporal_qa={"verdict": "global_pulse", "reason": "drift", "metrics": {"translation_drift_frac_of_pet": 0.5}},
        structural_evidence={})
    business_qa.attach_business_result(result, attempt_number=1, request_kind="MICRO")
    assert result["business_signals"]["breathing_catastrophic_motion_integrity"] == "FAIL"
    assert result["decision"] == "FAIL"
