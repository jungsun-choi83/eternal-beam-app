"""breathing-v2 authority mapping — metric fixtures only (no rendered video).

Whole-body sway / drift / settling / scale_trend are QUALITY_ADVISORY. The only
BREATHING-specific deterministic hard gate is the catastrophic scale pulse
(PROVISIONAL threshold) plus an UNVALIDATED drift backstop.
"""

from __future__ import annotations

import copy

import pytest

from backend.services import business_qa
from backend.services import motion_spec
from backend.services import motion_video_qa as qa

_CONTRACT = motion_spec.motion_snapshot(motion_spec.MOTIONS["BREATHING"])
_THRESHOLDS = qa._default_temporal_thresholds()

#: A calm, torso-local breath (modelled on stored PASS candidates).
_CALM = {
    "scale_range": 0.0061,
    "scale_oscillation": 0.0054,
    "scale_trend": 0.0025,
    "translation_drift_frac_of_pet": 0.0019,
    "torso_snr": 8.0,
    "head_to_torso_ratio": 0.9,
    "torso_energy_modulation": 0.6,
    "periodic_score": 0.4,
}
_VLM_YES = {
    "same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes",
    "requested_motion_occurs": "yes", "unintended_large_motion": "no",
    "duplicated_pet": "no", "scene_cut": "no", "human_present": "no",
    "major_flicker": "no", "camera_stable": "yes", "background_neutral": "yes",
    "single_pet": "yes", "ends_in_target_pose": "unknown",
}
_MEASURED = {
    "identity_over_time": "PASS",
    "temporal_stability": "PASS",
    "loop_return": "PASS",
}


def _receipt(metric_overrides=None, *, vlm=_VLM_YES, attempt=1, measured=None):
    from backend.services import breathing_temporal_qa as bt

    metrics = {**_CALM, **dict(metric_overrides or {})}
    verdict, reason, advisories = bt._classify_temporal_metrics(metrics, _THRESHOLDS)
    temporal = {
        "verdict": verdict, "reason": reason, "advisories": advisories,
        "metrics": metrics, "thresholds": _THRESHOLDS,
    }
    judged = qa.apply_judgement(
        checks={**_MEASURED, **dict(measured or {})},
        reasons=[],
        spec_contract=_CONTRACT,
        vlm_qa=vlm,
        temporal_qa=temporal,
        structural_evidence=None,
    )
    out = {**judged, "temporal": temporal}
    return out, business_qa.build_business_result(
        out, attempt_number=attempt, request_kind="MICRO", fallback_available=True
    )


def _assert_delivered(receipt):
    assert receipt["authority_profile"] == "breathing-v2"
    assert receipt["integrity_status"] != "FAIL"
    assert receipt["delivery_action"] in ("DELIVER", "DELIVER_WITH_ADVISORY")
    assert receipt["retry_action"] == "STOP"
    assert receipt["terminal_state"] == business_qa.DELIVERED_GENERATED


@pytest.mark.parametrize(
    "name,overrides",
    [
        # whole-body sway: drift above the 0.02 analyzer gate
        ("slight_sway", {"translation_drift_frac_of_pet": 0.0218}),
        # forward/back: scale range above the 0.02 analyzer gate
        ("mild_forward_back", {"scale_range": 0.0223, "scale_oscillation": 0.0200}),
        # posture settling: monotonic scale trend
        ("small_scale_trend", {"scale_range": 0.0155, "scale_oscillation": 0.0036, "scale_trend": 0.0134}),
        # largest human-accepted drift / scale_range in the stored set
        ("accepted_max_drift", {"scale_range": 0.0473, "scale_oscillation": 0.0448, "translation_drift_frac_of_pet": 0.1056}),
    ],
)
def test_normal_and_moderate_whole_body_motion_delivers_with_advisory(name, overrides):
    out, receipt = _receipt(overrides)

    assert out["temporal"]["verdict"] == "global_pulse", name
    assert out["checks"]["temporal_breathing"] == "FAIL", name  # telemetry unchanged
    assert out["business_signals"]["breathing_global_motion_integrity"] == "REVIEW", name
    assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == "PASS", name
    assert receipt["authority_evidence"]["QUALITY_ADVISORY"][
        "breathing_global_motion_integrity"
    ] == "REVIEW"
    assert receipt["authority_evidence"]["DIAGNOSTIC_ONLY"]["temporal_breathing"] == "FAIL"
    assert receipt["integrity_status"] == "PASS", name
    assert receipt["delivery_action"] == "DELIVER_WITH_ADVISORY", name
    _assert_delivered(receipt)


