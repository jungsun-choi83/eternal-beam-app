"""
모션 클래스별 비디오 프롬프트 빌더 (Phase 6) — 버전드. 거대한 범용 프롬프트 금지.

원칙:
* 신원은 레퍼런스 이미지가 담당한다 — 이미 이미지에 있는 외형을 말로 재서술하지
  않는다. 프롬프트는 **움직임과 제약**만 말한다.
* 테마/배경/환경 오브젝트 어휘 금지 (배경은 잠긴 중립 배경).
* 카메라 고정. 오디오 없음(출력 사양에서 별도 강제).
"""

from __future__ import annotations

from typing import Any

# v2 (2026-09-17): 그림자 금지 절 추가. I2V 는 정지 그림자를 **움직이는**
# 그림자로 살려내고, packed-alpha 매트가 그것을 전경으로 삼킨다.
# v3: unsupported conditioning is expressed in the prompt of the assigned model.
MOTION_VIDEO_PROMPT_VERSION = "motion-video-prompt-v3"
GAIT = {
    "dog": {
        "prompt": "runs naturally straight toward the camera in a short, energetic burst",
        "qa_expect": "a running gait with a brief airborne phase, then smooth braking",
        "qa_reject": "a slow walk or trot with a paw always on the ground",
    },
    "cat": {
        "prompt": "walks confidently straight toward the camera with a smooth, steady, natural cat walk",
        "qa_expect": "a continuous, steady walking gait with alternating paws and a calm stop",
        "qa_reject": "a run or leap, sliding without leg movement, floating, or repeated stop-start",
    },
}

def _gait(species):
    return GAIT.get((species or "").lower(), GAIT["dog"])

_IDENTITY_LOCK = (
    "The same pet as in the first frame, unchanged for the whole clip: same face, "
    "fur colors, markings, ears, body proportions, paws and tail. "
    "The background is one flat, matte, uniform neutral tone, identical to the first "
    "frame, with even shadowless studio lighting and constant exposure. "
    "The camera and framing stay completely fixed. "
    "The pet silhouette stays clean and clearly separated from the background, "
    "including ears, fur edges, paws, legs and tail, and all paws stay fully visible. "
    "The clip is silent. Keep it free of shadows, reflections and camera movement."
)


def _micro(spec_contract: dict[str, Any], description: str) -> str:
    parts = [
        "The pet stays in place with its paws planted, calm and relaxed, and looks "
        f"naturally alive. {description.rstrip('.')}.",
        "All movement is very small, soft and unhurried, like a real animal quietly "
        "at rest. The pet does not walk, step, slide, noticeably shift its weight or "
        "change its position in the frame. Its overall body proportions and silhouette "
        "remain stable except for the subtle movement explicitly described above.",
    ]
    if (spec_contract.get("video_compat") or {}).get("returns_to_start_pose"):
        parts.append(
            "Near the end of the clip, the motion gradually settles back toward the starting idle state. "
            "The final frame closely matches the starting pose, position, scale and appearance so the clip "
            "can loop without a visible jump."
        )
        parts.append(_IDENTITY_LOCK)
    return "\n".join(parts)


def _transition(spec_contract: dict[str, Any], description: str) -> str:
    endpoint_fallback = "supports_end_frame" in (spec_contract.get("conditioning_fallbacks") or [])
    return "\n".join(
        [
            _IDENTITY_LOCK,
            (
                "The supplied image is the exact starting pose. Complete the described ending pose."
                if endpoint_fallback else
                "The first supplied frame is the exact starting pose and the second "
                "supplied frame is the exact ending pose."
            ),
            f"The pet naturally performs this transition between them: {description}.",
            "Preserve identity and anatomy throughout the movement. The motion is "
            "smooth and physically plausible, with no intermediate teleporting.",
            (
                "End in the described target pose and hold it briefly."
                if endpoint_fallback else
                "End exactly in the supplied target pose and hold it briefly."
            ),
        ]
    )


def _locomotion(spec_contract: dict[str, Any], description: str,species=None) -> str:
    description = description.format(gait_text=_gait(species)["prompt"])
    return "\n".join(
        [
            f"The pet performs this whole-body motion: {description.rstrip('.')}.",
            "Locomotion must be anatomically correct and appropriate to this pet's "
            "species and morphology, with natural limb timing and plausible contact "
            "with the flat neutral ground. The pet moves within the frame, staying fully  "
            "inside it, while the camera itself stays completely still.",
            _IDENTITY_LOCK
        ]
    )


def _interaction(spec_contract: dict[str, Any], description: str) -> str:
    compat = spec_contract.get("video_compat") or {}
    parts = [
        _IDENTITY_LOCK,
        f"The pet shows only this reaction: {description}.",
    ]
    if compat.get("allow_generated_hand"):
        parts.append(
            "A single gentle human hand MAY enter softly from the edge of the frame "
            "to pet the head; nothing else may be added. If a hand appears it looks "
            "natural and calm."
        )
    else:
        parts.append("No human or hand appears; the pet reacts as if gently petted.")
    if compat.get("returns_to_start_pose"):
        parts.append("The pet settles back to the starting pose by the final frame.")
    return "\n".join(parts)


_BUILDERS = {
    "MICRO": _micro,
    "TRANSITION": _transition,
    "LOCOMOTION": _locomotion,
    "INTERACTION": _interaction,
}


def build_motion_video_prompt(spec_contract: dict[str, Any], description: str) -> str:
    """Phase 5.1 계약 + 모션 서술 → 클래스별 프롬프트 (버전 MOTION_VIDEO_PROMPT_VERSION)."""
    fallbacks = spec_contract.get("conditioning_fallbacks") or []
    if "supports_end_frame" in fallbacks:
        # Descriptions may refer to a target image that this model cannot receive.
        description = description.replace("shown in the second frame", "described below")
    builder = _BUILDERS.get(str(spec_contract.get("motion_class") or "").upper(), _micro)
    parts = [builder(spec_contract, description)]
    if "supports_end_frame" in fallbacks:
        if spec_contract.get("loop_same_start_end"):
            parts.append(
                "Use the starting image as the final-pose guide. Keep the described idle "
                "motion gentle and in place with a fixed camera. By the final frame, return "
                "to the exact starting pose, position, home distance, framing and scale, "
                "then settle briefly so the loop joins smoothly."
            )
        elif spec_contract.get("motion_id") == "RETURN_HOME":
            parts.append(
                "Begin at the close position in the supplied starting image. Naturally "
                "walk away to the original home distance, becoming gradually smaller through "
                "physical movement. Restore the original home framing and centered full-body "
                "scale with comfortable space around the pet. Turn to face the fixed camera "
                "and finish in a calm, balanced standing pose with all four paws grounded. "
                "Hold this home pose at the end. Keep the camera fixed; never zoom, slide, "
                "teleport, or shrink the body in place."
            )
        else:
            from .action_keyframe_spec import KEYFRAME_ROLES

            role = (spec_contract.get("target_keyframe") or {}).get("role")
            target = KEYFRAME_ROLES.get(role)
            if target:
                parts.append(f"Finish in this target pose: {target.required_pose}. Hold it briefly.")
    if "supports_motion_reference" in fallbacks:
        parts.append(
            "Perform the complete motion described above using natural, continuous limb "
            "timing appropriate to this pet. Clearly show the intended direction and pace, "
            "then settle into the described final pose. Preserve identity, anatomy and the "
            "fixed camera throughout."
        )
    return "\n".join(parts)
