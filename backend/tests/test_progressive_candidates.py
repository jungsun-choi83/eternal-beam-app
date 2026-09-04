"""점진적 후보 생성 + 조기 중단 — Phase 4/5/6 공통 정책 계약.

루프 구조(후보 N 의 QA 종결 전에 N+1 미제출, 재개 가능, 영수증)는 기존
빌더 테스트들이 지킨다. 여기서는 **정책 값**을 고정한다: 첫 PASS 즉시 중단,
모션 클래스별 상한, env 우선순위, 로컬(test 프로파일) 1회.
"""

from __future__ import annotations

import pytest

from backend.services import canonical_pet_service as canonical
from backend.services import motion_video_service as mv

_ENV_KEYS = [
    "CANONICAL_MAX_PRIMARY", "CANONICAL_MAX_FALLBACK", "CANONICAL_STOP_AFTER_PASSES",
    "PHASE6_MAX_PRIMARY", "PHASE6_MAX_FALLBACK", "PHASE6_STOP_AFTER_PASSES",
    "PHASE6_MAX_PRIMARY_MICRO", "PHASE6_MAX_PRIMARY_LOCOMOTION",
    "PHASE6_MAX_FALLBACK_MICRO", "PHASE6_GENERATION_PROFILE",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 프로파일 기본(benchmark)에서 클래스 기본값이 보이게 고정한다.
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "benchmark")


# ── Phase 4 canonical (+ Phase 5 keyframe 이 같은 정책을 빌린다) ─────────────


def test_canonical_stops_after_first_pass_by_default():
    policy = canonical.candidate_policy()
    assert policy["stop_after_passes"] == 1  # 첫 PASS → 즉시 중단
    assert policy["max_primary"] == 3
    assert policy["max_fallback"] == 2


def test_canonical_env_still_overrides(monkeypatch):
    monkeypatch.setenv("CANONICAL_STOP_AFTER_PASSES", "2")
    monkeypatch.setenv("CANONICAL_MAX_PRIMARY", "1")
    policy = canonical.candidate_policy()
    assert policy["stop_after_passes"] == 2
    assert policy["max_primary"] == 1


# ── Phase 6 motion — 클래스 인지 상한 ────────────────────────────────────────


def test_motion_class_aware_defaults():
    assert mv.candidate_policy("MICRO")["max_primary"] == 2
    assert mv.candidate_policy("TRANSITION")["max_primary"] == 3
    assert mv.candidate_policy("INTERACTION")["max_primary"] == 3
    assert mv.candidate_policy("LOCOMOTION")["max_primary"] == 4
    assert mv.candidate_policy("MICRO")["max_fallback"] == 1
    assert mv.candidate_policy("LOCOMOTION")["max_fallback"] == 2
    # 조기 중단은 클래스와 무관하게 1 — 첫 PASS 에서 멈춘다.
    for cls in ("MICRO", "TRANSITION", "INTERACTION", "LOCOMOTION"):
        assert mv.candidate_policy(cls)["stop_after_passes"] == 1


def test_motion_unknown_class_falls_back_to_hard_defaults():
    assert mv.candidate_policy("")["max_primary"] == 3
    assert mv.candidate_policy(None)["max_primary"] == 3
    assert mv.candidate_policy("FUTURE_CLASS")["max_primary"] == 3


def test_motion_global_env_overrides_class_default(monkeypatch):
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "5")
    assert mv.candidate_policy("MICRO")["max_primary"] == 5
    assert mv.candidate_policy("LOCOMOTION")["max_primary"] == 5


def test_motion_class_env_beats_global_env(monkeypatch):
    monkeypatch.setenv("PHASE6_MAX_PRIMARY", "5")
    monkeypatch.setenv("PHASE6_MAX_PRIMARY_MICRO", "1")
    assert mv.candidate_policy("MICRO")["max_primary"] == 1
    assert mv.candidate_policy("LOCOMOTION")["max_primary"] == 5
    monkeypatch.setenv("PHASE6_MAX_FALLBACK_MICRO", "1")
    monkeypatch.setenv("PHASE6_MAX_FALLBACK", "4")
    assert mv.candidate_policy("MICRO")["max_fallback"] == 1
    assert mv.candidate_policy("TRANSITION")["max_fallback"] == 4


def test_local_test_profile_defaults_to_single_attempt(monkeypatch):
    """로컬 개발(PHASE6_GENERATION_PROFILE=test)은 기본 1회 — env 로만 올린다."""
    monkeypatch.setenv("PHASE6_GENERATION_PROFILE", "test")
    for cls in ("MICRO", "TRANSITION", "LOCOMOTION"):
        assert mv.candidate_policy(cls)["max_primary"] == 1
        assert mv.candidate_policy(cls)["max_fallback"] == 1
    # 명시 env 는 test 프로파일보다 세다.
    monkeypatch.setenv("PHASE6_MAX_PRIMARY_LOCOMOTION", "3")
    assert mv.candidate_policy("LOCOMOTION")["max_primary"] == 3