def test_imperfect_periodicity_delivers_with_advisory():
    out, receipt = _receipt({"periodic_score": 0.10, "torso_energy_modulation": 0.20})

    assert out["temporal"]["verdict"] == "breathing_detected"
    assert out["business_signals"]["breathing_periodicity"] == "REVIEW"
    assert out["business_signals"]["breathing_modulation"] == "REVIEW"
    assert receipt["integrity_status"] == "PASS"
    assert receipt["delivery_action"] == "DELIVER_WITH_ADVISORY"
    _assert_delivered(receipt)


def test_scale_range_boundary_just_below_and_above_catastrophic_threshold(monkeypatch):
    monkeypatch.setenv("BREATHING_QA_SCALE_RANGE_HARD", "1")  # restored hard gate
    below, below_receipt = _receipt({"scale_range": 0.0519, "scale_oscillation": 0.05})
    above, above_receipt = _receipt({"scale_range": 0.0520, "scale_oscillation": 0.05})

    assert below["business_signals"]["breathing_catastrophic_motion_integrity"] == "PASS"
    _assert_delivered(below_receipt)
    assert above["business_signals"]["breathing_catastrophic_motion_integrity"] == "FAIL"
    assert above_receipt["integrity_status"] == "FAIL"
    assert above_receipt["delivery_action"] == "BLOCK"
    assert above_receipt["retry_action"] == "REGENERATE"


def test_whole_body_scale_pulse_like_48e31aff_hard_fails_without_vlm(monkeypatch):
    monkeypatch.setenv("BREATHING_QA_SCALE_RANGE_HARD", "1")  # restored hard gate
    # Stored metrics of the human-rejected candidate; VLM disabled.
    out, receipt = _receipt(
        {"scale_range": 0.05686, "translation_drift_frac_of_pet": 0.00331,
         "torso_snr": 6.789, "head_to_torso_ratio": 2.192},
        vlm=None,
    )

    assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == "FAIL"
    assert receipt["authority_evidence"]["INTEGRITY_HARD"][
        "breathing_catastrophic_motion_integrity"
    ] == "FAIL"
    assert receipt["integrity_status"] == "FAIL"
    assert receipt["delivery_action"] == "BLOCK"
    assert receipt["retry_action"] == "REGENERATE"
    _, second = _receipt({"scale_range": 0.05686}, vlm=None, attempt=2)
    assert second["retry_action"] == "FALLBACK"


def test_catastrophic_finding_hard_fails_even_when_every_other_check_passes(monkeypatch):
    monkeypatch.setenv("BREATHING_QA_SCALE_RANGE_HARD", "1")  # restored hard gate
    out, receipt = _receipt({"scale_range": 0.0569, "scale_oscillation": 0.05})

    others = {
        name: status
        for group in receipt["authority_evidence"].values()
        for name, status in group.items()
        if name not in (
            "breathing_catastrophic_motion_integrity",
            "breathing_global_motion_integrity",
            "breathing_motion_correctness",
            "temporal_breathing",
        )
    }
    assert others and all(status == "PASS" for status in others.values())
    assert receipt["integrity_status"] == "FAIL"
    assert receipt["retry_action"] == "REGENERATE"


def test_unvalidated_drift_backstop_boundary():
    below, below_receipt = _receipt({"translation_drift_frac_of_pet": 0.199})
    above, above_receipt = _receipt({"translation_drift_frac_of_pet": 0.20})

    assert below["business_signals"]["breathing_catastrophic_motion_integrity"] == "PASS"
    _assert_delivered(below_receipt)
    assert above["business_signals"]["breathing_catastrophic_motion_integrity"] == "FAIL"
    assert above_receipt["retry_action"] == "REGENERATE"


@pytest.mark.parametrize(
    "scale_range,expected_signal",
    [(0.030, "PASS"), (0.040, "PASS"), (0.045, "REVIEW")],
)
def test_vlm_unknown_never_hard_fails_and_marks_review_above_0_04(scale_range, expected_signal):
    out, receipt = _receipt({"scale_range": scale_range, "scale_oscillation": 0.03}, vlm=None)

    assert out["checks"]["vlm_same_pet"] == "unknown"
    assert out["checks"]["vlm_anatomy"] == "unknown"
    assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == expected_signal
    assert receipt["integrity_status"] == "REVIEW"
    assert receipt["delivery_action"] == "DELIVER_WITH_ADVISORY"
    _assert_delivered(receipt)


def test_vlm_confirmed_elevated_scale_is_not_review():
    out, receipt = _receipt({"scale_range": 0.045, "scale_oscillation": 0.03})

    assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == "PASS"
    assert receipt["integrity_status"] == "PASS"


@pytest.mark.parametrize(
    "override", [{"same_pet_all_frames": "no"}, {"anatomy_plausible_all_frames": "no"}]
)
def test_vlm_no_on_identity_or_anatomy_remains_hard(override):
    _, receipt = _receipt(vlm={**_VLM_YES, **override})

    assert receipt["integrity_status"] == "FAIL"
    assert receipt["retry_action"] == "REGENERATE"


