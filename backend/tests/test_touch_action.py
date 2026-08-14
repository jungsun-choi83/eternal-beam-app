"""
TOUCH 액션 계약 (Phase 9) — 실제 System B 런타임 경로의 최종 문자열을 검사한다.

    /api/v1/pet/generate-with-credit
      → prompt_factory.build_scenario_prompt
      → luma_prompts.build_scenario_luma_prompt
      → ACTION_COMMON_CONSTRAINT + LUMA_ACTION_PROMPTS["TOUCH"]

TOUCH 의 의미: 머리를 쓰다듬는 손길에 대한 **닻 내린(anchored) 반응**.
IDLE 보다 살짝 크고 이동 동작보다 훨씬 작다. 이벤트 클립이지 루프가 아니다.

가장 중요한 변경: 꼬리가 **필수 → 조건부**. 예전 "slight tail wag" 는 꼬리가 안
보이는 컷아웃에서 없는 해부구조를 만들어 내라는 뜻이었다.

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


def _touch() -> str:
    return build_scenario_prompt("<IMG>", PLACE, "TOUCH")


def _motion() -> str:
    return LUMA_ACTION_PROMPTS["TOUCH"]


# ── 발은 고정 ───────────────────────────────────────────────────────────────


def test_paws_stay_planted():
    assert "all paws stay planted exactly where they are" in _touch().lower()


# ── 꼬리는 조건부 (핵심 변경) ───────────────────────────────────────────────


def test_tail_is_conditional_not_required():
    p = _motion().lower()
    assert "if a tail is naturally visible in the reference" in p
    assert "a slight gentle wag is allowed" in p


def test_tail_must_not_be_guessed_from_a_portrait():
    """
    꼬리는 이제 **조건부 완성** 대상이다(BODY COMPLETION 정책):
    몸통 맥락이 충분하면 완성 가능, 얼굴만 있는 초상에서는 여전히 금지.

    예전 계약("보이지 않으면 무조건 금지")은 정책 변경으로 대체됐지만,
    막으려던 실패 모드 — 초상에서 없는 꼬리를 지어내기 — 는 그대로 막혀 있어야 한다.
    """
    p = _motion().lower()
    assert "if no tail can be established under the body-completion rule" in p
    assert "do not guess one" in p


def test_old_mandatory_tail_wording_is_gone():
    """예전 문구는 꼬리 흔들기를 무조건 요구했다 — 되돌아오면 안 된다."""
    p = _motion().lower()
    assert "happy expression, soft nuzzle, slight tail wag" not in p
    # 'slight tail wag' 가 남아 있다면 반드시 조건절 안이어야 한다.
    idx = p.find("slight gentle wag")
    assert idx != -1
    assert "if a tail is naturally visible" in p[max(0, idx - 120) : idx]


def test_ear_response_is_also_conditional():
    p = _motion().lower()
    assert "if this dog's ears are naturally expressive" in p
    assert "ears may also stay still" in p
    assert "do not force ear movement" in p


# ── 걷기 / 스텝 금지 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "forbidden",
    ["no stepping", "no pawing", "no shifting weight to another leg", "no walking"],
)
def test_locomotion_forbidden(forbidden: str):
    assert forbidden in _touch().lower(), f"빠진 금지문: {forbidden!r}"


def test_response_is_smaller_than_locomotion():
    p = _motion().lower()
    assert "stays anchored" in p
    assert "far smaller than any walking, approaching, or locomotion movement" in p


def test_expressiveness_is_above_resting():
    assert "slightly more expressive than resting" in _motion().lower()


# ── 자세 전환 금지 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "forbidden",
    ["no standing up", "no sitting down", "no lying down", "no change of posture"],
)
def test_posture_transitions_forbidden(forbidden: str):
    assert forbidden in _touch().lower(), f"빠진 금지문: {forbidden!r}"


# ── 큰 회전 금지 ────────────────────────────────────────────────────────────


def test_no_large_body_rotation():
    assert "no large rotation of the body" in _touch().lower()


def test_no_large_head_rotation_or_profile_turn():
    p = _touch().lower()
    for needle in ("no profile turn", "no looking away", "no large head rotation"):
        assert needle in p, f"빠진 금지문: {needle!r}"


def test_head_movement_is_small_and_anchored():
    p = _motion().lower()
    assert "only a very small nuzzle or lean of the head" in p
    assert "essentially the same direction as in the reference" in p


# ── 루프가 아니다 ───────────────────────────────────────────────────────────


def test_touch_is_not_a_loop():
    p = _touch().lower()
    assert "begin and end in the identical resting pose" not in p
    assert "complete a full cycle" not in p
    assert "so the video can loop naturally" not in p


def test_exact_loop_closure_not_required():
    assert "an exact match between the first and last frame is not required" in _touch().lower()


def test_ending_close_to_start_still_required():
    p = _touch().lower()
    assert "settles back to a posture close to the starting pose" in p
    assert "returning to idle does not look abrupt" in p


def test_touch_does_not_inherit_idle_constraint():
    assert IDLE_COMMON_CONSTRAINT not in _touch()


# ── 사람/손 금지 유지 ───────────────────────────────────────────────────────


def test_no_human_hand_shown():
    p = _motion().lower()
    assert "do not show a human hand, arm, or any person" in p
    assert "only the dog's reaction is visible" in p


def test_common_constraint_still_attached():
    assert ACTION_COMMON_CONSTRAINT in _touch()


# ── 다른 액션은 건드리지 않았다 ─────────────────────────────────────────────


def test_voice_and_nfc_and_idle_untouched():
    assert "familiar owner's voice" in LUMA_ACTION_PROMPTS["VOICE"]
    assert "5 to 10 degrees" in LUMA_ACTION_PROMPTS["VOICE"]
    assert "notices that a familiar place has appeared" in LUMA_ACTION_PROMPTS["NFC"]
    # Phase 10B: NFC 는 VOICE 와 같은 5~10° 상한으로 되돌아왔다.
    assert "5 to 10 degrees" in LUMA_ACTION_PROMPTS["NFC"]
    assert "already visible in the reference" in LUMA_ACTION_PROMPTS["IDLE"]
