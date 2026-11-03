"""
Phase 6.5 — 출력 규격 검증 + QA 캘리브레이션 준비 테스트.

종횡비 판정 (테스트 단계 2026-10-22): 생성 모션 자산 전역값은 16:9 · 480p
(854×480) — render.yaml 의 PHASE6_ASPECT_RATIO/PHASE6_RESOLUTION 로 고정. 테스트
후 포트레이트 HD 로 되돌린다. 여기서는 "요청은 명시했고, 출력이 어기면 FAIL"
계약(종횡비 불일치 → FAIL)을 검증한다 — 지금 요청은 16:9 라 세로 출력이 FAIL 이다.
"""

from __future__ import annotations

import anyio
import pytest

from backend.services import canonical_pet_service as canon
from backend.services import action_keyframe_service as kf
from backend.services import motion_video_qa as qa_mod
from backend.services import pet_morphology_service as morph
from backend.services import motion_video_service as mv
from backend.services import pet_identity_service as ids
from backend.services import pet_reference_service as refs
from backend.services import pet_reference_set_service as sets
from backend.services import pet_registry

from .test_canonical_pet_builder import GOOD
from .test_motion_video_generation import (
    VLM_MV_OK,
    FakeVideoProvider,
    _build_motion,
    _prepare_pipeline,
    sampler_identical,
)
from .test_pet_reference_sets import PET, USER

SPEC = {"aspect_ratio": "16:9", "resolution": "480p", "duration_sec": 5, "audio": False}


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("HYBRID_USE_SUPABASE", "0")
    monkeypatch.delenv("PET_VLM_IDENTITY_ENABLED", raising=False)
    monkeypatch.setenv("CANONICAL_QA_MIN_RESOLUTION", "100")
    monkeypatch.setenv("PHASE6_LIVE_MODE", "all")
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()
    yield
    for m in (refs, pet_registry, ids, morph, sets, canon, kf, mv):
        m.__reset_for_tests()


@pytest.fixture
def storage(monkeypatch) -> dict[str, bytes]:
    from backend.services import supabase_assets

    store: dict[str, bytes] = {}

    async def fake_upload(path, data, content_type):
        store[path] = bytes(data)
        return f"https://storage.test/{path}"

    monkeypatch.setattr(supabase_assets, "upload_asset_to_storage", fake_upload)
    return store


def _run(coro):
    return anyio.run(lambda: coro)


# ══════════════════════════════════════════════════════════════════════════
# 출력 규격 검증 (단위)
# ══════════════════════════════════════════════════════════════════════════


def _verify(probe):
    return qa_mod.verify_output_conformance(b"video", SPEC, probe=probe)


def test_conformance_pass_on_correct_landscape_output():
    # 테스트 단계 전역 16:9 · 480p → 854×480 가 규격 준수.
    r = _verify({"width": 854, "height": 480, "duration": 5.0, "has_audio": False})
    assert r["status"] == "PASS"
    assert r["checks"] == {
        "aspect_ratio": "PASS", "resolution": "PASS", "duration": "PASS", "audio_disabled": "PASS",
    }


def test_conformance_fails_on_portrait_despite_landscape_request():
    """종횡비 계약 — 요청은 16:9 인데 출력이 720×1280(세로)이면 FAIL."""
    r = _verify({"width": 720, "height": 1280, "duration": 5.0, "has_audio": False})
    assert r["checks"]["aspect_ratio"] == "FAIL"
    assert r["status"] == "FAIL"
    assert any("aspect_mismatch" in reason for reason in r["reasons"])


def test_conformance_fails_on_audio_stream():
    r = _verify({"width": 854, "height": 480, "duration": 5.0, "has_audio": True})
    assert r["checks"]["audio_disabled"] == "FAIL"
    assert r["status"] == "FAIL"


def test_conformance_review_on_low_resolution_or_duration():
    # 저해상도도 16:9 를 유지(426×240)해야 종횡비 FAIL 없이 해상도 REVIEW 만 잡힌다.
    r = _verify({"width": 426, "height": 240, "duration": 5.0, "has_audio": False})
    assert r["checks"]["resolution"] == "REVIEW" and r["status"] == "REVIEW"
    r = _verify({"width": 854, "height": 480, "duration": 10.5, "has_audio": False})
    assert r["checks"]["duration"] == "REVIEW" and r["status"] == "REVIEW"


def test_conformance_unknown_when_probe_unavailable():
    r = _verify(None) if False else qa_mod.verify_output_conformance(b"", SPEC, probe=None)
    # 빈 바이트 → 실제 probe 시도 → 실패 → unknown. PASS 로 승격되지 않는다.
    assert r["status"] == "unknown"
    assert "probe_unavailable" in r["reasons"]


# ══════════════════════════════════════════════════════════════════════════
# 빌드 통합 — 규격 위반은 후보를 FAIL 로 강등한다
# ══════════════════════════════════════════════════════════════════════════


