"""
VOICE 액션 계약 (Phase 8) — 실제 System B 런타임 경로의 최종 문자열을 검사한다.

    /api/v1/pet/generate-with-credit
      → credit_luma_batch._submit_one
      → prompt_factory.build_scenario_prompt
      → luma_prompts.build_scenario_luma_prompt
      → ACTION_COMMON_CONSTRAINT + LUMA_ACTION_PROMPTS["VOICE"]

VOICE 의 의미: 익숙한 보호자 목소리를 듣고 **귀 기울이는 순간**.
이벤트 클립이지 루프가 아니다 — 첫/끝 프레임 일치는 요구하지 않되, 끝 자세는
IDLE 로 돌아갈 때 튀지 않을 만큼 시작 자세와 가까워야 한다.

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


def _voice() -> str:
    """System B 가 실제로 Luma 에 보내는 VOICE 최종 문자열."""
    return build_scenario_prompt("<IMG>", PLACE, "VOICE")


def _motion() -> str:
    return LUMA_ACTION_PROMPTS["VOICE"]


# ── 머리 움직임 상한 5~10° ──────────────────────────────────────────────────


def test_head_movement_is_numerically_capped():
    p = _voice().lower()
    assert "5 to 10 degrees" in p, "머리 회전 상한이 숫자로 명시돼야 한다"
    assert "at most" in p


def test_head_movement_is_described_as_small():
    p = _voice().lower()
    assert "only a small change in head orientation" in p
    assert "gentle turn or tilt" in p


def test_no_profile_turn_or_large_rotation():
    p = _voice().lower()
    for needle in (
        "no profile turn",
        "no looking away",
        "no large head rotation",
        "no full turn of the muzzle",
    ):
        assert needle in p, f"빠진 금지문: {needle!r}"


def test_head_keeps_reference_facing_direction():
    assert "keep facing" in _voice().lower()
    assert "essentially the same direction as in the reference" in _voice().lower()


# ── 발은 고정, 걷기/스텝 금지 ───────────────────────────────────────────────


def test_paws_stay_planted():
    p = _voice().lower()
    assert "all paws stay planted exactly where they are" in p


@pytest.mark.parametrize(
    "forbidden",
    [
        "no stepping",
        "no pawing",
        "no shifting weight to another leg",
        "no walking",
        "no standing up",
        "no sitting down",
        "no change of posture",
    ],
)
def test_locomotion_and_posture_changes_forbidden(forbidden: str):
    assert forbidden in _voice().lower(), f"빠진 금지문: {forbidden!r}"


def test_common_constraint_also_bans_translation_and_rotation():
    """공통 제약이 큰 이동/회전을 이미 막고 있어야 한다 (중복 방어)."""
    c = ACTION_COMMON_CONSTRAINT.lower()
    assert "no large translation of the body" in c
    assert "no large rotation of the body" in c
    assert "no walking" in c


# ── 루프가 아니다 ───────────────────────────────────────────────────────────


def test_voice_is_not_a_loop():
    p = _voice().lower()
    # IDLE 전용 문구가 새어 들어오면 안 된다.
    assert "begin and end in the identical resting pose" not in p
    assert "complete a full cycle" not in p
    assert "so the video can loop naturally" not in p


def test_exact_first_last_match_is_explicitly_not_required():
    p = _voice().lower()
    assert "an exact match between the first and last frame is not required" in p


def test_ending_pose_should_still_be_close_to_start():
    """IDLE 복귀가 툭 튀지 않도록 '가깝게'는 요구한다."""
    p = _voice().lower()
    assert "settles back to a posture close to the starting pose" in p
    assert "returning to idle does not look abrupt" in p


def test_voice_does_not_inherit_idle_constraint():
    assert IDLE_COMMON_CONSTRAINT not in _voice()


# ── 강제하지 말아야 할 것들 ─────────────────────────────────────────────────


def test_ear_movement_is_conditional_not_required():
    p = _motion().lower()
    assert "if this dog's ears are naturally expressive" in p
    assert "ears may also stay still" in p
    assert "do not force ear movement" in p


@pytest.mark.parametrize(
    "forbidden",
    ["do not open the mouth", "do not bark", "do not pant", "do not dip or lower the neck"],
)
def test_unwanted_behaviours_are_banned(forbidden: str):
    assert forbidden in _motion().lower(), f"빠진 금지문: {forbidden!r}"


def test_no_camera_movement_requested():
    """카메라는 공통 제약이 고정한다 — VOICE 가 다시 요구하거나 풀면 안 된다."""
    import re

    assert "camera is completely fixed" in _voice().lower()
    m = _motion().lower()
    # 단어 경계로 검사한다 — "pant"(헐떡임) 안의 "pan" 에 걸리면 안 된다.
    # "off-camera"(화면 밖 소리)는 정당한 표현이므로 'camera' 자체는 금지어가 아니다;
    # 카메라 **움직임** 동사만 막는다.
    for moving in ("pan", "zoom", "dolly", "push in", "tracking shot"):
        assert not re.search(rf"\b{re.escape(moving)}\b", m), (
            f"VOICE 본문이 카메라 움직임을 언급한다: {moving!r}"
        )
    # 'camera' 는 오직 소리의 출처(off-camera)로만 등장해야 한다.
    for occurrence in re.findall(r"\S*camera\S*", m):
        assert occurrence.strip(".,") == "off-camera", f"예상치 못한 camera 언급: {occurrence!r}"


def test_upper_body_response_is_small_and_optional():
    p = _motion().lower()
    assert "a very small attentive lift or shift of the chest and upper body is allowed" in p


def test_attentive_intent_is_present():
    p = _motion().lower()
    assert "familiar owner's voice" in p
    assert "off-camera" in p
    assert "quietly attentive" in p
    assert "eyes stay open and alert" in p


# ── 다른 액션은 건드리지 않았다 ─────────────────────────────────────────────


def test_touch_and_nfc_untouched():
    # TOUCH 는 Phase 9 에서 재설계 — 꼬리가 필수에서 조건부로 바뀌었다.
    # 상세 계약은 test_touch_action.py 참고. 여기서는 VOICE 작업이 TOUCH 의
    # 핵심 의미를 건드리지 않았다는 것만 확인한다.
    assert "gently petted on the head" in LUMA_ACTION_PROMPTS["TOUCH"]
    assert "notices that a familiar place has appeared" in LUMA_ACTION_PROMPTS["NFC"]
    # Phase 10B: NFC 도 VOICE 와 동일한 5~10° 상한을 쓴다.
    assert "5 to 10 degrees" in LUMA_ACTION_PROMPTS["NFC"]


def test_idle_untouched():
    idle = LUMA_ACTION_PROMPTS["IDLE"]
    assert "already visible in the reference" in idle
    assert IDLE_COMMON_CONSTRAINT in build_scenario_prompt("<IMG>", PLACE, "IDLE")
