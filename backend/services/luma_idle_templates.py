"""
5개 고정 "아이들(Idle)" 모션 템플릿 — 홀로그램용 미세 동작 세트.

Constraint 1 (일관성): 모션 문구 자체는 여기서 절대 바꾸지 않는다. 5개 템플릿 고정.
Constraint 2 (품질): 모든 최종 프롬프트에 TECH_QUALITY_CLAUSE가 반드시 포함된다.
Constraint 3 (초점/안전): 배경 환경 생성 금지 — 강아지 동작에만 집중, 순정 블랙 배경만.

프론트(src/app/services/VideoGenerationService.ts)에도 같은 5개 템플릿을 상수로
동일하게 선언해 두었다 — 문자열은 항상 이 파일과 동기화할 것.
"""

from __future__ import annotations

from .luma_prompts import (
    IDLE_COMMON_CONSTRAINT,
    IDLE_COMMON_CONSTRAINT_HEAD_ROTATION,
    LUMA_AVOID_CLAUSE,
    LUMA_SUBJECT_RULE,
)

# ── 공통 제약 (프리셋 1~4) — luma_prompts.py 에 canonical 정의 ────────────────

# Constraint 1 — 고정 5종 아이들 템플릿 (common + preset-specific).
IDLE_TEMPLATES: dict[str, str] = {
    "IDLE_BREATH": (
        f"{IDLE_COMMON_CONSTRAINT} "
        "Only the chest and rib area rises and falls very subtly, as if breathing "
        "calmly. The movement is barely visible — no more than a few millimeters of "
        "expansion. Head, legs, tail, and camera angle do not move at all."
    ),
    "IDLE_HEAD_TILT": (
        f"{IDLE_COMMON_CONSTRAINT} "
        "Only the head tilts gently to one side, as if curiously reacting to a sound, "
        "then returns to center. The tilt angle should be small (10-15 degrees max). "
        "Body, legs, and torso remain completely still."
    ),
    "IDLE_TAIL_WAG": (
        f"{IDLE_COMMON_CONSTRAINT} "
        "Only the tail wags gently side to side. The rest of the body — head, torso, "
        "legs, and overall pose — stays completely static and unchanged."
    ),
    "IDLE_EAR_FLICK": (
        f"{IDLE_COMMON_CONSTRAINT} "
        "Only the ears flick or twitch slightly, as if reacting to a small sound "
        "nearby. Head position, body, and camera angle remain completely fixed."
    ),
    "IDLE_LOOK_AROUND": (
        f"{IDLE_COMMON_CONSTRAINT_HEAD_ROTATION} "
        "The head turns slowly to glance left, then right, then returns exactly to the "
        "original starting angle by the end of the clip. Body and legs remain still. "
        "The final frame must match the first frame's angle precisely, so the loop "
        "can restart seamlessly."
    ),
}

# 순서 고정 — "순차적으로 적용" 요구사항: 항상 이 순서로 호출한다.
IDLE_TEMPLATE_ORDER: tuple[str, ...] = (
    "IDLE_BREATH",
    "IDLE_HEAD_TILT",
    "IDLE_TAIL_WAG",
    "IDLE_EAR_FLICK",
    "IDLE_LOOK_AROUND",
)

# Constraint 2 — 모든 프롬프트에 반드시 포함되어야 하는 기술 품질 문구.
TECH_QUALITY_CLAUSE = (
    "black background, high resolution, 4k, cinematic, realistic fur texture, "
    "clean edges"
)

# Constraint 3 — 배경/환경 생성 금지, 강아지 동작에만 집중.
FOCUS_ONLY_CLAUSE = (
    "Focus only on the dog's movement and expression. Do not add any "
    "background scenery, objects, props, or environment — pure solid black "
    "void background only, no gradient, no studio floor."
)

# 재시도 시 블랙 배경을 더 강하게 요구하기 위해 덧붙이는 보강 문구.
BLACK_BG_RETRY_BOOST = (
    "Pure absolute black (#000000) background, no gray, no vignette, no "
    "gradient, no visible floor or horizon line — total darkness behind the "
    "dog."
)

# ── 배경이 구워진 장면용 (Phase 19) ──────────────────────────────────────────
#
# 위의 세 문구(TECH_QUALITY_CLAUSE / FOCUS_ONLY_CLAUSE / BLACK_BG_RETRY_BOOST)는
# **보이드 배경**을 전제로 쓰였다. 정본 장면을 입력으로 주면서 저 문구를 그대로
# 두면 프로바이더에게 "이 배경을 지우고 검정으로 만들라"고 말하는 셈이고,
# 실제로 그렇게 나온다 — 고객이 승인한 숲이 검정으로 덮인다.
#
# 그래서 장면 경로는 요구가 정반대다: **입력 배경을 그대로 두라.**
SCENE_QUALITY_CLAUSE = (
    "high resolution, 4k, cinematic, realistic fur texture, clean edges"
)

SCENE_PRESERVE_CLAUSE = (
    "Preserve the input image's background and scene composition exactly as "
    "given. Do not redesign, replace, restyle, or regenerate the environment. "
    "Keep the camera locked — no zoom, no pan, no dolly, no parallax, no "
    "perspective change. Keep the subject's position, scale and framing "
    "identical to the input frame. Background elements may show only the "
    "faintest natural motion (leaves, water, light); no warping, morphing, "
    "melting, or drifting of scenery."
)

#: 배경 보존이 흔들릴 때 덧붙이는 보강 문구(재시도용).
SCENE_PRESERVE_RETRY_BOOST = (
    "The background must remain pixel-stable and identical to the input frame. "
    "Animate only the animal. Treat the scene as a locked-off live-action plate."
)


def is_known_template(template_key: str) -> bool:
    return template_key in IDLE_TEMPLATES


def build_idle_variant_prompt(
    template_key: str,
    *,
    retry_boost: bool = False,
    background_baked: bool = False,
) -> str:
    """
    템플릿 키 → 최종 Luma 프롬프트.

    **모션 문구(IDLE_TEMPLATES)는 두 경로가 공유한다** — 갈라지는 것은 배경에 대한
    요구뿐이다. 행동별로 배경 처리를 따로 만들지 않는다는 요구사항이 여기서도
    같은 모양으로 지켜진다.

    background_baked=False (레거시)
        Constraint 2/3 — 순정 블랙 보이드, 배경 생성 금지.
    background_baked=True (정본 장면)
        입력 배경 보존, 카메라 고정, 환경 재설계 금지.

    retry_boost 는 각 경로의 **자기 실패 모드**를 보강한다: 레거시는 배경이
    검정에서 벗어나는 것이, 장면 경로는 배경이 흔들리는 것이 실패다.
    """
    if not is_known_template(template_key):
        raise ValueError(
            f"Unknown idle template_key: {template_key!r}. "
            f"Valid keys: {list(IDLE_TEMPLATES)}"
        )
    base = IDLE_TEMPLATES[template_key]

    if background_baked:
        parts = [base, f"{SCENE_QUALITY_CLAUSE}.", SCENE_PRESERVE_CLAUSE]
        if retry_boost:
            parts.append(SCENE_PRESERVE_RETRY_BOOST)
    else:
        parts = [base, f"{TECH_QUALITY_CLAUSE}.", FOCUS_ONLY_CLAUSE]
        if retry_boost:
            parts.append(BLACK_BG_RETRY_BOOST)

    parts.append(LUMA_SUBJECT_RULE)
    parts.append(LUMA_AVOID_CLAUSE)
    return " ".join(parts)
