"""
신체 완성(BODY COMPLETION) 정책 — 조건부 완성 계약.

정책:
  토르소 맥락 충분  → 이동/실루엣에 필요한 하체(다리·발·하복부·꼬리)만 완성
  얼굴/머리 초상    → 몸을 지어내지 않는다. 보이는 영역만으로 수행
  언제나            → 중복 사지·여분 발/꼬리·이상 관절·모순된 기하 금지

이 파일이 지키는 핵심은 **자기모순 방지**다. 완성을 요구하면서 같은 프롬프트 안에
"보이지 않는 것을 만들지 말라"를 남겨 두면, 그 부정 토큰이 요구 동작을 밀어낸다 —
COME_CLOSER 가 공용 목록의 'walking' 때문에 전진하지 못했던 것과 같은 함정이다.

유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS
from backend.services.luma_prompts import (
    ACTION_BODY_COMPLETION,
    COME_CLOSER_BODY_COMPLETION,
    IDLE_BODY_COMPLETION,
    body_completion_rule,
)
from backend.services.prompt_factory import build_scenario_prompt

#: 신체 완성 규칙이 도달해야 하는 모든 액션.
ALL_GENERATED = (*ACTION_ORDER, "COME_CLOSER", *IDLE_EVENTS)

#: 테마 독립 액션은 place='any', 레거시 4종은 실제 장소 키를 쓴다.
_THEME_INDEPENDENT = {"COME_CLOSER", *IDLE_EVENTS}


def _prompt(action: str) -> str:
    place = "any" if action in _THEME_INDEPENDENT else "01_snow_forest"
    return build_scenario_prompt("<IMG>", place, action)


def _main_idle_prompt() -> str:
    """메인 BREATH 경로(/generate-pet-video)의 최종 문자열."""
    from backend.services.luma_service import build_idle_action_prompts

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 180, 150)).save(buf, format="JPEG")
    return build_idle_action_prompts(buf.getvalue())[0]


# ── 규칙이 모든 생성 프롬프트에 도달한다 ─────────────────────────────────────


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_body_completion_reaches_every_generated_action(action: str):
    assert "BODY COMPLETION:" in _prompt(action), f"{action} 에 완성 규칙이 없다"


def test_body_completion_reaches_the_main_breath_path():
    """
    크레딧 경로만 고치고 메인 경로를 빠뜨리는 것이 이 코드베이스의 반복된 실패다
    (IDLE_COMMON_CONSTRAINT 가 그렇게 사라져 있었다).
    """
    assert "BODY COMPLETION:" in _main_idle_prompt()


# ── 조건부 — 양쪽 분기가 모두 명시돼 있다 ────────────────────────────────────


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_completion_is_gated_on_torso_context(action: str):
    p = _prompt(action).lower()
    assert "shows enough torso and body context" in p, "완성 조건이 없다"
    assert "infer the missing anatomy reliably" in p


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_portrait_sources_must_not_get_an_invented_body(action: str):
    """가장 중요한 안전 장치 — 얼굴만 있는 사진에서 몸을 지어내면 다른 개가 된다."""
    p = _prompt(action).lower()
    assert "face-only or head-only portrait" in p
    assert "do not invent a full unseen body" in p
    assert "visible head, neck, and upper-body region only" in p


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_identity_is_preserved_alongside_completion(action: str):
    p = _prompt(action).lower()
    assert "preserve all visible anatomy, proportions, fur pattern, breed traits" in p
    assert "physically plausible and consistent with the visible body" in p


# ── 품질 가드는 정책과 무관하게 언제나 유지된다 ──────────────────────────────


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_duplicated_and_malformed_geometry_always_forbidden(action: str):
    p = _prompt(action).lower()
    for guard in (
        "duplicated limbs",
        "extra paws",
        "extra tails",
        "malformed joints",
        "contradictory body geometry",
    ):
        assert guard in p, f"{action}: 품질 가드 누락 {guard!r}"


# ── 자기모순 금지 (이 파일의 핵심) ───────────────────────────────────────────


@pytest.mark.parametrize("action", ALL_GENERATED)
def test_no_self_cancelling_prohibition(action: str):
    """
    완성을 요구하는 프롬프트에 "보이지 않는 것을 만들지 말라"가 남아 있으면
    모델은 둘 중 하나를 무시한다 — 실측상 부정 토큰이 이긴다.
    """
    p = _prompt(action).lower()
    contradictions = [
        "invent anatomy that is not visible",
        "do not reveal, generate, extend, or invent legs",
        "revealed body parts",
        "invented body parts",
        "invented tail",
        "revealed hips or hind legs",
        "do not add a tail that is not clearly visible",
    ]
    hits = [c for c in contradictions if c in p]
    assert not hits, f"{action}: 완성 지시를 상쇄하는 금지문 {hits}"


def test_main_breath_path_has_no_self_cancelling_prohibition():
    p = _main_idle_prompt().lower()
    assert "invent anatomy that is not visible" not in p
    assert "do not reveal, generate, extend, or invent legs" not in p


# ── 완성은 프레임 안에서만 (줌아웃으로 몸을 드러내지 않는다) ──────────────────


def test_completion_never_widens_the_shot():
    """
    하체를 드러내려고 카메라를 빼면 접지(pet-grounding)와 이음매가 동시에 깨진다.
    완성은 기존 프레임의 빈 영역에 그려야 한다.
    """
    p = _main_idle_prompt().lower()
    assert "do not zoom out, widen the framing" in p
    assert "drawn inside the existing frame, never by pulling the camera back" in p


# ── 계열별 목적 문구 ─────────────────────────────────────────────────────────


def test_purpose_wording_differs_by_family():
    """
    COME_CLOSER 만 "이동"을 목적으로 쓴다. 아이들 계열에 같은 문구를 붙이면
    제자리 미세 동작 클립에 걷기를 암시하게 된다.
    """
    assert "forward locomotion" in COME_CLOSER_BODY_COMPLETION
    assert "forward locomotion" not in IDLE_BODY_COMPLETION
    assert "forward locomotion" not in ACTION_BODY_COMPLETION
    assert "resting silhouette" in IDLE_BODY_COMPLETION
    assert "resting silhouette" in ACTION_BODY_COMPLETION


def test_idle_events_do_not_get_locomotion_wording():
    for event in IDLE_EVENTS:
        assert "forward locomotion" not in _prompt(event), f"{event} 에 이동 문구가 새어 들어갔다"


def test_rule_builder_is_parameterised_not_duplicated():
    """문구가 한 곳에서 나와야 계열이 늘어도 갈라지지 않는다."""
    r = body_completion_rule(purpose="X_PURPOSE", fallback="Y_FALLBACK")
    assert "X_PURPOSE" in r and "Y_FALLBACK" in r
    assert "BODY COMPLETION:" in r


# ── 레거시 계약 불변 ─────────────────────────────────────────────────────────


def test_action_order_unchanged():
    assert ACTION_ORDER == ("IDLE", "TOUCH", "VOICE", "NFC")
