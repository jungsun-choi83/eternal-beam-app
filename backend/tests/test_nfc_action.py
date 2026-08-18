"""
NFC 액션 계약 (Phase 10B 재설계) — 실제 System B 런타임 경로의 최종 문자열 검사.

    /api/v1/pet/generate-with-credit
      → prompt_factory.build_scenario_prompt
      → luma_prompts.build_scenario_luma_prompt
      → ACTION_COMMON_CONSTRAINT + LUMA_ACTION_PROMPTS["NFC"]

배경: 1차 NFC 배치는 3/3 전부 BAD 였다 — 90~180° 회전 + 걸어 나감.
원인은 각도 상한이 아니라 동사였다. "look around" 는 '주위를 살펴라'로,
"sniff" 는 '냄새를 쫓아라'로 읽혔고 "to one side and back" 은 왕복 회전을
지시했다. 같은 조건에서 VOICE(5~10°, 그 동사들 없음)는 프로필 턴 0/3 이었다.

이 테스트는 그 세 가지 표현이 **되돌아오지 못하게** 못 박는다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import pytest

from backend.services.luma_prompts import (
    ACTION_COMMON_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT,
    LUMA_ACTION_PROMPTS,
)
from backend.services.prompt_factory import build_scenario_prompt

PLACE = "01_snow_forest"


def _nfc() -> str:
    return build_scenario_prompt("<IMG>", PLACE, "NFC")


def _motion() -> str:
    return LUMA_ACTION_PROMPTS["NFC"]


# ── 실패를 유발했던 표현이 사라졌는가 (회귀 방지) ───────────────────────────


@pytest.mark.parametrize(
    "banned",
    ["look around", "look-around", "looks around", "to one side and back", "10 to 15 degrees"],
)
def test_surveying_cues_removed(banned: str):
    assert banned not in _motion().lower(), f"1차 실패를 유발한 표현이 남아 있다: {banned!r}"


def test_no_sniff_or_scent_motion_is_offered():
    p = _motion().lower()
    # 허용문이 없어야 한다.
    assert "a subtle sniff is allowed" not in p
    assert "tiny nose and muzzle movement" not in p
    assert "scent trail" not in p
    # 대신 명시적 금지문이 있어야 한다.
    assert "do not sniff" in p
    assert "do not search" in p
    assert "do not follow a scent" in p
    assert "do not lower the nose or neck toward the ground" in p


# ── 5~10° 상한 (VOICE 와 동일한 검증된 값) ──────────────────────────────────


def test_head_movement_capped_at_5_to_10_degrees():
    p = _nfc().lower()
    assert "5 to 10 degrees" in p
    assert "at most" in p


def test_head_change_is_described_as_very_small():
    assert "only a very small change in head orientation" in _motion().lower()


def test_nfc_now_matches_voice_head_bound():
    """검증된 값으로 되돌렸는지 — 두 액션이 같은 상한을 쓴다."""
    assert "5 to 10 degrees" in LUMA_ACTION_PROMPTS["VOICE"]
    assert "5 to 10 degrees" in LUMA_ACTION_PROMPTS["NFC"]


# ── 얼굴이 항상 보여야 한다 ─────────────────────────────────────────────────


def test_face_must_stay_visible_every_frame():
    p = _motion().lower()
    assert "face must remain visible to the camera in every single frame" in p


@pytest.mark.parametrize(
    "forbidden",
    [
        "never turn the head away from the camera",
        "never turn the body away from the camera",
        "no rear view",
        "no back view",
        "no profile turn",
        "no looking away",
        "no large head rotation",
    ],
)
def test_away_and_profile_turns_forbidden(forbidden: str):
    assert forbidden in _nfc().lower(), f"빠진 금지문: {forbidden!r}"


def test_body_rotation_fully_forbidden():
    """'큰 회전 금지'가 아니라 '회전 금지'여야 한다 — 1차 실패가 회전이었다."""
    assert "no rotation of the body" in _motion().lower()


# ── 몸은 완전히 고정 ────────────────────────────────────────────────────────


def test_body_completely_anchored():
    assert "the body stays completely anchored" in _motion().lower()


def test_paws_stay_planted():
    assert "all paws stay planted exactly where they are" in _nfc().lower()


@pytest.mark.parametrize(
    "forbidden",
    ["no stepping", "no pawing", "no shifting weight to another leg", "no walking"],
)
def test_locomotion_forbidden(forbidden: str):
    assert forbidden in _nfc().lower(), f"빠진 금지문: {forbidden!r}"


@pytest.mark.parametrize(
    "forbidden",
    ["no standing up", "no sitting down", "no lying down", "no crouching", "no change of posture"],
)
def test_posture_transitions_forbidden(forbidden: str):
    assert forbidden in _nfc().lower(), f"빠진 금지문: {forbidden!r}"


# ── 루프가 아니다 ───────────────────────────────────────────────────────────


def test_nfc_is_not_a_loop():
    p = _nfc().lower()
    assert "begin and end in the identical resting pose" not in p
    assert "complete a full cycle" not in p
    assert "so the video can loop naturally" not in p


def test_exact_loop_closure_not_required():
    assert "an exact match between the first and last frame is not required" in _nfc().lower()


def test_ending_close_to_start_still_required():
    p = _nfc().lower()
    assert "settles back to a posture close to the starting pose" in p
    assert "returning to idle does not look abrupt" in p


def test_nfc_does_not_inherit_idle_constraint():
    assert IDLE_COMMON_CONSTRAINT not in _nfc()


# ── 의미/분위기 ─────────────────────────────────────────────────────────────


def test_recognition_intent_is_present():
    p = _motion().lower()
    assert "notices that a familiar place has appeared" in p
    assert "calm recognition" in p


def test_mood_is_calm_not_alarmed():
    assert "calm and content, not alarmed or excited" in _motion().lower()


def test_expression_and_ears_are_the_allowed_channels():
    p = _motion().lower()
    assert "if this dog's ears are naturally expressive" in p
    assert "ears may also stay still" in p
    assert "a soft change of expression in the eyes is allowed" in p


def test_no_person_or_leash():
    p = _motion().lower()
    assert "no person accompanying" in p
    assert "no leash" in p


def test_common_constraint_still_attached():
    assert ACTION_COMMON_CONSTRAINT in _nfc()


def test_no_camera_movement_requested():
    """카메라 고정은 공통 제약 담당 — 본문의 'camera' 는 정면 유지 문맥뿐이어야."""
    import re

    assert "camera is completely fixed" in _nfc().lower()
    m = _motion().lower()
    for moving in ("pan", "zoom", "dolly", "push in", "tracking shot"):
        assert not re.search(rf"\b{re.escape(moving)}\b", m), f"카메라 움직임 언급: {moving!r}"


# ── 다른 액션은 건드리지 않았다 ─────────────────────────────────────────────


def test_other_actions_untouched():
    touch = LUMA_ACTION_PROMPTS["TOUCH"].lower()
    voice = LUMA_ACTION_PROMPTS["VOICE"].lower()
    assert "gently petted on the head" in touch
    assert "if a tail is naturally visible in the reference" in touch
    assert "familiar owner's voice" in voice
    assert "already visible in the reference" in LUMA_ACTION_PROMPTS["IDLE"].lower()
