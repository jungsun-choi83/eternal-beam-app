"""
메인 메모리얼 IDLE 프롬프트 — "보이는 것만 애니메이션" 원칙 검증.

원칙: 최종 레퍼런스 컷아웃에 **실제로 보이는** 해부구조만 움직인다.
보이지 않는 부위를 드러내거나 새로 만들어 내지 않는다.

이 테스트는 실제 전송 경로의 최종 문자열을 검사한다:
    /api/generate-pet-video → build_idle_action_prompts() → LUMA_PROMPT_IDLE
                            → LUMA_ACTION_PROMPTS["IDLE"] → IDLE_COMMON_CONSTRAINT
유료 API 는 호출하지 않는다.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend.services.luma_prompts import (
    IDLE_COMMON_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT_HEAD_ROTATION,
    LUMA_ACTION_PROMPTS,
    LUMA_PROMPT_ACTION,
    LUMA_PROMPT_IDLE,
)


def _jpeg(color=(200, 180, 150)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    return buf.getvalue()


def _final_idle_prompt() -> str:
    """실제 메인 경로가 Wan/Luma 로 보내는 최종 IDLE 문자열."""
    from backend.services.luma_service import build_idle_action_prompts

    idle, _action = build_idle_action_prompts(_jpeg())
    return idle


# ── 최종 프롬프트가 담아야 할 의미 5가지 ────────────────────────────────────


@pytest.mark.parametrize(
    "meaning, needles",
    [
        ("preserve exact visible crop", ["preserve the visible crop"]),
        ("do not zoom out", ["do not zoom out"]),
        # 신체 완성 정책 전환: "보이지 않는 것 금지" → "조건부 완성".
        # 계약은 사라진 게 아니라 **조건이 붙었다** — 그 조건을 검사한다.
        ("body completion is gated on torso context", ["body completion:"]),
        ("portrait sources must not get an invented body", ["do not invent a full unseen body"]),
        ("animate only visible regions", ["already visible in the reference"]),
    ],
)
def test_final_idle_prompt_carries_meaning(meaning: str, needles: list[str]):
    p = _final_idle_prompt().lower()
    assert any(n in p for n in needles), f"최종 IDLE 프롬프트에 '{meaning}' 의미가 없다"


def test_final_idle_prompt_conditions_motion_on_visibility():
    """전신/부분/얼굴 어느 컷아웃이든 안전하도록 조건부로 지시해야 한다."""
    p = _final_idle_prompt().lower()
    assert "if the chest or upper torso is visibly present" in p
    assert "if the visible crop is mainly the head or face" in p


# ── 보이지 않는 부위의 존재를 전제하지 않아야 한다 ──────────────────────────


def test_idle_no_longer_asserts_full_body_parts_exist():
    """
    예전 문구는 다리·발·몸통·꼬리가 반드시 존재한다고 전제했다.
    머리만 있는 컷아웃에서는 성립하지 않으므로 사라져야 한다.
    """
    joined = (IDLE_COMMON_CONSTRAINT + " " + LUMA_ACTION_PROMPTS["IDLE"]).lower()
    forbidden = [
        "legs, paws, torso, tail, ears, head, and chest must retain",
        "the paws remain planted",
        "the lower body and torso do not move",
        "head, legs, tail, and camera angle do not move at all",
        "only the chest and rib area rises and falls",
    ]
    for f in forbidden:
        assert f not in joined, f"전신을 전제하는 옛 문구가 남아 있다: {f!r}"


def test_body_parts_are_never_assumed_to_exist():
    """
    IDLE 본문은 신체 부위의 **존재를 전제**하면 안 된다.

    예전에는 "만들어 내지 말라"는 금지문 형태만 허용했다. 지금은 조건부 완성이
    정책이라 금지문 자체가 사라졌고, 대신 완성 여부는 공통 제약의 BODY COMPLETION
    조항이 판단한다. 여기서 지킬 것은 원래 의도 그대로다 — 부위가 반드시 있다고
    말하는 문장이 없어야 한다(머리만 있는 컷아웃에서 깨진다).
    """
    idle = LUMA_ACTION_PROMPTS["IDLE"].lower()
    for assumes_existence in (
        "the paws remain planted",
        "the lower body and torso do not move",
        "legs, paws, torso, tail, ears, head, and chest must retain",
    ):
        assert assumes_existence not in idle, f"존재를 전제하는 문구: {assumes_existence!r}"
    # 반쯤 그려진 부위는 여전히 금지 — 완성하든 없든 둘 중 하나여야 한다.
    assert "half-drawn" in idle


def test_common_constraint_is_visibility_scoped():
    c = IDLE_COMMON_CONSTRAINT.lower()
    assert "stay exactly as visible" in c
    assert "every body part that is visible in the reference" in c
    assert "same fur pattern, same lighting, same background" in c


def test_common_constraint_anchors_the_body():
    """
    몸통은 고정된다 — 이동도 회전도 없다.

    예전에는 "no rotation, no turning" 이라는 **정확한 문구**를 요구했는데, 그 문구는
    이제 의도와 충돌한다: 현재 IDLE 은 "작고 느린 호기심 어린 고개 돌림"을 **일부러
    허용한다**(LUMA_ACTION_PROMPTS["IDLE"]). 머리까지 싸잡아 금지하면 요구 동작을
    부정 토큰으로 밀어내게 된다.

    그래서 문구가 아니라 **불변식**을 검사한다: 몸통 단위의 이동·회전 금지는
    그대로 살아 있어야 한다.
    """
    c = IDLE_COMMON_CONSTRAINT.lower()
    assert "stays anchored in the same overall resting position" in c
    assert "do not translate, rotate" in c
    assert "pose drift" in c
    # 머리 미세 동작은 여전히 허용된다 — 이 테스트가 그것까지 막으면 안 된다.
    idle = LUMA_ACTION_PROMPTS["IDLE"].lower()
    assert "do not turn or translate the torso" in idle, "몸통 고정은 모션 쪽에도 있어야 한다"


# ── 경로 확인 + 건드리지 않기로 한 것들 ─────────────────────────────────────


def test_main_path_receives_updated_prompt_unchanged():
    """build_idle_action_prompts 는 배경 문구만 덧붙이고 본문을 그대로 전달한다."""
    idle = _final_idle_prompt()
    assert idle.startswith(LUMA_PROMPT_IDLE)
    assert LUMA_ACTION_PROMPTS["IDLE"] in idle
    assert IDLE_COMMON_CONSTRAINT in idle
    tail = idle[len(LUMA_PROMPT_IDLE) :]
    assert "background" in tail.lower() and len(tail) < 120, "배경 접미사 외에는 변형이 없어야 한다"


def test_head_rotation_constraint_unchanged():
    c = IDLE_COMMON_CONSTRAINT_HEAD_ROTATION
    assert "Head rotation is allowed only as specifically described below" in c
    assert "identical to the original photo's exact angle" in c


def test_other_action_prompts_untouched():
    assert "gently petted on the head" in LUMA_ACTION_PROMPTS["TOUCH"]
    # VOICE 는 Phase 8 에서 재설계됐다 — 상세 계약은 test_voice_action.py 참고.
    assert "familiar owner's voice" in LUMA_ACTION_PROMPTS["VOICE"]
    # NFC 는 Phase 10 에서 재설계 — 상세 계약은 test_nfc_action.py 참고.
    assert "notices that a familiar place has appeared" in LUMA_ACTION_PROMPTS["NFC"]
    assert "takes a few playful steps toward camera" in LUMA_PROMPT_ACTION
    # 이 세 개는 IDLE 공통 제약을 쓰지 않는다
    for k in ("TOUCH", "VOICE", "NFC"):
        assert IDLE_COMMON_CONSTRAINT not in LUMA_ACTION_PROMPTS[k]
