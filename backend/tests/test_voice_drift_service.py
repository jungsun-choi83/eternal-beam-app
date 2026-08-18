"""
VOICE 드리프트 측정기 — 안전성 검증 (Phase 8E).

핵심 계약: **기본은 diagnostic 모드**다. 임계값을 아무리 넘겨도 passed=True 여야
하고, 따라서 이 모듈만으로는 유료 재생성이 절대 늘어나지 않는다.
임계값은 아직 보정되지 않은 잠정값(n=23)이다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import pytest

from backend.services import voice_drift_service as vds
from backend.services.voice_drift_service import VoiceDriftMetrics, measure_voice_drift


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.delenv("VOICE_DRIFT_GATE_ENABLED", raising=False)


def _stub(monkeypatch, *, bbox_h, area, tcx=0.0, tcy=0.0, cx=0.0, cy=0.0, width=480.0):
    """프레임 추출/측정을 건너뛰고 형상 통계만 주입한다."""
    base = {"bbox_h": 100.0, "area": 10000.0, "cx": 0.0, "cy": 0.0,
            "tcx": 0.0, "tcy": 0.0, "width": width}
    after = {"bbox_h": bbox_h, "area": area, "cx": cx, "cy": cy,
             "tcx": tcx, "tcy": tcy, "width": width}
    seq = iter([base, after])
    monkeypatch.setattr(vds, "_shape_stats", lambda png: next(seq))

    import backend.services.idle_validation_service as ivs

    monkeypatch.setattr(ivs, "_probe_duration_sec", lambda b: 5.03)
    monkeypatch.setattr(ivs, "_extract_frame_png", lambda b, ss="0.15": b"png")


# ── diagnostic 모드가 절대 막지 않는다 ──────────────────────────────────────


def test_diagnostic_mode_is_default():
    assert vds.gate_enabled() is False


def test_diagnostic_never_fails_even_on_extreme_drift(monkeypatch):
    _stub(monkeypatch, bbox_h=40.0, area=3000.0, tcx=200.0)  # -60% h, -70% area, 큰 이동
    m = measure_voice_drift(b"v")
    assert m.passed is True, "diagnostic 모드가 재생성을 유발하면 안 된다"
    assert m.gate_enabled is False
    assert m.violations, "위반은 기록되어야 한다"
    assert "diagnostic_only" in m.message
    assert "would_flag" in m.message


def test_metrics_are_computed_correctly(monkeypatch):
    _stub(monkeypatch, bbox_h=90.0, area=8000.0, tcx=24.0, width=480.0)
    m = measure_voice_drift(b"v")
    assert m.bbox_h_change_pct == pytest.approx(-10.0)
    assert m.area_change_pct == pytest.approx(-20.0)
    assert m.torso_centroid_disp_pct == pytest.approx(5.0)  # 24/480
    assert m.frame_width == 480


# ── gate 모드는 플래그 뒤에 있고, 켜야만 동작한다 ───────────────────────────


def test_gate_mode_can_fail_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("VOICE_DRIFT_GATE_ENABLED", "1")
    _stub(monkeypatch, bbox_h=100.0, area=5000.0)  # -50% area
    m = measure_voice_drift(b"v")
    assert m.gate_enabled is True
    assert m.passed is False
    assert any("area" in v for v in m.violations)


def test_gate_mode_passes_clean_clip(monkeypatch):
    monkeypatch.setenv("VOICE_DRIFT_GATE_ENABLED", "1")
    _stub(monkeypatch, bbox_h=102.0, area=10100.0, tcx=5.0)
    m = measure_voice_drift(b"v")
    assert m.passed is True
    assert m.violations == []
    assert m.message == "ok"


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_gate_stays_off_for_falsey_values(monkeypatch, value: str):
    monkeypatch.setenv("VOICE_DRIFT_GATE_ENABLED", value)
    assert vds.gate_enabled() is False


# ── 잠정 임계값이 문서화된 값 그대로인지 ────────────────────────────────────


def test_provisional_thresholds():
    assert vds.PROVISIONAL_MAX_AREA_CHANGE_PCT == 15.0
    assert vds.PROVISIONAL_MAX_BBOX_H_CHANGE_PCT == 15.0
    assert vds.PROVISIONAL_MAX_CENTROID_DISP_PCT == 6.0


# ── 측정 불가 상황은 조용히 통과 ────────────────────────────────────────────


def test_missing_duration_is_not_a_failure(monkeypatch):
    import backend.services.idle_validation_service as ivs

    monkeypatch.setattr(ivs, "_probe_duration_sec", lambda b: None)
    m = measure_voice_drift(b"v")
    assert m.passed is True
    assert m.message == "duration_unavailable"


def test_subject_not_found_is_not_a_failure(monkeypatch):
    import backend.services.idle_validation_service as ivs

    monkeypatch.setattr(ivs, "_probe_duration_sec", lambda b: 5.0)
    monkeypatch.setattr(ivs, "_extract_frame_png", lambda b, ss="0.15": b"png")
    monkeypatch.setattr(vds, "_shape_stats", lambda png: None)
    m = measure_voice_drift(b"v")
    assert m.passed is True
    assert m.message == "subject_not_found"


def test_to_dict_carries_all_metrics(monkeypatch):
    _stub(monkeypatch, bbox_h=95.0, area=9500.0)
    d = measure_voice_drift(b"v").to_dict()
    for k in ("bbox_h_change_pct", "area_change_pct", "centroid_disp_pct",
              "torso_centroid_disp_pct", "duration_sec", "frame_width",
              "passed", "gate_enabled", "violations", "message"):
        assert k in d


def test_default_result_is_safe():
    assert VoiceDriftMetrics().passed is True
    assert VoiceDriftMetrics().gate_enabled is False


# ── 다른 검증기는 건드리지 않았다 ───────────────────────────────────────────


def test_idle_gate_untouched():
    from backend.services.idle_validation_service import DEFAULT_LOOP_SSIM_THRESHOLD

    assert DEFAULT_LOOP_SSIM_THRESHOLD == 0.65
