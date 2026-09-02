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

KEYFRAME_SPEC_VERSION = "keyframe-spec-v1"
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


#: 현재 런타임의 전 행동 — 전부 중립 대기 포즈에서 시작한다.
_NEUTRAL_ACTIONS: tuple[str, ...] = (
    tuple(ACTION_ORDER)          # IDLE, TOUCH(=PET_HEAD_START), VOICE, NFC
    + tuple(PET_ACTIONS)         # COME_CLOSER (시작 포즈)
    + (BREATHING_HOME_STATE,)    # 웹 홈 상태
    + tuple(IDLE_EVENTS)         # BLINKING, EAR_TWITCHING, HEAD_TILTING, TAIL_WAGGING
    + tuple(IDLE_TEMPLATE_ORDER) # IDLE_BREATH … IDLE_LOOK_AROUND
)

KEYFRAME_ROLES: dict[str, KeyframeRole] = {
    "NEUTRAL_IDLE": KeyframeRole(
        role="NEUTRAL_IDLE",
        required_pose=(
            "a calm neutral sitting or standing pose, body relaxed, head level and "
            "facing slightly toward the camera, eyes open, mouth relaxed"
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="micro",
        preferred_canonical_source="raw",
        video_compat={"loopable_base": True, "motion_class": "idle"},
        supported_action_ids=_NEUTRAL_ACTIONS,
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
        supported_action_ids=(),  # 미래 액션 — 존재하지 않는 id 를 지어내지 않는다
    ),
    "SLEEP": KeyframeRole(
        role="SLEEP",
        required_pose=(
            "curled up or lying comfortably with eyes fully closed, sleeping "
            "peacefully, body settled"
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

#: 결정론적 순서 (벤치마크 순서).
KEYFRAME_ROLE_ORDER: tuple[str, ...] = ("NEUTRAL_IDLE", "LIE", "SLEEP", "LOOK_UP", "HAPPY")


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
