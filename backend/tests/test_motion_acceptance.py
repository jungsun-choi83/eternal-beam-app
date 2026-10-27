"""Acceptance policy regression tests. All VLM/provider responses are simulated."""
from copy import deepcopy
from dataclasses import replace

import pytest

from backend.services import business_qa, motion_spec, motion_video_qa, motion_video_service
from backend.services import vlm_escalation, vlm_identity


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
    for mid, spec in motion_spec.MOTIONS.items():
        assert spec.qa_acceptance_spec["motion_id"] == mid
        if mid not in {"BREATHING", "COME_CLOSER", "RETURN_HOME"}:
            assert spec.qa_acceptance_spec["description"] == spec.description
            assert "review_policy" not in spec.qa_acceptance_spec
    spec = motion_spec.MOTIONS["COME_CLOSER"]
    changed = replace(spec, description="An impossibly perfect running gait")
    assert changed.qa_acceptance_spec == spec.qa_acceptance_spec
    assert motion_spec.MOTIONS["RUN"].requirements["qa"]["business"]["check_authority"]["vlm_locomotion_form"] == "INTEGRITY_HARD"
    assert "runs naturally" in spec.description


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
    assert {"severe_motion_defect", "critical_state_violation", "final_position_verified"} <= set(schema["required"])


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
