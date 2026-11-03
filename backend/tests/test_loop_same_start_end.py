"""루프 same-start-end 아이들 모션 계약 테스트 (2026-10-22).

규칙: motion_class==MICRO 이고 loopable 이고 returns_to_start_pose 인 모션은
시작 **역할 키프레임**을 양끝으로 쓴다. end-frame 을 받는 provider 가 있으면
START_END(같은 키프레임을 start=end)로 올리고, 없으면 기존 단일 시작 I2V 로
폴백한다. BREATHING/STAND_READY 를 하드코딩하지 않는다 — 세 속성만 본다.

검증: 승격/폴백, 양끝에 같은 키프레임, 끝점 QA(첫≈시작, 끝≈같은 시작)가
자문으로 산출, 비자격·무관 모션 불변.
"""

from __future__ import annotations

from backend.services import motion_spec as ms
from backend.services import motion_video_service as mv

from .test_motion_video_generation import (
    FakeVideoProvider,
    _build_motion,
    _mock_backend,  # noqa: F401  (autouse fixture)
    _prepare_pipeline,
    storage,  # noqa: F401  (fixture)
)
from .test_canonical_pet_builder import GOOD
from .test_pet_reference_sets import PET, USER  # noqa: F401  (PET/USER used by helpers)


def _selected(v):
    return next(c for c in v.candidates if c.selected)


# ── 1. 자격 판정은 세 속성뿐 — 특정 모션/역할 하드코딩 없음 ───────────────────
def test_eligibility_is_property_based_not_hardcoded():
    # loopable MICRO 아이들 전부 자격 있음 (역할은 제각각: STAND_READY / LIE / SLEEP).
    for mid in ("BREATHING", "LIE_IDLE", "SLEEP_BREATH"):
        assert ms.is_loop_same_start_end(ms.MOTIONS[mid]) is True
    # 비loopable MICRO, 전이, 로코모션은 자격 없음.
    for mid in ("BLINKING", "LOOK_UP", "LIE_DOWN", "COME_CLOSER", "RETURN_HOME", "PET_HEAD"):
        assert ms.is_loop_same_start_end(ms.MOTIONS[mid]) is False


# ── 2. BREATHING: end-capable provider → START_END, 양끝에 같은 키프레임 ───────
def test_breathing_upgrades_to_same_start_end(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)  # STAND_READY 승인
    provider = FakeVideoProvider("seedance", [GOOD()], end_frame=True)
    v = _build_motion(h, "BREATHING", [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "START_END_FRAME"

    assert provider.requests, "provider 가 호출되지 않았다"
    req = provider.requests[0]
    # 시작·끝 이미지가 **둘 다** 실리고 바이트가 동일하다 (같은 역할 키프레임).
    assert req.start_image_bytes is not None
    assert req.end_image_bytes is not None
    assert req.end_image_bytes == req.start_image_bytes


# ── 3. 또 다른 loopable MICRO 아이들(LIE_IDLE)도 동일하게 승격 ─────────────────
def test_second_loopable_micro_idle_upgrades(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage, roles=("LIE",))  # LIE_IDLE 시작 역할
    provider = FakeVideoProvider("seedance", [GOOD()], end_frame=True)
    v = _build_motion(h, "LIE_IDLE", [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "START_END_FRAME"
    req = provider.requests[0]
    assert req.start_image_bytes is not None
    assert req.end_image_bytes == req.start_image_bytes


# ── 4. end-frame 미지원 provider → 단일 시작 I2V 폴백 (하드 실패 아님) ─────────
def test_unsupported_provider_falls_back_to_single_start(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("runway_wan", [GOOD()], end_frame=False)
    v = _build_motion(h, "BREATHING", [provider])
    assert v.status == mv.STATUS_COMPLETE  # 폴백은 성공적으로 전달된다
    assert v.video_strategy == "IMAGE_TO_VIDEO"

    req = provider.requests[0]
    assert req.start_image_bytes is not None
    assert req.end_image_bytes is None  # 끝 이미지 없음 — 단일 시작
    assert any("single-start IMAGE_TO_VIDEO" in w for w in (v.warnings or []))


# ── 5. 비loopable MICRO(BLINKING)는 불변 — 승격 자체가 없다 ────────────────────
def test_non_loopable_micro_unchanged(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()], end_frame=True)  # 능력 있어도
    v = _build_motion(h, "BLINKING", [provider])
    assert v.status == mv.STATUS_COMPLETE
    assert v.video_strategy == "IMAGE_TO_VIDEO"
    req = provider.requests[0]
    assert req.start_image_bytes is not None
    assert req.end_image_bytes is None  # 자격 없음 → 끝 이미지 없음


# ── 6. 승격된 생성은 끝점 QA(첫≈시작, 끝≈같은 시작)를 진단 전용으로 산출 ──────
def test_same_start_end_emits_endpoint_qa_as_diagnostic(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("seedance", [GOOD()], end_frame=True)
    v = _build_motion(h, "BREATHING", [provider])
    sel = _selected(v)
    checks = (sel.qa_result or {}).get("checks") or {}
    # 끝점 체크가 산출된다 — 전이와 같은 판정기 재사용.
    assert "starts_at_start_pose" in checks
    assert "reaches_target_pose" in checks
    # loop_return(기존 루프 QA)은 그대로 남아 있다.
    assert "loop_return" in checks
    # 권위는 이 모션 계약에서 진단 전용이다 — 전달/과금 판정을 바꾸지 않는다
    # (하드 게이트도, 품질 자문도 아니다).
    authority = ((sel.qa_result or {}).get("motion_business_contract") or {}).get(
        "check_authority"
    ) or {}
    assert authority.get("starts_at_start_pose") == "DIAGNOSTIC_ONLY"
    assert authority.get("reaches_target_pose") == "DIAGNOSTIC_ONLY"


# ── 7. 폴백 생성(단일 시작)은 끝점 QA 를 산출하지 않는다 — loop_return 만 ──────
def test_single_start_fallback_skips_endpoint_qa(monkeypatch, storage):
    h, _ = _prepare_pipeline(monkeypatch, storage)
    provider = FakeVideoProvider("runway_wan", [GOOD()], end_frame=False)
    v = _build_motion(h, "BREATHING", [provider])
    checks = (_selected(v).qa_result or {}).get("checks") or {}
    assert "reaches_target_pose" not in checks
    assert "loop_return" in checks  # 기존 루프 QA 는 유지


# ── 8. COME_CLOSER / RETURN_HOME 불변 (자체 전략, same-start-end 아님) ─────────
def test_locomotion_motions_unchanged():
    cc = ms.MOTIONS["COME_CLOSER"]
    assert ms.is_loop_same_start_end(cc) is False
    assert cc.preferred_video_strategy == ms.STRATEGY_I2V

    rh = ms.MOTIONS["RETURN_HOME"]
    assert ms.is_loop_same_start_end(rh) is False
    assert rh.preferred_video_strategy == ms.STRATEGY_START_END  # 파생 시작 자체 계약
