"""
COME_CLOSER 프리미엄 액션 (Phase 15).

핵심 계약:
  * 레거시 4종 계약을 전혀 건드리지 않는다 — ACTION_ORDER / 4코인 / device sync.
  * ACTION_COMMON_CONSTRAINT 를 **쓰지 않는다** — 그쪽의 "같은 스케일" 조항이
    COME_CLOSER 의 목적(스케일 증가)과 정면충돌하기 때문.
  * 레퍼런스 영상(다가오기action.mp4) 기준: **실제 전진 이동 + 원근 확대**.
    전신 프레이밍 → 클로즈업으로 끝난다. 걷기는 금지가 아니라 **요구사항**이다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import pytest

from backend.scenarios.pet_scenarios import (
    ACTION_ORDER,
    ACTIONS,
    ACTIONS_EN,
    CREDIT_COST_PER_PLACE_SET,
    PREMIUM_ACTIONS,
    storage_object_name,
)
from backend.services.luma_prompts import (
    ACTION_COMMON_CONSTRAINT,
    COME_CLOSER_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT,
    LUMA_ACTION_PROMPTS,
)
from backend.services.prompt_factory import build_scenario_prompt

PLACE = "01_snow_forest"


def _cc() -> str:
    return build_scenario_prompt("<IMG>", PLACE, "COME_CLOSER")


def _motion() -> str:
    return LUMA_ACTION_PROMPTS["COME_CLOSER"]


# ── 레거시 4종 계약 불변 ────────────────────────────────────────────────────


def test_action_order_unchanged():
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")
    assert "COME_CLOSER" not in ACTION_ORDER


def test_come_closer_is_a_premium_action_outside_the_set():
    """
    COME_CLOSER 는 레거시 4종 세트 밖의 프리미엄 액션이다.

    예전에는 `PREMIUM_ACTIONS == ("COME_CLOSER",)` 로 못 박았는데, 그 뒤 아이들
    이벤트(BLINKING/EAR_TWITCHING/HEAD_TILTING/TAIL_WAGGING)가 같은 튜플에
    합류했다 — 둘 다 "ACTION_ORDER 밖 · 테마 독립"이라 저장/라우팅 규칙이 같기
    때문이다. 그래서 **집합의 크기**가 아니라 원래 지키려던 계약을 검사한다:
    COME_CLOSER 는 유일한 PetAction 이고, 프리미엄 집합의 어느 것도 레거시
    4종에 끼어들지 않는다.
    """
    from backend.scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS

    assert PET_ACTIONS == ("COME_CLOSER",), "COME_CLOSER 외의 액션이 늘었다"
    assert "COME_CLOSER" in PREMIUM_ACTIONS
    assert set(PREMIUM_ACTIONS) == set(PET_ACTIONS) | set(IDLE_EVENTS)
    # 핵심 계약 — 프리미엄/아이들 어느 것도 레거시 4종에 섞이지 않는다.
    assert not (set(PREMIUM_ACTIONS) & set(ACTION_ORDER))


def test_credit_contract_unchanged():
    assert CREDIT_COST_PER_PLACE_SET == 4


def test_legacy_action_prompts_untouched():
    assert "already visible in the reference" in LUMA_ACTION_PROMPTS["IDLE"]
    assert "gently petted on the head" in LUMA_ACTION_PROMPTS["TOUCH"]
    assert "familiar owner's voice" in LUMA_ACTION_PROMPTS["VOICE"]
    assert "notices that a familiar place has appeared" in LUMA_ACTION_PROMPTS["NFC"]


def test_storage_naming_is_theme_independent():
    """
    COME_CLOSER 는 검정 플레이트 위 펫만 생성한다 → 배경과 무관 → 경로에서
    장소를 뺀다. 어떤 place 로 만들어도 같은 파일 하나다.
    """
    assert storage_object_name(PLACE, "COME_CLOSER") == "COME_CLOSER.mp4"
    assert storage_object_name("web_fresh_forest", "COME_CLOSER") == "COME_CLOSER.mp4"


def test_legacy_actions_stay_place_scoped():
    """레거시 4종은 장소별 자산 그대로 — 기기/NFC 가 이 전제로 돈다."""
    for a in ("IDLE", "TOUCH", "VOICE", "NFC"):
        assert storage_object_name(PLACE, a) == f"SNOW_FOREST_{a}.mp4"


def test_prompt_builder_accepts_come_closer():
    assert "COME_CLOSER" in ACTIONS and "COME_CLOSER" in ACTIONS_EN
    assert _cc()  # KeyError 없이 조립돼야 한다


# ── 전용 제약 블록 ──────────────────────────────────────────────────────────


def test_uses_dedicated_constraint_not_the_shared_one():
    p = _cc()
    assert COME_CLOSER_CONSTRAINT in p
    assert ACTION_COMMON_CONSTRAINT not in p, "'같은 스케일' 조항이 목적과 충돌한다"
    assert IDLE_COMMON_CONSTRAINT not in p


def test_shared_constraint_still_used_by_the_other_actions():
    for a in ("TOUCH", "VOICE", "NFC"):
        assert ACTION_COMMON_CONSTRAINT in build_scenario_prompt("<IMG>", PLACE, a)


def test_identity_preserved():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "preserve the dog's identity, fur colour, markings" in c
    # 레퍼런스는 끝에서 몸이 프레임을 넘어간다 — "전신 유지"는 더 이상 요구하지
    # 않는다. 대신 해부 자체가 망가지지 않게 막는다.
    assert "no duplicated limbs" in c
    assert "no extra limbs" in c


def test_body_may_leave_frame_at_the_end():
    """
    예전 제약의 "Keep the dog's full body visible" 가 레퍼런스의 마지막
    클로즈업을 직접 금지하고 있었다 — 그 조항이 되살아나면 접근이 잘린다.
    """
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "keep the dog's full body visible" not in c
    assert "parts of its body extend beyond the frame edges" in c
    assert "do not shrink the dog, pull back, or reframe" in c


def test_face_stays_visible():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "face must remain visible to the camera throughout the approach" in c
    for n in ("no rear view", "no back view", "no meaningful profile turn"):
        assert n in c


# 예전에는 "걷기 금지"를 못박았다. 실측 결과 그 금지가 접근 자체를 억제해
# 스케일 증가가 눈에 띄지 않았고, PM 이 "작은 자연스러운 전진/달려오는 동작"을
# 명시적으로 요구하도록 스펙을 바꿨다. 그래서 이제는 **전진이 요구사항**이다.
@pytest.mark.parametrize(
    "required",
    [
        "primary action",
        "walks and eagerly trots",
        "real forward steps",
        "travel through space toward the camera",
        "not merely change size while standing in one spot",
    ],
)
def test_forward_approach_is_required(required: str):
    assert required in COME_CLOSER_CONSTRAINT.lower(), f"빠진 전진 지시: {required!r}"


@pytest.mark.parametrize(
    "suppressor",
    [
        "do not animate a walking cycle",
        "no stepping",
        "no strides",
        "no paws lifting alternately",
        "only their apparent size changes",
        "legs stay in the same relative arrangement",
    ],
)
def test_old_locomotion_ban_is_gone(suppressor: str):
    """이 문구들이 되살아나면 접근 동작이 다시 억제된다."""
    assert suppressor not in COME_CLOSER_CONSTRAINT.lower(), f"억제 문구 부활: {suppressor!r}"


def test_face_not_occluded_by_legs():
    """전진은 허용하되 검정 키잉을 깨는 자기 가림은 막는다."""
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "never fully cross in front of" in c
    assert "hide the eyes and muzzle" in c


def test_turning_away_forbidden():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "no meaningful profile turn" in c
    assert "no turning away from the camera" in c


def test_camera_stays_fixed_scale_comes_from_the_dog():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "camera is completely fixed" in c
    assert "no pan, no tilt, no zoom, no dolly" in c
    assert "no reframing" in c
    # 크기 증가의 출처가 개의 전진이어야 한다 — 줌/달리/스케일이 아니라.
    assert "comes from the dog's own forward movement through space" in c
    assert "never simulate the approach by scaling a stationary dog" in c


def test_avoid_clause_no_longer_negates_walking():
    """
    공용 부정 목록에는 'walking' 이 있다. 그게 COME_CLOSER 프롬프트로 새어 들어가
    원하는 동작을 부정 토큰으로 밀어내고 있었다.
    """
    from backend.services.luma_prompts import COME_CLOSER_AVOID_CLAUSE, LUMA_AVOID_CLAUSE

    assert "walking" in LUMA_AVOID_CLAUSE, "공용 목록은 그대로 두어야 한다"
    assert "walking" not in COME_CLOSER_AVOID_CLAUSE

    p = build_scenario_prompt("<IMG>", PLACE, "COME_CLOSER")
    assert COME_CLOSER_AVOID_CLAUSE in p
    assert LUMA_AVOID_CLAUSE not in p


@pytest.mark.parametrize(
    "term",
    ["walking", "walk", "steps", "stepping", "trotting", "running", "locomotion", "strides"],
)
def test_no_locomotion_negatives_anywhere_in_assembled_prompt(term: str):
    """
    부정 절만 보면 놓친다 — LUMA_SUBJECT_RULE 의 'no owner walking the dog' 도
    같은 토큰을 흘려보냈다. 조립된 프롬프트의 **부정 구간 전체**를 검사한다.
    """
    p = build_scenario_prompt("<IMG>", PLACE, "COME_CLOSER")
    tail = p.split("Motion:", 1)[1].split(". ", 1)[-1].lower()
    negative_zone = tail[tail.find("animate only the single dog") :] if "animate only the single dog" in tail else tail
    assert f" {term}" not in f" {negative_zone}", (
        f"부정 구간에 이동 관련 토큰 {term!r} 이 남아 있다 — 접근 동작을 억제한다"
    )


@pytest.mark.parametrize(
    "neg",
    [
        "static pose", "frozen subject", "barely moving subject", "weak approach",
        "constant framing", "retreating", "backing away", "moving away from camera",
        "shrinking subject", "excessive lateral drift", "profile turn", "rear turn",
        "body turning away", "face occlusion", "eyes hidden", "muzzle hidden",
        "malformed anatomy", "duplicated limbs", "camera zoom", "camera movement",
        "sudden scale jump", "scaling in place",
    ],
)
def test_failure_modes_are_negative_tokens(neg: str):
    from backend.services.luma_prompts import COME_CLOSER_AVOID_CLAUSE

    assert neg in COME_CLOSER_AVOID_CLAUSE, f"빠진 실패 모드 부정어: {neg!r}"


def test_stationary_result_is_forbidden_somewhere_in_the_delivered_prompt():
    """
    "제자리에서 커지기만 하는" 결과 금지 — COME_CLOSER 의 핵심 실패 모드다.

    예전에는 이 계약을 `"stationary dog" in COME_CLOSER_AVOID_CLAUSE` 로 확인했는데,
    그 문구는 부정 목록이 아니라 **본문 제약**으로 옮겨갔다
    ("Never simulate the approach by scaling a stationary dog…"). 같은 문구를 양쪽에
    중복시키는 대신, 실제로 모델에 전달되는 **조립된 프롬프트** 수준에서 검사한다 —
    어느 섹션에 있든 계약이 전달되기만 하면 된다.
    """
    prompt = build_scenario_prompt("<IMG>", "any", "COME_CLOSER").lower()
    # 정지/제자리 확대 금지가 어떤 형태로든 들어 있어야 한다.
    stationary_guards = [
        "stationary dog",
        "scaling in place",
        "growing without moving",
        "static pose",
        "frozen subject",
    ]
    present = [g for g in stationary_guards if g in prompt]
    assert present, "조립된 프롬프트에 '제자리 정지' 실패 모드 방어가 하나도 없다"
    # 그리고 실제 전진 요구가 함께 있어야 한다 — 금지만으로는 동작이 생기지 않는다.
    assert "travel through space toward the camera" in prompt
    assert "not merely change size while" in prompt


def test_other_actions_keep_the_shared_clauses_untouched():
    """레거시 4종의 프롬프트는 한 글자도 달라지면 안 된다."""
    from backend.services.luma_prompts import LUMA_AVOID_CLAUSE, LUMA_SUBJECT_RULE

    for a in ("TOUCH", "VOICE", "NFC"):
        p = build_scenario_prompt("<IMG>", PLACE, a)
        assert LUMA_AVOID_CLAUSE in p
        assert LUMA_SUBJECT_RULE in p
        assert "no owner walking the dog" in p, "공용 주어 규칙이 바뀌면 안 된다"


def test_come_closer_subject_rule_keeps_the_meaning():
    """'walking' 토큰만 피하고, 사람이 개를 데리고 있는 장면 금지는 유지한다."""
    from backend.services.luma_prompts import COME_CLOSER_SUBJECT_RULE

    assert "no owner walking the dog" not in COME_CLOSER_SUBJECT_RULE
    assert "no owner leading or handling the dog" in COME_CLOSER_SUBJECT_RULE
    assert "no leash" in COME_CLOSER_SUBJECT_RULE.lower()


def test_drift_gate_stays_disabled_for_come_closer():
    """
    COME_CLOSER 는 **의도적으로** 커진다. 일반 드리프트/면적 게이트를 켜면
    좋은 클립까지 전부 탈락한다(§10: 면적 +42%, 높이 +30% 실측).
    게이트를 전역으로 켜더라도 이 액션은 예외여야 한다.
    """
    from backend.services.generated_motions_service import MotionJobRow, validate_candidate

    job = MotionJobRow(
        session_id="s1", user_id="u1", pet_id="p1", place_key="01_snow_forest",
        action_id="COME_CLOSER", luma_generation_id="g1",
    )
    accepted, meta = validate_candidate(job, b"not-a-real-mp4")
    assert accepted is True, "COME_CLOSER 후보가 드리프트로 차단되면 안 된다"
    assert meta.get("gate_enforced") is False


def test_lateral_drift_minimised():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "keep lateral drift small" in c
    assert "no diagonal path across the frame" in c


def test_black_plate_preserved():
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "pure solid black background only" in c
    assert "no people, no human hands or arms, no leash" in c


# ── 허용되어야 하는 것 ──────────────────────────────────────────────────────


def test_no_conservative_percentage_target():
    """
    레퍼런스 실측: 크로마 면적 2.97배(≈선형 1.7배 이상, 끝에서 프레임을 벗어나
    잘리므로 실제로는 더 크다). ~20% 목표는 한 자릿수만큼 작았다.
    프레이밍으로 지시하고 보수적인 퍼센트 목표는 두지 않는다.
    """
    m = _motion().lower()
    for stale in ("20 percent", "18 to 24 percent", "head-to-paw height"):
        assert stale not in m, f"보수적 목표가 남아 있다: {stale!r}"


def test_framing_transition_is_the_target():
    """전신 → 클로즈업. 이게 레퍼런스의 실제 결과다."""
    m = _motion().lower()
    assert "full-body framing" in m
    assert "head and chest fill most of the frame" in m
    assert "parts of its body pass beyond the frame edges" in m
    assert "face dominating the frame" in m


def test_three_beats_present():
    m = _motion().lower()
    for beat in ("beat 1", "beat 2", "beat 3"):
        assert beat in m


def test_no_idle_section_at_the_start():
    """
    실측 회귀: 생성된 클립이 앞 2.5초를 그냥 서 있다가 다가왔다. 그게 화면에서는
    BREATH 가 이어지는 것처럼 보여 "두 영상이 하나로 합쳐졌다"는 인상을 줬고,
    더블탭이 무시된 것처럼 보였다(실제로는 액션이 재생 중이었다).
    액션 클립은 **첫 프레임부터 이미 움직이고 있어야** 한다.
    """
    m = _motion().lower()
    assert "already moving toward the camera" in m
    assert "mid-step from frame one" in m
    for banned in ("no standing still", "no pause", "no idle beat",
                   "no breathing-in-place section"):
        assert banned in m, f"빠진 금지문: {banned!r}"


def test_idle_opening_is_a_negative_token():
    from backend.services.luma_prompts import COME_CLOSER_AVOID_CLAUSE as A

    for neg in ("standing still at the start", "idle opening",
                "breathing in place", "delayed start", "motionless first half"):
        assert neg in A, f"빠진 부정어: {neg!r}"


def test_growth_starts_at_the_first_second_and_accelerates():
    """
    원근 가속은 유지하되 **0 에서 시작하면 안 된다**.
    예전 문구("멀리 있는 동안은 완만")가 앞부분을 그냥 정지로 만들어 버렸다 —
    레퍼런스의 평평한 구간은 '멀리서 걷는 중'이었지 '서 있는 것'이 아니었다.
    """
    m = _motion().lower()
    assert "grows larger in frame from the very first second" in m
    assert "faster and faster as the gap closes" in m
    assert "gentle while it is still far off" not in m, "정지 오프닝을 허락하는 문구"
    c = COME_CLOSER_CONSTRAINT.lower()
    assert "in motion from the first frame" in c
    assert "must not open with the dog standing still" in c


def test_understating_the_growth_is_explicitly_rejected():
    m = _motion().lower()
    assert "dramatically closer than the opening" in m
    assert "not edge slightly nearer" in m


def test_small_vertical_bounce_is_allowed():
    assert "natural vertical bounce of an eager gait is welcome" in _motion().lower()


def test_ear_and_tail_are_conditional_not_required():
    m = _motion().lower()
    assert "if this dog's ears are naturally expressive" in m
    assert "ears may also stay still" in m
    assert "if a tail is naturally visible in the reference" in m
    # 신체 완성 정책이 조건부로 바뀌었다: 꼬리는 "무조건 금지"가 아니라
    # **몸통 맥락이 충분할 때만** 완성 가능하다. 초상 사진에서 지어내는 것은 여전히 금지.
    assert "if no tail can be established under the body-completion rule" in m
    assert "do not guess one" in m


def test_recognition_intent_present():
    m = _motion().lower()
    assert "sees the viewer" in m
    assert "lights up with recognition" in m
    assert "comes all the way to" in m


def test_retreat_is_forbidden():
    m = _motion().lower()
    assert "never retreat" in m
    assert "never move backwards or further away" in m


def test_no_barking():
    assert "do not bark" in _motion().lower()


# ── 프로바이더/파이프라인 재사용 ────────────────────────────────────────────


def test_provider_resolves_for_come_closer(monkeypatch):
    from backend.services.video_generation import resolve_action_provider

    for k in ("VIDEO_PROVIDER", "VIDEO_PROVIDER_ACTION", "VIDEO_PROVIDER_COME_CLOSER"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_action_provider("COME_CLOSER") == "luma"
    monkeypatch.setenv("VIDEO_PROVIDER_COME_CLOSER", "wan_turbo")
    assert resolve_action_provider("COME_CLOSER") == "wan_turbo"


def test_candidate_naming_works_for_come_closer():
    from backend.services.generated_motions_service import candidate_object_name

    # 후보 경로도 canonical 규칙을 따라 장소가 빠진다. 시도 번호와 job id 로
    # 구분되므로 서로 덮어쓰지 않는다(기존 재시도 동작 그대로).
    assert (
        candidate_object_name(PLACE, "COME_CLOSER", 1, "job123")
        == "candidates/COME_CLOSER_1_job123.mp4"
    )
    assert (
        candidate_object_name(PLACE, "COME_CLOSER", 2, "job456")
        == "candidates/COME_CLOSER_2_job456.mp4"
    )
    # 레거시는 그대로 장소별.
    assert (
        candidate_object_name(PLACE, "IDLE", 1, "j")
        == "candidates/SNOW_FOREST_IDLE_1_j.mp4"
    )


def test_migration_allows_come_closer():
    from pathlib import Path

    sql = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    body = (sql / "20260810020000_allow_come_closer_action.sql").read_text(encoding="utf-8")
    assert "COME_CLOSER" in body
    for legacy in ("IDLE", "TOUCH", "VOICE", "NFC"):
        assert f"'{legacy}'" in body, "기존 4종이 제약에서 빠지면 안 된다"
