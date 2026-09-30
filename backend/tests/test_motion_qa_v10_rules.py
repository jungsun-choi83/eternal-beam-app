"""
motion-video-qa-v10 — 규칙 집합 플래그, 경계 구간, VLM/시간축 상충 규칙, 오프라인 재판정.

핵심 계약:
  * 플래그 미설정 = v9 와 동일한 판정 (바이트 단위 동일 checks/decision).
  * v10 의 완화는 FAIL→REVIEW 강등 또는 REVIEW→advisory 뿐이다. 어떤 규칙도
    VLM "no" / scene_cut / 신원 FAIL / 한계 1.5배 이상 위반을 되살리지 않는다.
  * rescore_stored_qa_result 는 저장된 qa_result 만으로 라이브 판정을 재현한다.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from backend.services import breathing_temporal_qa as bt
from backend.services import motion_video_qa as qa

KEYFRAME = np.full((96, 64, 3), 180, dtype=np.uint8)
KEYFRAME[30:70, 20:44] = (90, 60, 40)  # 회색 배경 위의 펫 덩어리

_CONTRACT = {"motion_id": "BREATHING", "motion_class": "MICRO",
             "video_compat": {"returns_to_start_pose": True}}

THRESHOLDS = {
    "scale_pulse_max": 0.020, "scale_strict": 0.010, "drift_max_frac": 0.020,
    "sag_trend_max": 0.010, "visible_osc_min": 0.003, "torso_snr_min": 1.6,
    "periodic_min": 0.25, "modulation_strong": 0.45, "head_ratio_max": 1.6,
    "head_ratio_max_midband": 1.2,
}


def _vlm(motion: str = "unknown", **over) -> dict:
    base = {
        "same_pet_all_frames": "yes", "anatomy_plausible_all_frames": "yes",
        "requested_motion_occurs": motion, "unintended_large_motion": "no",
        "duplicated_pet": "no", "scene_cut": "no", "human_present": "no",
        "major_flicker": "no", "camera_stable": "yes", "background_neutral": "yes",
        "single_pet": "yes", "ends_in_target_pose": "unknown", "source": "vlm-motion-qa-v2",
    }
    base.update(over)
    return base


def _metrics(**over) -> dict:
    m = {
        "scale_range": 0.006, "scale_oscillation": 0.0055, "scale_trend": 0.002,
        "translation_drift_px": 0.5, "translation_drift_frac_of_pet": 0.002,
        "torso_snr": 6.0, "head_to_torso_ratio": 0.8, "periodic_score": 0.3,
        "torso_energy_modulation": 0.5, "pet_height_px": 40.0,
    }
    m.update(over)
    return m


def _temporal(**over) -> dict:
    metrics = _metrics(**over)
    verdict, reason, advisories = bt._classify_temporal_metrics(metrics, THRESHOLDS)
    return {"version": bt.BREATHING_TEMPORAL_QA_VERSION, "verdict": verdict, "reason": reason,
            "metrics": metrics, "thresholds": dict(THRESHOLDS), "advisories": advisories}


def _evaluate(*, vlm=None, temporal=None, ruleset=None, contract=_CONTRACT):
    img = KEYFRAME
    return qa.evaluate_motion_video(
        frames=[img, img, img],
        spec_contract=contract,
        start_keyframe_rgb=img,
        target_keyframe_rgb=None,
        vlm_qa=vlm,
        temporal_qa=temporal,
        ruleset=ruleset,
    )


# ── 플래그 ────────────────────────────────────────────────────────────────


def test_default_ruleset_is_v9_and_unknown_values_fail_closed(monkeypatch):
    monkeypatch.delenv(qa.MOTION_VIDEO_QA_RULESET_ENV, raising=False)
    assert qa.active_ruleset() == qa.RULESET_V9
    assert qa.active_qa_version() == qa.MOTION_VIDEO_QA_VERSION == "motion-video-qa-v9"
    monkeypatch.setenv(qa.MOTION_VIDEO_QA_RULESET_ENV, "v11-typo")
    assert qa.active_ruleset() == qa.RULESET_V9
    monkeypatch.setenv(qa.MOTION_VIDEO_QA_RULESET_ENV, "v10")
    assert qa.active_ruleset() == qa.RULESET_V10
    assert qa.active_qa_version() == "motion-video-qa-v10"
    assert qa.active_ruleset("v9") == qa.RULESET_V9  # 인자가 환경보다 우선


def test_v9_output_unchanged_when_flag_off(monkeypatch):
    monkeypatch.delenv(qa.MOTION_VIDEO_QA_RULESET_ENV, raising=False)
    borderline = _temporal(scale_range=0.0214)  # 1.07x — v10 이면 REVIEW 후보
    out = _evaluate(vlm=_vlm("unknown"), temporal=borderline)
    assert out["qa_version"] == "motion-video-qa-v9"
    assert out["ruleset"] == "v9"
    assert out["checks"]["temporal_breathing"] == "FAIL"
    assert out["decision"] == "FAIL"
    assert out["judgement"]["downgrades"] == [] and out["judgement"]["advisories"] == []


def test_env_flag_switches_live_evaluation_to_v10(monkeypatch):
    monkeypatch.setenv(qa.MOTION_VIDEO_QA_RULESET_ENV, "v10")
    out = _evaluate(vlm=_vlm("unknown"), temporal=_temporal(scale_range=0.0214, torso_snr=9.3, scale_oscillation=0.025))
    assert out["qa_version"] == "motion-video-qa-v10"
    assert out["checks"]["temporal_breathing"] == "REVIEW"
    assert out["decision"] == "REVIEW"


# ── (b) 경계 구간 ─────────────────────────────────────────────────────────


def test_borderline_global_pulse_with_strong_breathing_is_review_not_fail():
    t = _temporal(scale_range=0.0214, torso_snr=9.3, scale_oscillation=0.025)
    assert t["verdict"] == bt.VERDICT_GLOBAL_PULSE
    out = _evaluate(vlm=_vlm("unknown"), temporal=t, ruleset="v10")
    assert out["checks"]["temporal_breathing"] == "REVIEW"
    assert out["decision"] == "REVIEW"
    dg = out["judgement"]["downgrades"]
    assert dg and dg[0]["check"] == "temporal_breathing" and dg[0]["reason"] == "global_pulse_borderline"
    assert any(r.startswith("temporal_global_pulse_borderline") for r in out["reasons"])
    # 강등된 REVIEW 는 자문이 아니다 — PASS 로 풀리지 않는다.
    assert "temporal_breathing" not in out["advisories"]["checks"]


def test_borderline_requires_strong_breathing_evidence():
    weak = _temporal(scale_range=0.0214, torso_snr=2.9, scale_oscillation=0.0031)
    out = _evaluate(vlm=_vlm("unknown"), temporal=weak, ruleset="v10")
    assert out["checks"]["temporal_breathing"] == "FAIL"
    assert out["decision"] == "FAIL"


def test_borderline_never_applies_when_any_gate_is_a_clear_violation():
    # scale_range 1.06x 이지만 침하 추세 1.96x — 명백한 프레이밍 드리프트.
    t = _temporal(scale_range=0.0211, scale_trend=0.0196, scale_oscillation=0.0031, torso_snr=9.0)
    out = _evaluate(vlm=_vlm("unknown"), temporal=t, ruleset="v10")
    assert out["checks"]["temporal_breathing"] == "FAIL"
    assert out["decision"] == "FAIL"
    assert out["judgement"]["hard_fails"] and out["judgement"]["hard_fails"][0]["worst_ratio"] >= 1.5


def test_borderline_outside_band_stays_fail():
    t = _temporal(translation_drift_frac_of_pet=0.026, torso_snr=9.0, scale_oscillation=0.02)  # 1.3x
    out = _evaluate(vlm=_vlm("unknown"), temporal=t, ruleset="v10")
    assert out["decision"] == "FAIL"


def test_vlm_no_is_never_rescued_by_borderline_band():
    t = _temporal(scale_range=0.0214, torso_snr=9.3, scale_oscillation=0.025)
    out = _evaluate(vlm=_vlm("no"), temporal=t, ruleset="v10")
    assert out["checks"]["vlm_motion"] == "FAIL"
    assert out["decision"] == "FAIL"


def test_band_knob_is_env_tunable(monkeypatch):
    t = _temporal(translation_drift_frac_of_pet=0.0232, torso_snr=9.0, scale_oscillation=0.02)  # 1.16x
    assert _evaluate(vlm=_vlm("unknown"), temporal=t, ruleset="v10")["decision"] == "FAIL"
    monkeypatch.setenv("MOTION_QA_V10_BORDERLINE_BAND", "0.20")
    assert _evaluate(vlm=_vlm("unknown"), temporal=t, ruleset="v10")["decision"] == "REVIEW"


# ── (a) VLM 구도/카메라 vs 결정론 시간축 ─────────────────────────────────


def test_camera_stable_no_is_advisory_when_temporal_gates_pass():
    t = _temporal()
    assert t["verdict"] == bt.VERDICT_BREATHING
    v9 = _evaluate(vlm=_vlm("unknown", camera_stable="no"), temporal=t, ruleset="v9")
    v10 = _evaluate(vlm=_vlm("unknown", camera_stable="no"), temporal=t, ruleset="v10")
    assert v9["checks"]["vlm_composition"] == "REVIEW" and v9["decision"] == "REVIEW"
    assert v10["checks"]["vlm_composition"] == "REVIEW"  # 근거 보존
    assert "vlm_composition" in v10["advisories"]["checks"]
    assert v10["decision"] == "PASS"
    assert any(r.startswith("advisory_checks_not_blocking") for r in v10["reasons"])


def test_camera_stable_no_stays_blocking_without_temporal_metrics():
    # BREATHING 이 아니거나 지표가 없으면 v10 도 v9 그대로다.
    out = _evaluate(vlm=_vlm("yes", camera_stable="no"), temporal=None, ruleset="v10")
    assert out["checks"]["vlm_composition"] == "REVIEW"
    assert out["decision"] == "REVIEW"


def test_background_not_neutral_is_not_covered_by_temporal_metrics():
    out = _evaluate(vlm=_vlm("unknown", background_neutral="no"), temporal=_temporal(), ruleset="v10")
    assert out["decision"] == "REVIEW"
    assert "vlm_composition" not in out["advisories"]["checks"]


def test_unintended_large_motion_downgrades_to_review_only_when_gates_pass():
    passing = _temporal(scale_range=0.0169, scale_trend=0.0076)
    out = _evaluate(vlm=_vlm("unknown", unintended_large_motion="yes"), temporal=passing, ruleset="v10")
    assert out["checks"]["vlm_composition"] == "REVIEW"
    assert out["decision"] == "REVIEW"  # 자동 PASS 는 없다
    assert "vlm_composition" not in out["advisories"]["checks"]

    failing = _temporal(translation_drift_frac_of_pet=0.0232, torso_snr=2.9)
    out = _evaluate(vlm=_vlm("unknown", unintended_large_motion="yes"), temporal=failing, ruleset="v10")
    assert out["checks"]["vlm_composition"] == "FAIL"
    assert out["decision"] == "FAIL"


def test_temporal_gates_pass_requires_adjacent_frame_stability():
    img = KEYFRAME
    white = np.full_like(img, 255)
    out = qa.evaluate_motion_video(
        frames=[img, white, img], spec_contract=_CONTRACT, start_keyframe_rgb=img,
        target_keyframe_rgb=None, vlm_qa=_vlm("unknown", camera_stable="no"),
        temporal_qa=_temporal(), ruleset="v10",
    )
    assert out["checks"]["temporal_stability"] == "FAIL"
    assert out["judgement"]["temporal_gates_pass"] is False
    assert out["decision"] == "FAIL"


# ── (d) 명백한 위반은 그대로 hard FAIL ───────────────────────────────────


@pytest.mark.parametrize("flag", ["scene_cut", "duplicated_pet", "human_present"])
def test_composition_contamination_remains_hard_fail(flag):
    out = _evaluate(vlm=_vlm("unknown", **{flag: "yes"}), temporal=_temporal(), ruleset="v10")
    assert out["checks"]["vlm_composition"] == "FAIL"
    assert out["decision"] == "FAIL"


@pytest.mark.parametrize("key", ["anatomy_plausible_all_frames", "same_pet_all_frames"])
def test_vlm_identity_and_anatomy_no_remain_hard_fail(key):
    out = _evaluate(vlm=_vlm("yes", **{key: "no"}), temporal=_temporal(), ruleset="v10")
    assert out["decision"] == "FAIL"


def test_identity_drift_remains_hard_fail_under_v10():
    img = KEYFRAME
    white = np.full_like(img, 255)
    out = qa.evaluate_motion_video(
        frames=[img, img, white, white, white], spec_contract=_CONTRACT, start_keyframe_rgb=img,
        target_keyframe_rgb=None, vlm_qa=_vlm("yes"), temporal_qa=_temporal(), ruleset="v10",
    )
    assert out["checks"]["identity_over_time"] == "FAIL"
    assert out["decision"] == "FAIL"


# ── 휴리스틱 구조 FAIL 의 경계 구간 (VLM 해부학 확언 시) ──────────────────


def _structural_evidence(*, mismatch: int, support: int, deformation: float) -> dict:
    return {
        "enabled": True,
        "strong_contradiction_ratio": 0.7,
        "trait_summary": {
            "body_length_class": {"status": "FAIL", "mismatch_frames": mismatch, "support_frames": support},
        },
        "anatomy_signals": {"deformation_ratio_max": deformation},
        "advisory_checks": [], "advisory_findings": [],
    }


def _judge_structural(evidence: dict, *, ruleset: str, band: float | None = None, monkeypatch=None):
    if band is not None and monkeypatch is not None:
        monkeypatch.setenv("MOTION_QA_V10_BORDERLINE_BAND", str(band))
    checks = {"identity_over_time": "PASS", "temporal_stability": "PASS",
              "structural_morphology_consistency": "FAIL", "anatomy_body_deformation": "FAIL"}
    contract = {"motion_id": "PET_HEAD", "motion_class": "INTERACTION",
                "video_compat": {"returns_to_start_pose": True, "allow_generated_hand": True},
                "requirements": {"qa": {"structural_anatomy": {
                    "required_checks": ["vlm_anatomy", "structural_morphology_consistency"]}}}}
    return qa.apply_judgement(
        checks=checks, reasons=["structural_body_length_class_strong_contradiction 0.778 >= 0.7"],
        spec_contract=contract, vlm_qa=_vlm("yes"), temporal_qa=None,
        structural_evidence=evidence, ruleset=ruleset,
    )


def test_structural_borderline_with_vlm_anatomy_ok_becomes_review(monkeypatch):
    ev = _structural_evidence(mismatch=7, support=9, deformation=2.1)  # 1.11x, 1.05x
    assert _judge_structural(ev, ruleset="v9")["decision"] == "FAIL"
    out = _judge_structural(ev, ruleset="v10")
    assert out["checks"]["structural_morphology_consistency"] == "REVIEW"
    assert out["checks"]["anatomy_body_deformation"] == "REVIEW"
    assert out["decision"] == "REVIEW"
    # 비필수 anatomy_body_deformation 이 REVIEW 가 됐어도 자문으로 풀리지 않는다.
    assert "anatomy_body_deformation" not in out["advisories"]["checks"]
    assert out["domains"]["structural_anatomy"]["status"] == "REVIEW"


def test_structural_borderline_respects_band_and_vlm_disagreement(monkeypatch):
    # 실측 3e28b18c: body_length 1.11x 이지만 deformation 2.56/2.0 = 1.28x → 기본 band 0.15 밖.
    ev = _structural_evidence(mismatch=7, support=9, deformation=2.5626)
    assert _judge_structural(ev, ruleset="v10")["decision"] == "FAIL"
    assert _judge_structural(ev, ruleset="v10", band=0.30, monkeypatch=monkeypatch)["decision"] == "REVIEW"
    # VLM 이 해부학 이상을 봤으면 손대지 않는다.
    checks = {"structural_morphology_consistency": "FAIL", "identity_over_time": "PASS"}
    out = qa.apply_judgement(
        checks=checks, reasons=[], spec_contract={"motion_class": "INTERACTION", "video_compat": {}},
        vlm_qa=_vlm("yes", anatomy_plausible_all_frames="no"), temporal_qa=None,
        structural_evidence=_structural_evidence(mismatch=7, support=9, deformation=1.0), ruleset="v10",
    )
    assert out["checks"]["structural_morphology_consistency"] == "FAIL"
    assert out["decision"] == "FAIL"


# ── 오프라인 재판정 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("ruleset", ["v9", "v10"])
@pytest.mark.parametrize(
    "vlm,temporal",
    [
        (_vlm("unknown"), _temporal()),                                             # PASS via temporal
        (_vlm("unknown"), _temporal(scale_range=0.0214, torso_snr=9.3, scale_oscillation=0.025)),  # borderline
        (_vlm("unknown", camera_stable="no"), _temporal()),                         # advisory
        (_vlm("unknown", unintended_large_motion="yes"), _temporal(scale_range=0.0169)),
        (_vlm("no"), _temporal()),                                                  # hard fail
        (None, None),                                                               # vlm unavailable
    ],
)
def test_rescore_reproduces_live_decision(ruleset, vlm, temporal):
    live = _evaluate(vlm=vlm, temporal=temporal, ruleset=ruleset)
    stored = json.loads(json.dumps(live))  # DB 왕복(jsonb) 흉내
    again = qa.rescore_stored_qa_result(stored, motion_id="BREATHING", ruleset=ruleset)
    assert again["decision"] == live["decision"]
    assert again["checks"] == live["checks"]
    assert again["qa_version"] == live["qa_version"]
    assert again["stored_qa_version"] == live["qa_version"]


def test_rescore_reapplies_output_conformance_downgrade():
    live = _evaluate(vlm=_vlm("yes"), temporal=_temporal(), ruleset="v9")
    assert live["decision"] == "PASS"
    stored = json.loads(json.dumps(live))
    stored["output_conformance"] = {"status": "FAIL", "reasons": ["aspect_mismatch requested 9:16 got 1440x1440"]}
    out = qa.rescore_stored_qa_result(stored, motion_id="BREATHING", ruleset="v10")
    assert out["decision"] == "FAIL"
    assert any(r.startswith("output_conformance:aspect_mismatch") for r in out["reasons"])
    stored["output_conformance"] = {"status": "REVIEW", "reasons": ["duration_off requested 5s got 8.0s"]}
    assert qa.rescore_stored_qa_result(stored, motion_id="BREATHING", ruleset="v10")["decision"] == "REVIEW"


def test_rescore_reclassifies_stored_temporal_metrics_with_current_classifier():
    # temporal v2 시절 receipt: head ratio 로 unlocalized_motion(REVIEW). 지표는 남아 있다.
    legacy = _temporal(head_to_torso_ratio=2.0)
    legacy["verdict"] = bt.VERDICT_UNLOCALIZED
    legacy["reason"] = "head_to_torso 2.0 > 1.6"
    live = _evaluate(vlm=_vlm("unknown"), temporal=legacy, ruleset="v9")
    assert live["decision"] == "REVIEW"
    stored = json.loads(json.dumps(live))
    kept = qa.rescore_stored_qa_result(stored, motion_id="BREATHING", ruleset="v9", reclassify_temporal=False)
    assert kept["decision"] == "REVIEW" and kept["temporal_reclassified"] is False
    now = qa.rescore_stored_qa_result(stored, motion_id="BREATHING", ruleset="v9")
    assert now["temporal_reclassified"] is True
    assert now["temporal_verdict"] == bt.VERDICT_BREATHING  # v3: head ratio 는 advisory
    assert now["decision"] == "PASS"


def test_rescore_uses_motion_spec_compat_for_allowed_hand():
    # PET_HEAD 는 손을 허용한다 — human_present=yes 가 오염이 아니다.
    live = qa.evaluate_motion_video(
        frames=[KEYFRAME] * 3,
        spec_contract={"motion_id": "PET_HEAD", "motion_class": "INTERACTION",
                       "video_compat": {"returns_to_start_pose": True, "allow_generated_hand": True}},
        start_keyframe_rgb=KEYFRAME, target_keyframe_rgb=None,
        vlm_qa=_vlm("yes", human_present="yes"), temporal_qa=None, ruleset="v9",
    )
    assert live["checks"]["vlm_composition"] == "PASS"
    again = qa.rescore_stored_qa_result(json.loads(json.dumps(live)), motion_id="PET_HEAD", ruleset="v9")
    assert again["checks"]["vlm_composition"] == "PASS" and again["decision"] == live["decision"]


def test_replay_script_prints_decision_changes(tmp_path, capsys):
    from backend.scripts import replay_motion_qa_rules as replay

    borderline = _evaluate(
        vlm=_vlm("unknown"), temporal=_temporal(scale_range=0.0214, torso_snr=9.3, scale_oscillation=0.025),
        ruleset="v9",
    )
    clean = _evaluate(vlm=_vlm("yes"), temporal=_temporal(), ruleset="v9")
    rows = {
        "candidates": [
            {"id": "aaaaaaaa-1", "motion_version_id": "v1", "motion_id": "BREATHING", "provider": "seedance",
             "attempt": 1, "qa_result": borderline, "decision": borderline["decision"], "created_at": "2026-09-01"},
            {"id": "bbbbbbbb-2", "motion_version_id": "v1", "motion_id": "BREATHING", "provider": "wan",
             "attempt": 1, "qa_result": clean, "decision": clean["decision"], "created_at": "2026-09-02"},
            {"id": "cccccccc-3", "motion_version_id": "v1", "motion_id": "BREATHING", "provider": "wan",
             "attempt": 2, "qa_result": {}, "decision": "ERROR", "created_at": "2026-09-03"},
        ],
        "versions": [{"id": "v1", "motion_id": "BREATHING", "motion_class": "MICRO"}],
    }
    src = tmp_path / "rows.json"
    src.write_text(json.dumps(rows))
    labels = tmp_path / "labels.csv"
    labels.write_text("candidate_id,label\naaaaaaaa,good\nbbbbbbbb-2,good\n")
    out_json = tmp_path / "out.json"
    assert replay.main(["--input", str(src), "--labels", str(labels), "--json", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "rows with QA result: 2" in text
    assert "v9→v10 decision changes: 1" in text
    assert "aaaaaaaa" in text and "FAIL   REVIEW" in text
    assert "v9 fidelity" in text and "2/2 identical" in text
    assert "good→FAIL   1" in text  # v9 행
    saved = json.loads(out_json.read_text())
    assert [r["v10"] for r in saved] == ["REVIEW", "PASS"]
    assert all("user" not in k for r in saved for k in r)


def test_rescore_keeps_stored_vlm_checks_when_v1_row_has_no_vlm_evidence():
    # qa-v1 행: vlm 은 {"source":..} 뿐이고 vlm_* 검사값만 남았다.
    stored = {
        "qa_version": "motion-video-qa-v1", "decision": "FAIL",
        "checks": {"identity_over_time": "PASS", "temporal_stability": "PASS", "loop_return": "PASS",
                   "vlm_same_pet": "PASS", "vlm_anatomy": "FAIL", "vlm_motion": "PASS", "vlm_composition": "PASS"},
        "reasons": ["vlm:anatomy_plausible_all_frames=no"],
        "vlm": {"source": "vlm-motion-qa-v1", "model": "x"},
    }
    for ruleset in ("v9", "v10"):
        out = qa.rescore_stored_qa_result(stored, motion_id="RUN", ruleset=ruleset)
        assert out["checks"]["vlm_anatomy"] == "FAIL"
        assert out["decision"] == "FAIL"
        assert "vlm_evidence_from_stored_checks" in out["reasons"]
        assert out["judgement"]["vlm_evidence"] == "stored_checks"
