"""
무결성 게이트 (MOTION_QA_SEVERITY_GATE=integrity_only) — "지금은 무결성 문제만 전달을 막는다".

계약:
  * classify_reason: 열거된 모든 사유 → INTEGRITY | COSMETIC. 모르는 사유는 INTEGRITY.
  * 게이트 off(기본): 이전과 바이트 단위 동일 — qa_result 형식, 선택, 발행 거절 코드.
  * 게이트 on: 무결성 사유 없는 REVIEW/FAIL 후보가 selected 되고 발행·포장·재생된다.
    결정/status 는 고쳐 쓰지 않는다. 무결성 사유가 하나라도 있으면 절대 발행되지 않는다.
  * 실행은 FAILED 대신 PUBLISHED 로 끝난다; 전달 못 하면 사용자용 문장으로 실패한다.
프로바이더는 전부 가짜 — 실 결제 호출 없음.
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import motion_delivery_service as delivery
from backend.services import motion_publication_service as publication
from backend.services import motion_video_qa as qa
from backend.services import motion_video_service as mv
from backend.services import pet_generation_run_service as runs
from backend.services import pet_registry

from . import test_phase7c_generation_runs as p7c
from .test_canonical_pet_builder import GOOD
from .test_motion_delivery import _isolated  # noqa: F401  (autouse: 스토리지/서명 스텁 + 목업 리셋)
from .test_motion_delivery import _package as _package_delivery
from .test_motion_delivery import _seed as _seed_delivery
from .test_motion_delivery import PET as DPET, USER as DUSER, VERSION_ID as DVERSION
from .test_phase7c_generation_runs import _clean  # noqa: F401  (autouse: 실행/레퍼런스 목업 리셋)
from .test_motion_video_generation import (  # noqa: F401  (fixtures: _mock_backend, storage)
    VLM_MV_OK,
    FakeVideoProvider,
    _build_motion,
    _mock_backend,
    _prepare_pipeline,
    install_mv_vlm,
    storage,
)
from .test_pet_reference_sets import PET, USER


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch):
    monkeypatch.delenv(qa.SEVERITY_GATE_ENV, raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_TEST_AUTH", "1")
    publication.__reset_for_tests()
    delivery.__reset_for_tests() if hasattr(delivery, "__reset_for_tests") else None
    runs.__reset_for_tests()
    yield
    publication.__reset_for_tests()
    runs.__reset_for_tests()


def _on(monkeypatch):
    monkeypatch.setenv(qa.SEVERITY_GATE_ENV, "integrity_only")


# ══════════════════════════════════════════════════════════════════════════
# 1. 사유 → 심각도 매핑 (코드 + 91행 실측에서 나온 모든 사유)
# ══════════════════════════════════════════════════════════════════════════

INTEGRITY_REASONS = [
    "identity_drift worst_frame 0.082 < 0.2",
    "identity_mean 0.15 < 0.2",
    "identity_crater_frame 0.18 < 0.2",
    "identity borderline worst_frame 0.328",
    "identity borderline mean 0.41",
    "no_comparable_frames",
    "frame_sampling_unavailable",
    "vlm:same_pet_all_frames=no",
    "vlm:anatomy_plausible_all_frames=no",
    "vlm_composition_contaminated",
    "vlm_qa_unavailable",
    "scene_cut_or_swap adjacent 0.12 < 0.2",
    "structural_body_length_class_strong_contradiction 0.778 >= 0.7",
    "structural_leg_length_class_strong_contradiction 1.0 >= 0.7",
    "anatomy_limb_count_or_placement_corrupted",
    "anatomy_joint_implausible",
    "anatomy_severe_body_deformation",
    "output_conformance:aspect_mismatch requested 9:16 got 1440x1440",
    "output_conformance:audio_stream_present_despite_audio_false",
]
COSMETIC_REASONS = [
    "temporal_global_pulse: scale_range 0.0214 > 0.02",
    "temporal_global_pulse: drift 0.0232 > 0.02",
    "temporal_global_pulse: monotonic_scale_sag trend 0.0134 > osc 0.0036",
    "temporal_global_pulse_borderline: scale_range 0.0214 > 0.02 (worst 1.07x, band 0.15)",
    "temporal_unlocalized_motion: head_to_torso 2.011 > 1.6",
    "temporal_no_breathing: amplitude_below_visible_floor osc 0.00231 < 0.003",
    "vlm_motion_resolved_by_temporal_evidence",
    "vlm:requested_motion_occurs=no",
    "vlm_unintended_large_motion",
    "vlm_unintended_large_motion_contradicted_by_temporal_metrics",
    "vlm_temporal_or_background_issue",
    "vlm_temporal_or_background_issue_advisory_temporal_metrics_pass",
    "vlm_did_not_reach_target",
    "start_pose_not_reached 0.2 < 0.25",
    "target_pose_not_reached 0.2 < 0.25",
    "target_pose_borderline 0.463",
    "start_pose_unmeasurable",
    "flicker adjacent 0.401",
    "loop_ssim_below_threshold 0.6 < 0.65",
    "loop_return_unmeasurable",
    "end_pose_far_from_start 0.817 < 0.85",
    "locomotion_identity_resolved mean 0.4 + adjacent_min 0.6 + vlm_same_pet",
    "structural_body_length_class_drift 1/9",
    "structural_leg_length_class_insufficient_visibility",
    "structural_ear_form_insufficient_visibility",
    "structural_body_length_class_pose_dependent_change_advisory 0.8",
    "structural_evidence_unavailable",
    "structural_morphology_evidence_insufficient",
    "structural_heuristic_borderline_vlm_anatomy_ok worst 1.11x band 0.15",
    "anatomy_joint_borderline",
    "anatomy_body_deformation_review",
    "anatomy_body_deformation_pose_transition_advisory",
    "advisory_checks_not_blocking:anatomy_body_deformation,structural_morphology_consistency",
    "advisory_structural_fail_not_blocking: anatomy_body_deformation",
    "vlm_evidence_from_stored_checks",
    "output_conformance:resolution_below_requested 480 < 720",
    "output_conformance:duration_off requested 5s got 8.0s",
]


@pytest.mark.parametrize("reason", INTEGRITY_REASONS)
def test_integrity_reasons(reason):
    assert qa.classify_reason(reason) == qa.SEVERITY_INTEGRITY


@pytest.mark.parametrize("reason", COSMETIC_REASONS)
def test_cosmetic_reasons(reason):
    assert qa.classify_reason(reason) == qa.SEVERITY_COSMETIC


@pytest.mark.parametrize("reason", ["", None, "brand_new_rule_v11_something", "vlm:some_future_flag=no", "structural_new_axis_weird"])
def test_unknown_or_empty_reason_defaults_to_integrity(reason):
    assert qa.classify_reason(reason) == qa.SEVERITY_INTEGRITY


def test_every_reason_emitted_by_the_code_is_mapped():
    """motion_video_qa 가 만드는 사유 접두어가 규칙표에 전부 있다 — 새 사유가 조용히 INTEGRITY 로만 남지 않게."""
    import re
    src = open(qa.__file__, encoding="utf-8").read()
    literals = set(re.findall(r'reasons\.append\(\s*f?"([a-zA-Z_:][^"{]*)', src))
    literals |= set(re.findall(r'reasons\.append\(\s*\n\s*f?"([a-zA-Z_:][^"{]*)', src))
    unmapped = []
    for lit in literals:
        if lit.endswith("_") or lit.startswith("vlm:") or lit.endswith(": "):
            continue  # f-string 머리("temporal_", "structural_", "vlm:{key}=") — 구체 값은 위 매개변수 테스트가 검증
        probes = [lit.replace("{label}", "target_pose")]
        probes.append("output_conformance:" + probes[0])   # verify_output_conformance 의 사유는 호출부가 접두어를 붙인다
        hit = any(
            (kind == "prefix" and probe.startswith(pat)) or (kind == "regex" and re.match(pat, probe))
            for probe in probes
            for kind, pat, _ in qa.REASON_SEVERITY_RULES
        )
        if not hit:
            unmapped.append(lit)
    assert not unmapped, unmapped


def test_severity_summary_flags_integrity_check_without_reason():
    summary = qa.severity_summary({"reasons": ["flicker adjacent 0.4"], "checks": {"vlm_same_pet": "FAIL"}})
    assert summary["cosmetic"] == ["flicker adjacent 0.4"]
    assert summary["integrity"] == ["check:vlm_same_pet=FAIL"]


# ══════════════════════════════════════════════════════════════════════════
# 2. is_publishable / 게이트 모드
# ══════════════════════════════════════════════════════════════════════════


def test_gate_mode_defaults_off_and_unknown_is_off(monkeypatch):
    assert qa.severity_gate_mode() == "off"
    monkeypatch.setenv(qa.SEVERITY_GATE_ENV, "everything")
    assert qa.severity_gate_mode() == "off"
    monkeypatch.setenv(qa.SEVERITY_GATE_ENV, "Integrity_Only")
    assert qa.severity_gate_mode() == "integrity_only"


def test_is_publishable_matrix(monkeypatch):
    cosmetic = {"decision": "FAIL", "reasons": ["vlm_unintended_large_motion"], "checks": {"vlm_composition": "FAIL"}}
    integrity = {"decision": "FAIL", "reasons": ["vlm:same_pet_all_frames=no"], "checks": {"vlm_same_pet": "FAIL"}}
    review = {"decision": "REVIEW", "reasons": [], "checks": {"vlm_motion": "unknown"}}
    assert qa.is_publishable({"decision": "PASS", "reasons": [], "checks": {}}) is True
    assert qa.is_publishable(cosmetic) is False and qa.is_publishable(review) is False   # off
    _on(monkeypatch)
    assert qa.is_publishable(cosmetic) is True
    assert qa.is_publishable(review) is True
    assert qa.is_publishable(integrity) is False
    assert qa.is_publishable({"decision": "ERROR", "reasons": [], "checks": {}}) is False
    assert qa.is_publishable({"decision": "FAIL", "reasons": ["totally_new_reason"], "checks": {}}) is False
    # 인자 모드가 환경보다 우선
    assert qa.is_publishable(cosmetic, mode="off") is False


def test_choose_publishable_candidate_rule():
    def cand(cid, decision, reasons, snr=None, osc=None, sim=0.9, attempt=1):
        temporal = {"metrics": {"torso_snr": snr, "scale_oscillation": osc}} if snr is not None else None
        return {"id": cid, "decision": decision, "attempt": attempt,
                "qa_result": {"decision": decision, "reasons": reasons, "checks": {}, "identity_similarity": sim, "temporal": temporal}}
    a = cand("a", "FAIL", ["vlm_unintended_large_motion"], snr=9.0, osc=0.02)
    b = cand("b", "REVIEW", ["temporal_global_pulse: scale_range 0.021 > 0.02", "flicker adjacent 0.4"], snr=3.0)
    c = cand("c", "REVIEW", ["temporal_global_pulse: scale_range 0.021 > 0.02"], snr=5.0, osc=0.01)
    d = cand("d", "REVIEW", ["temporal_global_pulse: drift 0.021 > 0.02"], snr=8.0, osc=0.005)
    e = cand("e", "FAIL", ["vlm:same_pet_all_frames=no"], snr=99.0)
    f = cand("f", "ERROR", [])
    assert mv.choose_publishable_candidate([a, b, c, d, e, f], mode="integrity_only")["id"] == "d"   # REVIEW, 1 cosmetic, best snr
    assert mv.choose_publishable_candidate([a, b, e], mode="integrity_only")["id"] == "b"           # REVIEW beats FAIL
    assert mv.choose_publishable_candidate([a, e, f], mode="integrity_only")["id"] == "a"
    assert mv.choose_publishable_candidate([e, f], mode="integrity_only") is None
    assert mv.choose_publishable_candidate([a, b, c, d], mode="off") is None


# ══════════════════════════════════════════════════════════════════════════
# 3. 빌더: off 는 이전과 동일, on 은 cosmetic-only 를 selected 로 고른다
# ══════════════════════════════════════════════════════════════════════════

COSMETIC_VLM = {**VLM_MV_OK, "unintended_large_motion": "yes"}      # MICRO → vlm_composition FAIL (외관)
INTEGRITY_VLM = {**VLM_MV_OK, "same_pet_all_frames": "no"}           # 무결성 FAIL


def test_business_contract_delivers_cosmetic_only_fail_even_with_legacy_gate_off(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, COSMETIC_VLM)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE and v.selected_candidate_id is not None
    cand = v.candidates[0]
    assert cand.decision == "FAIL" and cand.selected is True
    assert "severity" not in cand.qa_result, "게이트 off 에서는 qa_result 형식도 이전과 같다"
    assert cand.qa_result["reasons"] == ["vlm_unintended_large_motion"]
    assert cand.qa_result["business_qa"]["delivery_action"] == "DELIVER_WITH_ADVISORY"
    pub = _run(publication.publish_breathing(
        user_id=USER, pet_id=PET, motion_version_id=v.id, sign_fn=lambda a: "https://x"
    ))
    assert pub.selected_candidate_id == cand.id


def test_gate_on_cosmetic_only_fail_is_selected_and_published_without_rewriting_decision(storage, monkeypatch):
    _on(monkeypatch)
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, COSMETIC_VLM)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    cand = v.candidates[0]
    assert cand.decision == "FAIL", "결정은 고쳐 쓰지 않는다"
    assert v.status == mv.STATUS_COMPLETE
    assert v.selected_candidate_id == cand.id and cand.selected is True
    assert v.selection_reason.startswith("best business-deliverable candidate")
    assert cand.qa_result["severity"]["publishable"] is True
    assert cand.qa_result["severity"]["integrity"] == []
    assert cand.qa_result["severity"]["cosmetic"] == ["vlm_unintended_large_motion"]
    assert v.qa_summary["business_qa"]["selected"]["delivery_action"] == "DELIVER_WITH_ADVISORY"

    pub = _run(publication.publish_breathing(user_id=USER, pet_id=PET, motion_version_id=v.id, sign_fn=lambda a: "https://storage.test/x"))
    assert pub.selected_candidate_id == cand.id
    assert pub.qa_decision == "FAIL" and pub.qa_reasons == ["vlm_unintended_large_motion"] and pub.severity_gate == "integrity_only"
    row = next(c for c in mv._MOCK_CANDIDATES if c["id"] == cand.id)
    assert row["decision"] == "FAIL"
    record = row["generation_metadata"]["publication"]
    assert record["publication_id"] == pub.publication_id and record["cosmetic"] == ["vlm_unintended_large_motion"]
    assert record["integrity"] == [] and record["gate"] == "integrity_only"
    pet = _run(pet_registry.get(PET))
    assert pet and pet.breathing_object_path == cand.raw_video_path


def test_gate_on_integrity_reason_never_publishes(storage, monkeypatch):
    _on(monkeypatch)
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, INTEGRITY_VLM)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    cand = v.candidates[0]
    assert cand.decision == "FAIL" and cand.selected is False and v.selected_candidate_id is None
    assert cand.qa_result["severity"]["publishable"] is False
    assert "vlm:same_pet_all_frames=no" in cand.qa_result["severity"]["integrity"]
    with pytest.raises(publication.MotionPublicationError) as e:
        _run(publication.publish_breathing(user_id=USER, pet_id=PET, motion_version_id=v.id, sign_fn=lambda a: "https://x"))
    assert e.value.code in ("SELECTED_CANDIDATE_MISSING", "CANDIDATE_NOT_PASS")
    assert publication._MOCK_PUBLICATIONS == []


def test_gate_on_pass_candidate_still_wins_over_cosmetic(storage, monkeypatch):
    _on(monkeypatch)
    h, _ = _prepare_pipeline(monkeypatch, storage)
    install_mv_vlm(monkeypatch, VLM_MV_OK)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    assert v.status == mv.STATUS_COMPLETE and v.candidates[0].decision == "PASS"
    assert v.selection_reason.startswith("best business-deliverable candidate")


def test_publication_gate_rejects_integrity_fail_even_when_marked_selected(monkeypatch):
    """운영 실수 방어: 게이트 on 이어도 무결성 사유가 있는 selected 후보는 발행 거절."""
    _on(monkeypatch)
    _seed_delivery(decision="FAIL")
    mv._MOCK_VERSIONS[0]["status"] = "failed"
    mv._MOCK_CANDIDATES[0]["qa_result"] = {"decision": "FAIL", "reasons": ["vlm:anatomy_plausible_all_frames=no"], "checks": {"vlm_anatomy": "FAIL"}}
    with pytest.raises(publication.MotionPublicationError) as e:
        _run(publication.publish_breathing(user_id=DUSER, pet_id=DPET, motion_version_id=DVERSION, sign_fn=lambda a: "https://x"))
    assert e.value.code == "CANDIDATE_NOT_PASS"
    assert publication._MOCK_PUBLICATIONS == []


# ══════════════════════════════════════════════════════════════════════════
# 4. 포장 / 재생: cosmetic-only FAIL 은 게이트 on 에서만 통과
# ══════════════════════════════════════════════════════════════════════════


def _cosmetic_fail_qa():
    return {"decision": "FAIL", "reasons": ["temporal_global_pulse: scale_range 0.021 > 0.02"],
            "checks": {"temporal_breathing": "FAIL"}}


def test_cosmetic_fail_is_packageable_and_playable_only_with_gate_on(monkeypatch):
    _seed_delivery(decision="FAIL")
    mv._MOCK_CANDIDATES[0]["qa_result"] = _cosmetic_fail_qa()
    with pytest.raises(delivery.MotionDeliveryError) as e:
        _package_delivery()
    assert e.value.code == "CANDIDATE_NOT_PACKAGEABLE"

    _on(monkeypatch)
    result, _ = _package_delivery()
    assert result.delivery_format == "packed_alpha"
    assert mv._MOCK_CANDIDATES[0]["decision"] == "FAIL"
    playback = _run(delivery.resolve_breathing_playback(user_id=DUSER, pet_id=DPET, motion_version_id=DVERSION))
    assert playback.qa_decision == "FAIL" and playback.published is False


def test_integrity_fail_is_never_packageable(monkeypatch):
    _on(monkeypatch)
    _seed_delivery(decision="FAIL")
    mv._MOCK_CANDIDATES[0]["qa_result"] = {"decision": "FAIL", "reasons": ["scene_cut_or_swap adjacent 0.1 < 0.2"], "checks": {"temporal_stability": "FAIL"}}
    with pytest.raises(delivery.MotionDeliveryError) as e:
        _package_delivery()
    assert e.value.code == "CANDIDATE_NOT_PACKAGEABLE"


# ══════════════════════════════════════════════════════════════════════════
# 5. 실행: 전달했으면 FAILED 로 끝나지 않는다; 못 했으면 사용자용 문장
# ══════════════════════════════════════════════════════════════════════════

from types import SimpleNamespace  # noqa: E402


def _harness_with_gated_candidate(monkeypatch, *, reasons, decision="FAIL", status="failed"):
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch, motion_status=status)
    gated = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000699", selected=True, decision=decision,
        qa_result={"decision": decision, "reasons": reasons, "checks": {}, "identity_similarity": 0.9},
    )
    harness.motion.candidates = [gated]
    harness.motion.selected_candidate_id = gated.id
    harness.publication = SimpleNamespace(publication_id="00000000-0000-0000-0000-000000000702", selected_candidate_id=gated.id)
    return harness, gated


def test_run_publishes_gated_candidate_and_does_not_end_failed(storage, monkeypatch):
    _on(monkeypatch)
    harness, gated = _harness_with_gated_candidate(monkeypatch, reasons=["vlm_unintended_large_motion"])
    p7c.start(key="gate-on-cosmetic")
    result = p7c.work()
    assert result.status == runs.STATUS_PUBLISHED and result.last_error is None
    assert result.selected_candidate_id == gated.id
    assert harness.counts["delivery"] == 1 and harness.counts["publication"] == 1


def test_run_with_integrity_reason_still_fails_with_user_message(storage, monkeypatch):
    _on(monkeypatch)
    harness, _ = _harness_with_gated_candidate(monkeypatch, reasons=["vlm:same_pet_all_frames=no"])
    p7c.start(key="gate-on-integrity")
    result = p7c.work()
    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "MOTION_QA_FAILED"
    assert "Phase 6" not in result.last_error["message"]
    assert "품질 기준" in result.last_error["message"]
    assert harness.counts["publication"] == 0


def test_run_gate_off_keeps_failed_outcome_and_new_message(storage, monkeypatch):
    harness, _ = _harness_with_gated_candidate(monkeypatch, reasons=["vlm_unintended_large_motion"])
    p7c.start(key="gate-off")
    result = p7c.work()
    assert result.status == runs.STATUS_FAILED
    assert result.last_error["code"] == "MOTION_QA_FAILED"
    assert result.last_error["message"] == "이번에 만든 영상이 품질 기준을 충족하지 못해 전달하지 못했습니다. 다시 시도해 주세요."
    assert harness.counts["publication"] == 0 and harness.counts["delivery"] == 0


def test_run_gate_on_review_version_without_selection_still_reports_review(storage, monkeypatch):
    """게이트가 켜져도 빌더가 아무 후보도 못 골랐으면(무결성 사유) REVIEW 계약은 그대로다."""
    _on(monkeypatch)
    p7c.seed_intake()
    harness = p7c.PipelineHarness(monkeypatch, motion_status="review")
    harness.motion.candidates = [SimpleNamespace(
        id="00000000-0000-0000-0000-000000000698", selected=False, decision="REVIEW",
        qa_result={"decision": "REVIEW", "reasons": ["identity borderline worst_frame 0.33"], "checks": {}, "identity_similarity": 0.5},
    )]
    p7c.start(key="gate-on-review-integrity")
    result = p7c.work()
    assert result.status == runs.STATUS_FAILED and result.last_error["code"] == "MOTION_QA_REVIEW"
    assert result.current_stage == runs.STAGE_QA