def test_wrong_aspect_output_downgrades_candidate_to_fail(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)

    def portrait_conformance(video_bytes, output_spec):
        # 테스트 단계 요청은 16:9 라 세로(720×1280) 출력이 종횡비 FAIL 이다.
        return qa_mod.verify_output_conformance(
            video_bytes, output_spec,
            probe={"width": 720, "height": 1280, "duration": 5.0, "has_audio": False},
        )

    primary = FakeVideoProvider("seedance", [GOOD()] * 3)
    fallback = FakeVideoProvider("kling", [GOOD()])
    # 폴백은 규격 준수 출력을 낸다 — 폴백 경로에는 정상 conformance 를 준다.
    calls = {"n": 0}

    def mixed_conformance(video_bytes, output_spec):
        # 클래스 인지 상한(MICRO 기본 2)과 무관하게 "seedance 는 전부 규격 위반,
        # kling 은 준수" 를 유지한다 — 호출 횟수가 아니라 상한을 기준으로 센다.
        calls["n"] += 1
        if calls["n"] <= mv.candidate_policy("MICRO")["max_primary"]:
            return portrait_conformance(video_bytes, output_spec)
        return qa_mod.verify_output_conformance(
            video_bytes, output_spec,
            probe={"width": 854, "height": 480, "duration": 5.0, "has_audio": False},
        )

    v = _build_motion(h, "BREATHING", [primary, fallback], conformance=mixed_conformance)
    seedance_cands = [c for c in v.candidates if c.provider == "seedance"]
    assert all(c.decision == "FAIL" for c in seedance_cands)
    assert all(
        any("output_conformance:aspect_mismatch" in r for r in c.qa_result["reasons"])
        for c in seedance_cands
    )
    assert v.status == mv.STATUS_COMPLETE
    assert next(c for c in v.candidates if c.selected).provider == "kling"


def test_conformance_recorded_on_passing_candidate(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    sel = next(c for c in v.candidates if c.selected)
    assert sel.qa_result["output_conformance"]["status"] == "PASS"
    assert sel.qa_result["output_conformance"]["probe"]["width"] == 854


# ══════════════════════════════════════════════════════════════════════════
# QA 캘리브레이션 보고
# ══════════════════════════════════════════════════════════════════════════


def test_qa_calibration_report(storage, monkeypatch):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    v = _build_motion(h, "BREATHING", [FakeVideoProvider("seedance", [GOOD()])])
    sel = next(c for c in v.candidates if c.selected)
    assert sel.decision == "PASS"

    # 사람: PASS (true PASS 사례) — 자동 PASS 와 일치.
    _run(
        mv.record_motion_evaluation(
            user_id=USER, pet_id=PET, motion_version_id=v.id, candidate_id=sel.id,
            scores={"identity_fidelity": 9, "motion_correctness": 9},
            verdict="PASS", overall_usable=True,
        )
    )
    # 같은 후보에 대한 반대 판정 (false PASS 사례) — 계산 검증용.
    _run(
        mv.record_motion_evaluation(
            user_id=USER, pet_id=PET, motion_version_id=v.id, candidate_id=sel.id,
            scores={"identity_fidelity": 2}, verdict="FAIL",
        )
    )

    report = _run(mv.qa_calibration_report(user_id=USER))
    assert report["qa_version"] == qa_mod.MOTION_VIDEO_QA_VERSION
    assert report["sample_count"] == 2
    assert report["buckets"]["true_pass"] == 1
    assert report["buckets"]["false_pass"] == 1
    assert report["matrix"]["PASS"]["PASS"] == 1
    assert report["matrix"]["PASS"]["FAIL"] == 1
    assert "10~20" in report["note"]  # 소표본 재캘리브레이션 금지 명시
    pair = report["pairs"][0]
    assert pair["motion_id"] == "BREATHING" and pair["provider"] == "seedance"


# ══════════════════════════════════════════════════════════════════════════
# 스모크 러너 안전장치
# ══════════════════════════════════════════════════════════════════════════


def test_smoke_preflight_reports_missing_credentials(monkeypatch, capsys):
    from backend.scripts import phase6_live_smoke as smoke

    for var in ("SEEDANCE_API_KEY", "ARK_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    ok = smoke.preflight()
    out = capsys.readouterr().out
    assert ok is False
    assert "라이브 호출 불가" in out
    assert "PHASE6_LIVE_MODE" in out


def test_authoritative_aspect_ratio_is_landscape_by_default(monkeypatch):
    # 테스트 단계(2026-10-22): env 없이도 코드 기본값이 16:9 (가로). 포트레이트로
    # 되돌리려면 build_output_spec 기본값과 render.yaml env 를 9:16 으로.
    monkeypatch.delenv("PHASE6_ASPECT_RATIO", raising=False)
    spec = mv.default_output_spec([3.0, 6.0])
    assert spec["aspect_ratio"] == "16:9"
    assert spec["audio"] is False
