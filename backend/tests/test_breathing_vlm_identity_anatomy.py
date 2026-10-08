"""BREATHING VLM policy: one combined identity+anatomy call, failure classes, metric.

No network: the anthropic client is replaced by an in-process fake.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from backend.services import breathing_temporal_qa as bt
from backend.services import business_qa
from backend.services import motion_spec
from backend.services import motion_video_qa as qa
from backend.services import qa_shadow_telemetry
from backend.services import vlm_escalation
from backend.services import vlm_identity

_CONTRACT = motion_spec.motion_snapshot(motion_spec.MOTIONS["BREATHING"])
_THRESHOLDS = qa._default_temporal_thresholds()
_CALM = {
    "scale_range": 0.0061, "scale_oscillation": 0.0054, "scale_trend": 0.0025,
    "translation_drift_frac_of_pet": 0.0019, "torso_snr": 8.0,
    "head_to_torso_ratio": 0.9, "torso_energy_modulation": 0.6, "periodic_score": 0.4,
}
_SWAY = {**_CALM, "translation_drift_frac_of_pet": 0.0218}
_MEASURED = {"identity_over_time": "PASS", "temporal_stability": "PASS", "loop_return": "PASS"}
_IA_YES = {
    "same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes",
    "single_pet": "yes", "duplicated_pet": "no", "human_present": "no", "notes": "",
}


def _temporal(metrics: dict) -> dict:
    verdict, reason, advisories = bt._classify_temporal_metrics(metrics, _THRESHOLDS)
    return {"verdict": verdict, "reason": reason, "advisories": advisories,
            "metrics": metrics, "thresholds": _THRESHOLDS}


def _judge(metrics: dict, vlm: dict | None, measured: dict | None = None) -> dict:
    temporal = _temporal(metrics)
    judged = qa.apply_judgement(
        checks={**_MEASURED, **dict(measured or {})}, reasons=[], spec_contract=_CONTRACT,
        vlm_qa=vlm, temporal_qa=temporal, structural_evidence=None,
    )
    return {**judged, "temporal": temporal}


def _escalate(metrics: dict, measured: dict | None = None, motion_id: str = "BREATHING") -> dict:
    return vlm_escalation.should_call_vlm(
        stage="MOTION", qa_result=_judge(metrics, None, measured),
        request_kind="MICRO", motion_id=motion_id,
    )


def _pipeline(metrics: dict, vlm_answers: dict | None, *, attempt: int = 1):
    """Mirror motion_video_service: deterministic pass -> escalation -> VLM -> receipt."""

    escalation = _escalate(metrics)
    called = escalation["decision"] == vlm_escalation.CALL
    result = None
    if called and vlm_answers is not None:
        result = {
            **vlm_answers,
            "source": vlm_identity.VLM_TARGETED_QA_VERSION,
            "called_tasks": list(escalation["requested_tasks"]),
        }
    out = _judge(metrics, result) if called else _judge(metrics, None)
    out["vlm_escalation"] = vlm_escalation.finalize_vlm_escalation(
        escalation, called=called, result=result,
        failures=([] if result else [{"failure_class": "timeout"}]) if called else [],
    )
    receipt = business_qa.build_business_result(
        out, attempt_number=attempt, request_kind="MICRO", fallback_available=True
    )
    return out, receipt


# ── escalation policy ────────────────────────────────────────────────────


def test_clean_breathing_now_calls_combined_identity_anatomy_only():
    decision = _escalate(_CALM)

    assert decision["decision"] == vlm_escalation.CALL
    assert decision["requested_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert decision["unresolved_questions"] == ["identity_anatomy"]


def test_unresolved_motion_calls_combined_and_motion_tasks():
    decision = _escalate(_SWAY)

    assert decision["decision"] == vlm_escalation.CALL
    assert decision["requested_tasks"] == [
        vlm_escalation.IDENTITY_ANATOMY_VLM, vlm_escalation.MOTION_VLM,
    ]
    unrequested = set(decision["non_authoritative_checks_when_unrequested"])
    assert not {"vlm_same_pet", "vlm_anatomy", "vlm_composition", "vlm_motion"} & unrequested


def test_identity_uncertainty_does_not_add_a_separate_identity_call():
    decision = _escalate(_CALM, measured={"identity_over_time": "REVIEW"})

    assert vlm_escalation.IDENTITY_VLM not in decision["requested_tasks"]
    assert vlm_escalation.IDENTITY_ANATOMY_VLM in decision["requested_tasks"]


def test_deterministic_hard_fail_still_skips_the_vlm(monkeypatch):
    monkeypatch.setenv("BREATHING_QA_SCALE_RANGE_HARD", "1")  # restored hard gate
    decision = _escalate({**_CALM, "scale_range": 0.0569, "scale_oscillation": 0.05})

    assert decision["decision"] == vlm_escalation.SKIP
    assert "hard_fail:breathing_catastrophic_motion_integrity" in decision["reason_codes"]


def test_other_micro_motions_keep_their_escalation():
    qa_result = {
        "checks": {"identity_over_time": "PASS", "temporal_stability": "PASS"},
        "business_signals": {"motion_temporal_integrity": "PASS"},
        "motion_business_contract": dict(
            motion_spec.MOTIONS["BLINKING"].requirements["qa"]["business"]
        ),
    }
    decision = vlm_escalation.should_call_vlm(
        stage="MOTION", qa_result=qa_result, request_kind="MICRO", motion_id="BLINKING"
    )

    assert decision["requested_tasks"] == [vlm_escalation.MOTION_VLM]
    assert vlm_escalation.targeted_tasks("MOTION", ["full_stage_review"]) == [
        vlm_escalation.IDENTITY_VLM, vlm_escalation.ANATOMY_VLM, vlm_escalation.MOTION_VLM,
    ]


# ── authority outcomes ───────────────────────────────────────────────────


def test_vlm_yes_on_identity_and_anatomy_delivers_normally():
    out, receipt = _pipeline(_CALM, _IA_YES)

    assert out["checks"]["vlm_same_pet"] == "PASS" and out["checks"]["vlm_anatomy"] == "PASS"
    assert receipt["authority_evidence"]["INTEGRITY_HARD"]["vlm_same_pet"] == "PASS"
    assert receipt["authority_evidence"]["INTEGRITY_HARD"]["vlm_anatomy"] == "PASS"
    assert receipt["integrity_status"] == "PASS"
    assert receipt["delivery_action"] == "DELIVER"
    assert receipt["retry_action"] == "STOP"


@pytest.mark.parametrize(
    "override", [{"same_pet_all_frames": "no"}, {"anatomy_plausible_all_frames": "no"}]
)
def test_vlm_no_on_identity_or_anatomy_hard_fails(override):
    _, receipt = _pipeline(_CALM, {**_IA_YES, **override})

    assert receipt["integrity_status"] == "FAIL"
    assert receipt["delivery_action"] == "BLOCK"
    assert receipt["retry_action"] == "REGENERATE"
    _, second = _pipeline(_CALM, {**_IA_YES, **override}, attempt=2)
    assert second["retry_action"] == "FALLBACK"


@pytest.mark.parametrize(
    "answers",
    [
        None,  # call returned nothing
        {**_IA_YES, "same_pet_all_frames": "unknown"},
        {**_IA_YES, "anatomy_plausible_all_frames": "unknown"},
    ],
)
@pytest.mark.parametrize("metrics", [_CALM, _SWAY])
def test_unknown_or_unavailable_vlm_is_review_and_delivers_with_advisory(answers, metrics):
    out, receipt = _pipeline(metrics, answers)

    hard = receipt["authority_evidence"]["INTEGRITY_HARD"]
    assert "unknown" in (hard["vlm_same_pet"], hard["vlm_anatomy"])
    assert receipt["integrity_status"] == "REVIEW"
    assert receipt["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert receipt["retry_action"] == "STOP"
    assert receipt["terminal_state"] == business_qa.DELIVERED_GENERATED
    if answers is None:
        assert out["vlm_escalation"]["result_available"] is False
        assert out["vlm_escalation"]["result_failure_class"] == "timeout"
        assert out["vlm_escalation"]["failed_tasks"] == out["vlm_escalation"]["requested_tasks"]


def test_vlm_motion_no_stays_advisory_when_identity_and_anatomy_pass():
    out, receipt = _pipeline(_SWAY, {**_IA_YES, "requested_motion_occurs": "no"})

    assert out["checks"]["vlm_motion"] == "FAIL"
    assert receipt["authority_evidence"]["DIAGNOSTIC_ONLY"]["vlm_motion"] == "FAIL"
    assert receipt["integrity_status"] == "PASS"
    assert receipt["delivery_action"] == "DELIVER_WITH_ADVISORY"
    assert receipt["retry_action"] == "STOP"


# ── VLM client: combined call + failure classes + metric ─────────────────


class _Status(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code, self.message, self.request_id = status_code, message, "req_test"


class _Timeout(Exception):
    pass


def _install_anthropic(monkeypatch, behaviour):
    """behaviour(schema) -> response object, or raises."""

    calls: list[dict] = []

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return behaviour(kwargs["output_config"]["format"]["schema"])

    fake = types.SimpleNamespace(
        Anthropic=lambda *a, **k: types.SimpleNamespace(messages=_Messages()),
        APIStatusError=_Status,
        APITimeoutError=_Timeout,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("PET_VLM_IDENTITY_ENABLED", "1")
    monkeypatch.setenv(vlm_identity.VLM_QA_CACHE_ENV, "off")
    vlm_identity.reset_call_stats()
    return calls


def _response(payload=None, *, stop_reason="end_turn", text=None):
    block = types.SimpleNamespace(type="text", text=text if text is not None else json.dumps(payload))
    return types.SimpleNamespace(
        model="test-stub", stop_reason=stop_reason, content=[block],
        stop_details=types.SimpleNamespace(category="cyber"), _request_id="req_ok",
    )


def _ask(tasks, questions):
    return vlm_identity.qa_motion_video(
        [(b"f0", "image/jpeg"), (b"f1", "image/jpeg")],
        motion_description="calm breathing", motion_class="MICRO",
        sample_fractions=(0.0, 1.0), reference_image=(b"ref", "image/png"),
        tasks=tasks, unresolved_questions=questions,
    )


def test_identity_and_anatomy_are_one_combined_call(monkeypatch):
    calls = _install_anthropic(monkeypatch, lambda schema: _response(
        {key: _IA_YES.get(key, "unknown") for key in schema["properties"]}
    ))

    result = _ask([vlm_escalation.IDENTITY_ANATOMY_VLM], ["identity_anatomy"])

    assert len(calls) == 1
    assert set(calls[0]["output_config"]["format"]["schema"]["properties"]) == {
        "same_pet_all_frames", "anatomy_plausible_all_frames", "single_pet",
        "duplicated_pet", "human_present", "notes",
    }
    assert result["called_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert result["same_pet_all_frames"] == "yes"
    assert "requested_motion_occurs" not in result
    assert vlm_identity.call_stats()["returned_nothing_rate"] == 0.0


def _raise(exc):
    def behaviour(schema):
        raise exc
    return behaviour


@pytest.mark.parametrize(
    "behaviour,expected,detail",
    [
        (_raise(_Timeout("slow")), "timeout", {"error_type": "_Timeout"}),
        (_raise(_Status(529, "overloaded")), "api_error", {"status_code": 529, "request_id": "req_test"}),
        (_raise(RuntimeError("connection reset")), "api_error", {"error_type": "RuntimeError"}),
        (lambda schema: _response(stop_reason="refusal", text=""), "refusal", {"category": "cyber"}),
        (lambda schema: _response(stop_reason="max_tokens", text='{"same_pet'), "parse_failure",
         {"stop_reason": "max_tokens"}),
        (lambda schema: _response(text="[1, 2]"), "parse_failure", {"error_type": "non_object_json"}),
    ],
)
def test_failure_class_is_captured_and_persisted_on_the_receipt(monkeypatch, behaviour, expected, detail):
    _install_anthropic(monkeypatch, behaviour)
    escalation = _escalate(_CALM)

    with vlm_identity.capture_call_failures() as failures:
        result = _ask(escalation["requested_tasks"], escalation["unresolved_questions"])

    assert result is None
    assert [item["failure_class"] for item in failures] == [expected]
    for key, value in detail.items():
        assert failures[0][key] == value
    final = vlm_escalation.finalize_vlm_escalation(
        escalation, called=True, result=result, failures=failures
    )
    assert final["result_available"] is False
    assert final["result_failure_class"] == expected
    assert final["failed_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert final["called_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert final["answered_tasks"] == []
    stats = vlm_identity.call_stats()
    assert stats["calls"] == 1 and stats["returned_nothing"] == 1
    assert stats[f"returned_nothing:{expected}"] == 1
    assert stats["returned_nothing_rate"] == 1.0


def test_failures_are_captured_across_to_thread(monkeypatch):
    _install_anthropic(monkeypatch, _raise(_Timeout("slow")))

    async def run():
        with vlm_identity.capture_call_failures() as failures:
            await asyncio.to_thread(_ask, [vlm_escalation.IDENTITY_ANATOMY_VLM], ["identity_anatomy"])
        return failures

    assert [item["failure_class"] for item in asyncio.run(run())] == ["timeout"]


def test_partial_failure_keeps_answered_task_and_records_the_failed_one(monkeypatch):
    def behaviour(schema):
        if "requested_motion_occurs" in schema["properties"]:
            raise _Timeout("slow")
        return _response({key: _IA_YES.get(key, "unknown") for key in schema["properties"]})

    _install_anthropic(monkeypatch, behaviour)
    escalation = _escalate(_SWAY)

    with vlm_identity.capture_call_failures() as failures:
        result = _ask(escalation["requested_tasks"], escalation["unresolved_questions"])
    final = vlm_escalation.finalize_vlm_escalation(
        escalation, called=True, result=result, failures=failures
    )

    assert final["result_available"] is True
    assert final["answered_tasks"] == [vlm_escalation.IDENTITY_ANATOMY_VLM]
    assert final["failed_tasks"] == [vlm_escalation.MOTION_VLM]
    assert final["result_failure_class"] == "timeout"
    assert vlm_identity.call_stats()["returned_nothing_rate"] == 0.5


def test_receipt_without_failure_detail_reports_no_result():
    escalation = _escalate(_CALM)
    final = vlm_escalation.finalize_vlm_escalation(escalation, called=True, result=None)

    assert final["result_failure_class"] == "no_result"
    skipped = vlm_escalation.finalize_vlm_escalation(
        {**escalation, "decision": "SKIP", "requested_tasks": []}, called=False, result=None
    )
    assert skipped["failed_tasks"] == [] and skipped["result_failure_class"] is None


def test_shadow_telemetry_records_returned_nothing_metric():
    out, _ = _pipeline(_SWAY, None)
    run = types.SimpleNamespace(
        id="run-1", user_id="u", pet_id="p", motion_id="BREATHING", request_kind="FREE_HOME"
    )
    candidate = types.SimpleNamespace(
        id="cand-1", qa_result=out, decision="REVIEW", attempt=1,
        provider="seedance", model="m", selected=True, generation_metadata={},
    )

    row = qa_shadow_telemetry._candidate_row(
        run=run, stage="MOTION", stage_role=None, parent_id="ver-1", candidate=candidate
    )

    assert row["metadata"]["vlm_tasks_requested"] == 2
    assert row["metadata"]["vlm_tasks_returned_nothing"] == 2
    assert row["metadata"]["vlm_failure_class"] == "timeout"