def test_temporal_scene_cut_remains_hard():
    _, receipt = _receipt(measured={"temporal_stability": "FAIL"})

    assert receipt["authority_evidence"]["INTEGRITY_HARD"]["motion_temporal_integrity"] == "FAIL"
    assert receipt["retry_action"] == "REGENERATE"


def test_missing_metrics_never_hard_fail():
    status, evidence = qa._breathing_catastrophic_motion({}, {"verdict": "unmeasurable"})

    assert status == "PASS"
    assert evidence["scale_range"] is None


def test_v1_profile_contract_keeps_legacy_hard_gate():
    # Rollback path (BREATHING_AUTHORITY_PROFILE=breathing-v1) and historical
    # receipts: a persisted v1 contract is evaluated with v1 semantics.
    contract = copy.deepcopy(_CONTRACT)
    business = contract["requirements"]["qa"]["business"]
    business["authority_profile"] = business_qa.BREATHING_AUTHORITY_V1
    business["check_authority"]["breathing_global_motion_integrity"] = "INTEGRITY_HARD"
    business["check_authority"].pop("breathing_catastrophic_motion_integrity")
    business["domains"].pop("catastrophic_motion_integrity")
    metrics = {**_CALM, "translation_drift_frac_of_pet": 0.0218}
    temporal = {"verdict": "global_pulse", "reason": "drift", "advisories": [], "metrics": metrics}
    judged = qa.apply_judgement(
        checks=dict(_MEASURED), reasons=[], spec_contract=contract,
        vlm_qa=_VLM_YES, temporal_qa=temporal, structural_evidence=None,
    )
    receipt = business_qa.build_business_result(
        {**judged, "temporal": temporal}, attempt_number=1, request_kind="MICRO"
    )

    assert "breathing_catastrophic_motion_integrity" not in judged["business_signals"]
    assert judged["business_signals"]["breathing_global_motion_integrity"] == "FAIL"
    assert receipt["authority_profile"] == "breathing-v1"
    assert receipt["retry_action"] == "REGENERATE"


def test_other_motion_authority_profiles_are_unchanged():
    breathing_only = {
        "breathing_motion_correctness", "breathing_periodicity", "breathing_modulation",
        "breathing_head_motion", "breathing_global_motion_integrity",
        "breathing_catastrophic_motion_integrity", "breathing_composition_integrity",
    }
    for motion_id, spec in motion_spec.MOTIONS.items():
        if motion_id == "BREATHING":
            continue
        business = spec.requirements["qa"]["business"]
        assert business["authority_profile"] is None, motion_id
        assert not breathing_only & set(business["check_authority"]), motion_id
        assert "catastrophic_motion_integrity" not in business["domains"], motion_id
        assert business["check_authority"]["vlm_motion"] == "INTEGRITY_HARD", motion_id
        assert business["check_authority"]["temporal_stability"] == "QUALITY_ADVISORY", motion_id

    blink = business_qa.build_business_result(
        {
            "checks": {"vlm_motion": "FAIL"},
            "motion_business_contract": dict(
                motion_spec.MOTIONS["BLINKING"].requirements["qa"]["business"]
            ),
        },
        attempt_number=1,
        request_kind="MICRO",
    )
    assert blink["authority_profile"] == "motion-class-v1:micro"
    assert blink["retry_action"] == "REGENERATE"


def test_scale_range_gate_is_temporarily_advisory_by_default(monkeypatch):
    """TEMPORARY: scale_range over 0.052 is REVIEW (deliverable), not a hard FAIL."""
    monkeypatch.delenv("BREATHING_QA_SCALE_RANGE_HARD", raising=False)
    # Stored metrics of 48e31aff (size pulse) and of the accepted MiniMax clip 5c4b9e16.
    for scale_range in (0.05686, 0.10595):
        out, receipt = _receipt({"scale_range": scale_range}, vlm=None)
        assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == "REVIEW"
        assert receipt["integrity_status"] != "FAIL"
        assert receipt["delivery_action"] != "BLOCK"


def test_drift_backstop_stays_hard_while_scale_gate_is_advisory(monkeypatch):
    monkeypatch.delenv("BREATHING_QA_SCALE_RANGE_HARD", raising=False)
    out, receipt = _receipt({"scale_range": 0.10595, "translation_drift_frac_of_pet": 0.20})
    assert out["business_signals"]["breathing_catastrophic_motion_integrity"] == "FAIL"
    assert receipt["integrity_status"] == "FAIL"
    assert receipt["delivery_action"] == "BLOCK"
