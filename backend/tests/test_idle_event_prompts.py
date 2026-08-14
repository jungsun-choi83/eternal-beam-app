"""
아이들 이벤트(IdleEvent) 프롬프트 모듈 계약.

지키려는 것:
  * 등록된 아이들 이벤트마다 모션 모듈이 **있다** (한쪽만 추가하는 드리프트 방지)
  * 조립 규칙 = IDLE_COMMON_CONSTRAINT + <모션> + 이벤트별 부정 목록
  * 레거시 4종/COME_CLOSER 의 조립은 아이들 이벤트 경로로 새지 않는다
  * ACTION_ORDER 불변

유료 API 는 호출하지 않는다 — 문자열 조립만 검사한다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import (
    ACTION_ORDER,
    IDLE_EVENTS,
    PET_ACTIONS,
    PREMIUM_ACTIONS,
    is_theme_independent_action,
    storage_object_name,
)
from backend.services.luma_prompts import (
    HEAD_MOVING_IDLE_EVENTS,
    IDLE_COMMON_CONSTRAINT,
    IDLE_EAR_TWITCH_MOTION,
    IDLE_EVENT_AVOID_BASE,
    IDLE_EVENT_MOTIONS,
    IDLE_HEAD_TILT_MOTION,
    IDLE_TAIL_WAG_MOTION,
    LUMA_AVOID_CLAUSE,
    build_idle_event_prompt,
    idle_event_avoid_clause,
    is_idle_event,
)
from backend.services.prompt_factory import build_scenario_prompt

KEYFRAME = "https://example.test/pet-black-plate.png"


def test_action_order_unchanged():
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")


def test_registered_idle_events_all_have_motion_modules():
    """파이썬 레지스트리와 프롬프트 모듈이 어긋나면 생성 시점에 ValueError 로 죽는다."""
    for event in IDLE_EVENTS:
        assert event in IDLE_EVENT_MOTIONS, f"{event} 의 모션 모듈이 없다"
        assert is_idle_event(event)


def test_no_orphan_motion_modules():
    """반대 방향 — 모듈만 있고 등록되지 않은 이벤트가 남아 있지 않은지."""
    assert set(IDLE_EVENT_MOTIONS) == set(IDLE_EVENTS)


def test_idle_events_are_outside_action_order():
    for event in IDLE_EVENTS:
        assert event not in ACTION_ORDER
        assert event in PREMIUM_ACTIONS
        assert event not in PET_ACTIONS, "아이들 이벤트가 액션 목록에 섞였다"


def test_idle_events_are_theme_independent():
    """place_id='any' 로 접혀 펫당 한 행이 된다."""
    for event in IDLE_EVENTS:
        assert is_theme_independent_action(event)
        assert storage_object_name("any", event) == f"{event}.mp4"


@pytest.mark.parametrize("event", list(IDLE_EVENTS))
def test_idle_event_prompt_assembles_from_modules(event: str):
    motion = build_idle_event_prompt(event)
    assert IDLE_COMMON_CONSTRAINT in motion, "공통 제약이 빠졌다"
    assert IDLE_EVENT_MOTIONS[event] in motion, "모션 모듈이 빠졌다"


@pytest.mark.parametrize("event", list(IDLE_EVENTS))
def test_full_prompt_uses_idle_event_avoid_clause(event: str):
    prompt = build_scenario_prompt(KEYFRAME, "any", event)
    assert idle_event_avoid_clause(event) in prompt
    assert IDLE_EVENT_AVOID_BASE in prompt
    # 공용 금지(사람·목줄·워터마크)도 반드시 포함돼야 한다.
    assert LUMA_AVOID_CLAUSE in prompt
    # 공통 제약이 두 번 들어가면 안 된다 (조립 지점이 겹치면 프롬프트가 부풀고
    # 모델이 같은 지시를 두 번 읽는다).
    assert prompt.count(IDLE_COMMON_CONSTRAINT) == 1


def test_ear_twitch_motion_protects_ear_anatomy():
    """
    귀는 이 이벤트에서 가장 망가지기 쉬운 부위다. 형태 보존과 '안전한 실패'
    지시가 빠지면 모델이 귀를 세우거나 머리를 재구성한다.
    """
    m = IDLE_EAR_TWITCH_MOTION.lower()
    for phrase in ["preserve ear anatomy", "do not straighten", "enlarge"]:
        assert phrase in m, f"귀 형태 보존 문구 누락: {phrase}"
    # 안전한 실패: 귀가 안 보이면 시도하지 말고 호흡만.
    assert "not clearly visible" in m
    assert "quiet natural breathing for the whole clip" in m
    # 머리 움직임 금지 — 귀를 움직이려다 머리를 돌리는 것이 가장 흔한 실패다.
    assert "do not turn, tilt, nod" in m
    assert "reorient the head" in m


def test_ear_twitch_allows_one_or_both_ears():
    m = IDLE_EAR_TWITCH_MOTION.lower()
    assert "one ear" in m
    assert "both ears may twitch" in m


def test_avoid_clause_targets_ear_failure_modes():
    a = IDLE_EVENT_AVOID_BASE.lower()
    for phrase in ["reshaped ears", "enlarged ears", "perked-up ears", "deformed head"]:
        assert phrase in a, f"귀 실패 모드 부정 누락: {phrase}"


def test_legacy_actions_do_not_use_idle_event_assembly():
    """레거시 4종과 COME_CLOSER 는 아이들 이벤트 경로로 새면 안 된다."""
    for action in (*ACTION_ORDER, *PET_ACTIONS):
        assert not is_idle_event(action), f"{action} 이 아이들 이벤트로 분류됐다"


def test_come_closer_prompt_unaffected_by_idle_event_clause():
    prompt = build_scenario_prompt(KEYFRAME, "any", "COME_CLOSER")
    assert IDLE_EVENT_AVOID_BASE not in prompt
    assert IDLE_COMMON_CONSTRAINT not in prompt


# ── Phase 4 — HEAD_TILTING / TAIL_WAGGING ────────────────────────────────────


def test_head_tilt_is_the_only_head_moving_event():
    """
    부정 목록이 이벤트마다 갈리는 근거. 여기 없는 이벤트는 머리를 고정한다.
    """
    assert HEAD_MOVING_IDLE_EVENTS == frozenset({"HEAD_TILTING"})


def test_head_tilt_avoid_clause_does_not_forbid_tilting():
    """
    가장 위험한 회귀: 공용 부정 목록의 'head tilt' 가 HEAD_TILTING 이 요구하는
    동작 자체를 밀어낸다 (COME_CLOSER 가 'walking' 때문에 전진을 못 했던 것과 같다).
    """
    avoid = idle_event_avoid_clause("HEAD_TILTING").lower()
    assert "head tilt" not in avoid, "요구 동작이 부정 토큰에 들어갔다"
    assert "head roll" not in avoid
    # 돌아보기(yaw)는 여전히 막아야 한다 — tilt 와 turn 은 다른 동작이다.
    assert "head turn" in avoid
    assert "profile view" in avoid


def test_other_idle_events_still_lock_the_head():
    for event in IDLE_EVENTS:
        if event in HEAD_MOVING_IDLE_EVENTS:
            continue
        avoid = idle_event_avoid_clause(event).lower()
        assert "head tilt" in avoid, f"{event} 이 머리 기울임을 허용하고 있다"
        assert "head turn" in avoid, f"{event} 이 머리 돌리기를 허용하고 있다"


def test_head_tilt_motion_distinguishes_roll_from_turn():
    m = IDLE_HEAD_TILT_MOTION.lower()
    assert "sideways roll" in m, "tilt 를 roll 로 명시하지 않으면 모델이 고개를 돌린다"
    assert "it is not a turn" in m
    assert "muzzle keeps pointing in the same direction" in m
    # 끝에 기울인 채로 멈추면 휴지 자세가 어긋나 이음매 복귀가 깨진다.
    assert "do not hold the tilt at the end" in m
    assert "returns smoothly to its original upright orientation" in m


def test_tail_wag_protects_tail_anatomy():
    m = IDLE_TAIL_WAG_MOTION.lower()
    assert "preserve tail anatomy" in m
    for phrase in ["lengthen", "thicken", "straighten", "duplicate"]:
        assert phrase in m, f"꼬리 형태 보존 문구 누락: {phrase}"


def test_tail_wag_fails_gracefully_when_tail_not_visible():
    """
    꼬리는 잘리거나 가려지는 일이 잦다. 없는 꼬리를 만들라고 하면 엉덩이·뒷다리까지
    재구성한다 — 그럴 바에는 호흡만 남기는 편이 낫다 (EAR_TWITCHING 과 같은 철학).
    """
    m = IDLE_TAIL_WAG_MOTION.lower()
    # 정책 전환 후: 꼬리는 **몸통 맥락이 있으면** 완성될 수 있다. 그러나 얼굴만
    # 있는 초상에서 추측하는 것은 여전히 금지이고, 그때는 호흡만 남긴다.
    assert "cannot be established" in m
    assert "face-only or head-only" in m
    assert "quiet natural breathing for the whole clip" in m
    assert "calm breathing clip is a correct result" in m
    assert "never acceptable" in m


def test_tail_wag_forbids_whole_body_substitute():
    m = IDLE_TAIL_WAG_MOTION.lower()
    for phrase in ["whole-body sway", "hip swing", "torso rotation"]:
        assert phrase in m, f"몸통 흔들기 대체 금지 문구 누락: {phrase}"


def test_avoid_clause_targets_tail_failure_modes():
    a = IDLE_EVENT_AVOID_BASE.lower()
    # "invented tail" / "revealed hips" 는 조건부 완성 정책과 충돌하므로 제거됐다.
    # 남아야 하는 것은 **품질** 가드다 — 중복·기형은 정책과 무관하게 언제나 실패다.
    for phrase in ["duplicated tail", "extra tail", "malformed tail", "malformed joints", "extra paws"]:
        assert phrase in a, f"꼬리/사지 품질 가드 누락: {phrase}"


def test_all_four_idle_events_registered():
    assert set(IDLE_EVENTS) == {
        "BLINKING",
        "EAR_TWITCHING",
        "HEAD_TILTING",
        "TAIL_WAGGING",
    }
