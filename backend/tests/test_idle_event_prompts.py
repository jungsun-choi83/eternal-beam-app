"""
아이들 이벤트(IdleEvent) 프롬프트 모듈 계약.

지키려는 것:
  * 등록된 아이들 이벤트마다 모션 모듈이 **있다** (한쪽만 추가하는 드리프트 방지)
  * 조립 규칙 = <모션> + IDLE_EVENT_COMMON_CONSTRAINT + 이벤트별 부정 목록
    (순서가 계약이다 — 모션이 먼저 와야 주역으로 읽힌다)
  * 이벤트는 BREATH 용 IDLE_COMMON_CONSTRAINT 를 쓰지 **않는다** — 그쪽은 호흡을
    주 모션으로 선언해서 요구 동작과 경합한다
  * 부정 목록이 요구 동작을 밀어내지 않는다 (자기 취소 쌍 금지)
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
    EAR_MOVING_IDLE_EVENTS,
    HEAD_MOVING_IDLE_EVENTS,
    IDLE_COMMON_CONSTRAINT,
    IDLE_EAR_TWITCH_MOTION,
    IDLE_EVENT_AVOID_BASE,
    IDLE_EVENT_COMMON_CONSTRAINT,
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
    assert IDLE_EVENT_COMMON_CONSTRAINT in motion, "이벤트 공통 제약이 빠졌다"
    assert IDLE_EVENT_MOTIONS[event] in motion, "모션 모듈이 빠졌다"


@pytest.mark.parametrize("event", list(IDLE_EVENTS))
def test_idle_events_do_not_use_the_breath_constraint(event: str):
    """
    가장 중요한 회귀 방어.

    IDLE_COMMON_CONSTRAINT 는 "PRIMARY IDLE MOTION: natural breathing" 으로 호흡을
    주 모션으로 **선언**한다. 그걸 이벤트 프롬프트에 붙이면 모션 모듈의 주역 선언과
    경합하고, 실측에서 이긴 쪽은 호흡이었다 — BLINKING 이 눈을 감지 않고
    EAR_TWITCHING 이 BREATH 와 구별되지 않은 원인이다.
    """
    prompt = build_scenario_prompt(KEYFRAME, "any", event)
    assert IDLE_COMMON_CONSTRAINT not in prompt
    assert "primary idle motion" not in prompt.lower()


@pytest.mark.parametrize("event", list(IDLE_EVENTS))
def test_motion_module_comes_before_the_constraint(event: str):
    """
    순서가 계약이다. IDLE_EVENT_COMMON_CONSTRAINT 가 "the micro-event described
    above" 라고 앞을 가리키므로, 순서를 뒤집으면 그 문장이 거짓말이 된다.
    """
    assembled = build_idle_event_prompt(event)
    assert assembled.index(IDLE_EVENT_MOTIONS[event]) < assembled.index(
        IDLE_EVENT_COMMON_CONSTRAINT
    ), "제약이 모션보다 앞에 왔다 — 요구 동작이 뒤로 밀렸다"


@pytest.mark.parametrize("event", list(IDLE_EVENTS))
def test_full_prompt_uses_idle_event_avoid_clause(event: str):
    prompt = build_scenario_prompt(KEYFRAME, "any", event)
    assert idle_event_avoid_clause(event) in prompt
    # 공용 금지(사람·목줄·워터마크)도 반드시 포함돼야 한다.
    assert LUMA_AVOID_CLAUSE in prompt
    # 공통 제약이 두 번 들어가면 안 된다 (조립 지점이 겹치면 프롬프트가 부풀고
    # 모델이 같은 지시를 두 번 읽는다).
    assert prompt.count(IDLE_EVENT_COMMON_CONSTRAINT) == 1


# ── 이벤트 전용 공통 제약의 계약 ──────────────────────────────────────────────


def test_event_constraint_makes_the_micro_event_primary():
    c = IDLE_EVENT_COMMON_CONSTRAINT.lower()
    assert "motion priority" in c
    assert "the micro-event described above is the primary motion" in c
    assert "plainly visible on playback" in c


def test_event_constraint_demotes_breathing_to_background():
    """
    호흡을 **지우면** 안 된다 — 지우면 모델이 얼어붙어 이벤트 한 번 외에 아무것도
    살아 있지 않은 클립이 된다. 격하하되 유지한다.
    """
    c = IDLE_EVENT_COMMON_CONSTRAINT.lower()
    assert "quiet natural breathing continues" in c, "호흡이 통째로 사라졌다"
    assert "background motion only" in c
    assert "stand in for the micro-event" in c


def test_event_constraint_does_not_cancel_the_safe_failure_instruction():
    """
    EAR_TWITCHING / TAIL_WAGGING 은 "귀·꼬리를 확인할 수 없으면 호흡만 남겨라"고
    지시한다. 제약이 "호흡만 있는 클립은 실패다"라고 말하면 자기 취소 쌍이 된다.
    """
    c = IDLE_EVENT_COMMON_CONSTRAINT.lower()
    for banned in ("failed clip", "is a failure", "only breathing is"):
        assert banned not in c, f"안전한 실패 지시와 모순되는 문구: {banned!r}"


def test_event_constraint_keeps_loop_closure():
    """IdleEvent 의 seam-aligned 복귀가 이 약속 위에 서 있다."""
    c = IDLE_EVENT_COMMON_CONSTRAINT.lower()
    assert "begins and ends in the identical resting pose" in c
    for attr in ("pose", "position", "scale", "head orientation"):
        assert attr in c
    assert "completes a full cycle" in c


def test_event_constraint_keeps_camera_identity_and_body_completion():
    """격하·감량 과정에서 진짜 안전장치를 흘리지 않았는지."""
    c = IDLE_EVENT_COMMON_CONSTRAINT.lower()
    assert "framing remain completely fixed" in c
    assert "stays anchored in the same overall resting position" in c
    assert "do not translate, rotate" in c
    assert "body completion:" in c, "신체 완성 정책이 빠졌다"
    assert "do not invent a full unseen body" in c
    assert "do not zoom out or widen the framing" in c


def test_event_constraint_does_not_dwarf_the_motion_module():
    """
    실측 회귀: 예전에는 제약이 3,014자로 최종 프롬프트의 42~46% 를 차지하고
    요구 동작을 담은 모션 모듈은 17~25% 였다. 비중이 다시 뒤집히면 요구 동작이
    묻힌다.
    """
    assert len(IDLE_EVENT_COMMON_CONSTRAINT) < len(IDLE_COMMON_CONSTRAINT)
    for event in IDLE_EVENTS:
        prompt = build_scenario_prompt(KEYFRAME, "any", event)
        share = len(IDLE_EVENT_MOTIONS[event]) / len(prompt)
        assert share > 0.15, f"{event}: 모션 모듈 비중이 {share:.1%} 로 주저앉았다"


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


# ── 귀 부정 목록도 이벤트별로 갈린다 (HEAD_TILTING 과 같은 함정) ───────────────


def test_ear_twitch_is_the_only_ear_moving_event():
    assert EAR_MOVING_IDLE_EVENTS == frozenset({"EAR_TWITCHING"})


def test_base_still_locks_the_ear_set_so_the_removal_target_cannot_drift():
    """
    idle_event_avoid_clause 가 이 문구를 **문자열로 제거**한다. 기본 목록에서
    문구가 바뀌면 제거가 조용히 실패하므로 여기서 존재를 못 박는다.
    """
    assert "ears changing shape or set, " in IDLE_EVENT_AVOID_BASE


def test_ear_twitch_avoid_clause_does_not_forbid_ear_movement():
    """
    HEAD_TILTING 의 'head tilt' 와 똑같은 자기 취소다: "ears changing shape or set"
    은 부정 토큰으로는 귀가 제자리에서 움직이는 것 자체를 억제한다 — 그게
    EAR_TWITCHING 이 요구하는 유일한 동작이다.
    """
    avoid = idle_event_avoid_clause("EAR_TWITCHING").lower()
    assert "ears changing shape or set" not in avoid, "요구 동작이 부정 토큰에 들어갔다"
    # 지속적 변형 가드는 남아 있어야 한다 — 모션 모듈도 같은 것을 금지한다.
    for keep in ("reshaped ears", "enlarged ears", "lengthened ears", "duplicated ears"):
        assert keep in avoid, f"귀 품질 가드가 함께 날아갔다: {keep}"


def test_other_events_still_lock_the_ear_set():
    for event in IDLE_EVENTS:
        if event in EAR_MOVING_IDLE_EVENTS:
            continue
        avoid = idle_event_avoid_clause(event).lower()
        assert "ears changing shape or set" in avoid, f"{event} 이 귀 이동을 허용하고 있다"
        assert IDLE_EVENT_AVOID_BASE in idle_event_avoid_clause(event)


def test_legacy_actions_do_not_use_idle_event_assembly():
    """레거시 4종과 COME_CLOSER 는 아이들 이벤트 경로로 새면 안 된다."""
    for action in (*ACTION_ORDER, *PET_ACTIONS):
        assert not is_idle_event(action), f"{action} 이 아이들 이벤트로 분류됐다"


def test_come_closer_prompt_unaffected_by_idle_event_clause():
    prompt = build_scenario_prompt(KEYFRAME, "any", "COME_CLOSER")
    assert IDLE_EVENT_AVOID_BASE not in prompt
    assert IDLE_COMMON_CONSTRAINT not in prompt
    assert IDLE_EVENT_COMMON_CONSTRAINT not in prompt


def test_breath_path_still_uses_the_breath_constraint():
    """
    반대 방향 회귀. 이벤트를 떼어냈다고 BREATH 가 제약을 잃으면 안 된다 —
    메인 IDLE 경로가 고정 카메라·정체성·루프 종료 상태를 여기서만 받는다.
    """
    prompt = build_scenario_prompt(KEYFRAME, "any", "IDLE")
    assert IDLE_COMMON_CONSTRAINT in prompt
    assert IDLE_EVENT_COMMON_CONSTRAINT not in prompt


def test_breath_constraint_has_no_sentence_fragment():
    """
    한동안 IDLE_COMMON_CONSTRAINT 안에 주어 없는 문장 조각이 남아 있었다
    ("the neck and upper body naturally rather than ..."). 앞의 허용문을 지우면서
    뒷절만 살아남은 것이고, 그 비문이 모든 BREATH 프롬프트에 실려 나갔다.
    """
    c = IDLE_COMMON_CONSTRAINT
    assert ". the neck and upper body naturally" not in c.lower(), "문장 조각이 돌아왔다"
    assert "If the motion description explicitly requests a small head or gaze" in c


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
