"""
액션 키프레임 스펙 (Phase 5) — 어떤 액션이 어떤 시작 포즈를 요구하는가의 정본.

── 명명 원칙: 네 번째 액션 체계는 없다 ─────────────────────────────────────
키프레임 **역할**(role)은 포즈 축이다. 액션 id 는 기존 레지스트리의 것만 쓴다:
  * backend/scenarios/pet_scenarios.py  (ACTION_ORDER, IDLE_EVENTS, PET_ACTIONS)
  * backend/services/luma_idle_templates.py  (IDLE_TEMPLATE_ORDER)
  * BREATHING — 웹 홈 상태 (src/lib/pet-runtime-events.ts 의 IDLE_HOME_STATE)
supported_action_ids 는 저 레지스트리들에서 **import 로** 채운다 — 문자열을 새로
만들지 않는다. 하나의 키프레임을 여러 액션이 공유한다 (생성 비용 절약).

── 벤치마크 역할 (초기 5개, 요구 16) ───────────────────────────────────────
NEUTRAL_IDLE 은 현재 런타임의 사실상 전부를 감당한다. LIE/SLEEP/LOOK_UP/HAPPY 는
미래 액션의 시작 포즈다 — 지금은 매핑이 비어 있고, 신원이 안정되면 채워진다.

PET_HEAD(쓰다듬기): v1 은 사람 손을 생성하지 않는다 (요구 9). TOUCH 액션은
NEUTRAL_IDLE 키프레임(= PET_HEAD_START)을 쓴다 — 손의 등장 여부는 Phase 6 의
영상 모델이 결정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..scenarios.pet_scenarios import ACTION_ORDER, IDLE_EVENTS, PET_ACTIONS
from .luma_idle_templates import IDLE_TEMPLATE_ORDER

# v2 (2026-09-10): NEUTRAL_IDLE 이 포즈를 다시 고르지 않는다 — "sitting or
# standing" 선택 문구 제거, 정본(Canonical)의 기존 자세를 그대로 유지하도록
# 서술. 버전을 올리는 이유: 키프레임 재사용 게이트(action_keyframe_service 의
# skip_if_unchanged/resume)가 analyzer_versions 로 이 값을 비교한다 — 안 올리면
# 앉기로 쏠린 기존 NEUTRAL_IDLE 키프레임이 새 문구 아래에서도 영원히 재사용된다.
# v2 확장 (같은 날, Phase 4 — v2 아티팩트가 생성되기 전이라 버전 재범프 없음):
# STAND_READY 역할 신설(이동/기립 전이의 명시적 서기 시작점, COME_CLOSER +
# LIE_DOWN 이관), SLEEP 서술에 LIE 와의 신체 구성 차이(머리 내림/말림) 명시.
KEYFRAME_SPEC_VERSION = "keyframe-spec-v2"
KEYFRAME_PROMPT_VERSION = "keyframe-prompt-v1"

#: 웹 홈 상태 id (pet-runtime-events.ts IDLE_HOME_STATE 미러 — TS 와 동일 문자열).
BREATHING_HOME_STATE = "BREATHING"


@dataclass(frozen=True)
class KeyframeRole:
    role: str
    #: 요구 포즈 — 프롬프트의 포즈 절이자 pose QA 의 기준 서술.
    required_pose: str
    #: 이 포즈에서 보여야 하는 신체 영역.
    required_visibility: tuple[str, ...]
    #: 이후 영상 모션의 크기 (Phase 6 계획용): micro | small | medium
    body_motion_complexity: str
    #: 신원 앵커로 쓸 정본 소스: "raw" (정본 원본) — cutout 은 보조.
    preferred_canonical_source: str
    #: Phase 6 영상 생성 호환 메타.
    video_compat: dict[str, Any]
    #: 이 키프레임을 시작 포즈로 쓸 수 있는 **기존** 액션 id 들.
    supported_action_ids: tuple[str, ...] = field(default_factory=tuple)


#: 시작 포즈가 LIE 인 액션 — NEUTRAL_IDLE 이 아니라 LIE 역할이 담당한다.
_LIE_START_ACTIONS: tuple[str, ...] = ("LIE_IDLE", "STAND_UP")

#: 시작 포즈가 **명시적 서기**인 액션 — STAND_READY 역할이 담당한다 (Phase 4).
#: NEUTRAL_IDLE 은 keyframe-spec-v2 부터 정본 자세를 물려받아 앉아 있을 수
#: 있으므로, 이동(COME_CLOSER)과 기립→눕기 전이(LIE_DOWN)의 시작점으로 쓸 수
#: 없다 — 걷기 시작은 네 발로 서 있어야 한다. (RUN/WALK 는 motion_spec 에만
#: 있는 미래 모션 id 라 여기 액션 레지스트리 매핑에는 등장하지 않는다.)
_STAND_READY_ACTIONS: tuple[str, ...] = ("COME_CLOSER", "LIE_DOWN")

_NEUTRAL_ACTIONS: tuple[str, ...] = (
    tuple(ACTION_ORDER)          # IDLE, TOUCH(=PET_HEAD_START), VOICE, NFC
    # PET_ACTIONS 중 중립 시작만 — LIE 시작은 LIE 역할로, 서기 시작은
    # STAND_READY 역할로 (아래).
    + tuple(
        a for a in PET_ACTIONS
        if a not in _LIE_START_ACTIONS and a not in _STAND_READY_ACTIONS
    )
    + (BREATHING_HOME_STATE,)    # 웹 홈 상태
    + tuple(IDLE_EVENTS)         # BLINKING, EAR_TWITCHING, HEAD_TILTING, TAIL_WAGGING
    + tuple(IDLE_TEMPLATE_ORDER) # IDLE_BREATH … IDLE_LOOK_AROUND
)

KEYFRAME_ROLES: dict[str, KeyframeRole] = {
    "NEUTRAL_IDLE": KeyframeRole(
        role="NEUTRAL_IDLE",
        # 홈/기준 포즈다 — 포즈를 **새로 고르는 단계가 아니다** (spec-v2).
        # 이전 문구 "sitting or standing" 은 이미지 모델에게 선택권을 줬고
        # 앉기로 강하게 쏠렸다. 이제 정본의 기존 자세를 그대로 물려받는다:
        # 정본이 서 있으면 서 있고, 앉아 있으면 앉아 있다.
        required_pose=(
            "a calm neutral pose that keeps the pet's existing body posture "
            "exactly as shown in the canonical reference image — if the "
            "reference pet is standing it stays standing, if sitting it stays "
            "sitting; do not switch to any other posture — body relaxed, head "
            "level and facing slightly toward the camera, eyes open, mouth "
            "relaxed"
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="micro",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": True, "motion_class": "idle"},
        supported_action_ids=_NEUTRAL_ACTIONS,
    ),
    "STAND_READY": KeyframeRole(
        role="STAND_READY",
        # 이동/기립 전이의 시작점 (Phase 4). NEUTRAL_IDLE 과 달리 자세를
        # 물려받지 않는다 — 정본이 앉아 있어도 이 역할은 반드시 서 있다.
        # 걷기·달리기·눕기 전이는 네 발로 선 자세에서만 자연스럽게 시작한다.
        required_pose=(
            "standing upright and alert on all four legs, weight evenly "
            "balanced over all four paws, ready to move, tail in a natural "
            "position, head level and facing slightly toward the camera, "
            "eyes open"
        ),
        required_visibility=("face", "full_body", "front_paws"),
        body_motion_complexity="medium",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": False, "motion_class": "locomotion"},
        supported_action_ids=_STAND_READY_ACTIONS,
    ),
    "LIE": KeyframeRole(
        role="LIE",
        required_pose=(
            "lying down naturally on its belly, front legs extended forward, head "
            "upright and awake, relaxed and comfortable"
        ),
        required_visibility=("face", "full_body", "front_paws"),
        body_motion_complexity="micro",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": True, "motion_class": "idle"},
        # 자세 전이 상용화 (2026-09-08): LIE 에서 **시작**하는 액션들.
        supported_action_ids=_LIE_START_ACTIONS,
    ),
    "SLEEP": KeyframeRole(
        role="SLEEP",
        # LIE 와 다른 신체 구성이어야 한다 (Phase 4) — LIE 는 머리를 들고 깨어
        # 있는 자세, SLEEP 은 머리를 내리거나 몸을 만 완전 이완 자세다.
        required_pose=(
            "curled up or lying comfortably with the head resting down on or "
            "near the paws, eyes fully closed, sleeping peacefully, body fully "
            "settled — clearly different from an awake lying pose with the "
            "head upright"
        ),
        required_visibility=("full_body",),
        body_motion_complexity="micro",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": True, "motion_class": "sleep"},
        supported_action_ids=(),
    ),
    "LOOK_UP": KeyframeRole(
        role="LOOK_UP",
        required_pose=(
            "a neutral standing or sitting pose with the head naturally tilted "
            "slightly upward, as if noticing something above"
        ),
        required_visibility=("face", "full_body", "ears"),
        body_motion_complexity="small",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": False, "motion_class": "gesture"},
        supported_action_ids=(),
    ),
    "HAPPY": KeyframeRole(
        role="HAPPY",
        required_pose=(
            "an alert upright pose, ears perked, gently open relaxed mouth, "
            "bright attentive expression, tail naturally lifted where anatomy allows"
        ),
        required_visibility=("face", "full_body", "ears"),
        body_motion_complexity="small",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": False, "motion_class": "gesture"},
        supported_action_ids=(),
    ),
}

#: 결정론적 순서 (벤치마크 순서). 앞의 4개가 기계적으로 쓰이는 포즈 역할이다
#: (Phase 4) — LOOK_UP/HAPPY 는 매핑 없는 미래 벤치마크로 남는다.
KEYFRAME_ROLE_ORDER: tuple[str, ...] = (
    "NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP", "LOOK_UP", "HAPPY",
)


def get_role(role: str) -> Optional[KeyframeRole]:
    return KEYFRAME_ROLES.get((role or "").strip().upper())


def role_for_action(action_id: str) -> Optional[str]:
    """기존 액션 id → 그 액션이 시작 포즈로 쓰는 키프레임 역할."""
    aid = (action_id or "").strip()
    for role in KEYFRAME_ROLE_ORDER:
        if aid in KEYFRAME_ROLES[role].supported_action_ids:
            return role
    return None


def role_spec_snapshot(spec: KeyframeRole) -> dict[str, Any]:
    """빌드 시점 스냅샷 — DB 에 박제되는 형태."""
    return {
        "spec_version": KEYFRAME_SPEC_VERSION,
        "role": spec.role,
        "required_pose": spec.required_pose,
        "required_visibility": list(spec.required_visibility),
        "body_motion_complexity": spec.body_motion_complexity,
        "preferred_canonical_source": spec.preferred_canonical_source,
        "video_compat": dict(spec.video_compat),
        "supported_action_ids": list(spec.supported_action_ids),
    }


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 프롬프트 (버전드)
# ══════════════════════════════════════════════════════════════════════════

_PROMPT_BASE = (
    "The first supplied image is the canonical reference of a specific pet; any "
    "additional images are real photos of the same pet. Create a photorealistic "
    "still image of the EXACT SAME pet — this is a pose change, not a new "
    "interpretation of the animal.\n"
    "Preserve exactly: face identity and facial proportions, coat colors and all "
    "distinctive markings, body proportions, ear shape, paw appearance and tail "
    "appearance, exactly as in the supplied images.\n"
    "Change only: the pose, head direction and limb placement required by the "
    "requested pose, and expression only where the pose requires it.\n"
    "Plain solid neutral light-gray background. Even neutral lighting. No beds, "
    "furniture, toys, scenery or any environmental objects. No accessories unless "
    "clearly part of the pet's identity in the supplied images. No additional "
    "animals. No human. No text. No stylization."
)


def build_keyframe_prompt(spec: KeyframeRole, visual_identity: dict[str, Any]) -> str:
    """정본 이미지 + 확인된 특성 제약 + 역할 포즈 절 → 키프레임 프롬프트."""
    from .canonical_prompt import confident_trait_lines

    parts = [_PROMPT_BASE, f"Requested pose: {spec.required_pose}."]
    if spec.required_visibility:
        parts.append(
            "The following must be clearly visible: "
            + ", ".join(v.replace("_", " ") for v in spec.required_visibility)
            + "."
        )
    parts.extend(confident_trait_lines(visual_identity or {}))
    return "\n".join(parts)


# ── 컴팩트 키프레임 프롬프트 (Runway promptText ≤ 1000자 — 라이브 검증 계약) ──
KEYFRAME_COMPACT_PROMPT_VERSION = "keyframe-prompt-compact-v1"

_COMPACT_PROMPT_BASE = (
    "The first supplied image is the canonical reference of a specific pet; any "
    "additional images are real photos of the same pet. Create a photorealistic "
    "still of the EXACT SAME pet — a pose change only, not a new interpretation. "
    "Preserve face, coat colors, markings, ear shape, body proportions, paws and "
    "tail exactly as supplied; invent nothing. Natural anatomy. Plain solid "
    "neutral light-gray background, even lighting. No objects, no scenery, no "
    "other animals, no human, no text, no stylization."
)


def build_compact_keyframe_prompt(
    spec: KeyframeRole, visual_identity: dict[str, Any], *, max_chars: int = 1000
) -> str:
    """짧은 전용 변형 — 신원은 이미지가 정본이고, 포즈 절만 필수다."""
    from .canonical_prompt import _compact_trait_lines

    pose = f"Requested pose: {spec.required_pose}."
    vis = (
        "Clearly visible: " + ", ".join(v.replace("_", " ") for v in spec.required_visibility) + "."
        if spec.required_visibility
        else ""
    )
    lines = [x for x in (vis,) if x] + _compact_trait_lines(visual_identity or {})
    while True:
        prompt = " ".join([_COMPACT_PROMPT_BASE, pose] + lines)
        if len(prompt) <= max_chars or not lines:
            return prompt
        lines.pop()
