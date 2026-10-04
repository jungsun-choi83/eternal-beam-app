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
MOTION_VIDEO_PROMPT_VERSION = "motion-video-prompt-v2"

_IDENTITY_LOCK = (
    "The exact same pet as in the supplied reference image(s). Do not alter the "
    "face, fur colors, markings, ears, body proportions, paws or tail at any point "
    "in the video. No new objects, no other animals, no scenery. "
    "The background must remain a single flat matte neutral tone with no texture, "
    "no gradient, no vignette, no floor plane and no horizon line. "
    "There must be no contact shadow under the pet, no cast shadow, no reflection, "
    "no glossy surface and no ambient grounding darkening of any kind. "
    "The pet silhouette must remain clean and clearly separated from the background, "
    "including around the ears, fur edges, paws, legs and tail. "
    "All paws must remain fully visible and cleanly separated. "
    "Camera and framing remain completely fixed. No text, no stylization."
)


def _micro(spec_contract: dict[str, Any], description: str) -> str:
    parts = [
        _IDENTITY_LOCK,
        f"The pet stays in the same pose and position. Only this subtle natural "
        f"motion occurs: {description}.",
        "No other intentional or expressive behavior occurs. The pet remains silent: "
        "no barking, vocalizing, panting, licking, chewing, or unnecessary mouth, jaw "
        "or tongue movement. If audio is generated, it must be absolute silence.",
        "No body translation, no walking, no large movement of any kind.",
    ]
    if (spec_contract.get("video_compat") or {}).get("returns_to_start_pose"):
        parts.append(
            "By the final frame the pet has returned to exactly the starting pose, "
            "so the clip can hand back to the idle loop without a visible jump."
        )
    return "\n".join(parts)


def _transition(spec_contract: dict[str, Any], description: str) -> str:
    return "\n".join(
        [
            _IDENTITY_LOCK,
            "The first supplied frame is the exact starting pose and the second "
            "supplied frame is the exact ending pose.",
            f"The pet naturally performs this transition between them: {description}.",
            "Preserve identity and anatomy throughout the movement. The motion is "
            "smooth and physically plausible, with no intermediate teleporting.",
            "End exactly in the supplied target pose and hold it briefly.",
        ]
    )


def _locomotion(spec_contract: dict[str, Any], description: str) -> str:
    return "\n".join(
        [
            _IDENTITY_LOCK,
            f"The pet performs this whole-body motion: {description}.",
            "Leg movement is anatomically correct with natural gait timing; paws "
            "contact the ground plausibly, but the ground itself stays the flat "
            "neutral background — no contact shadow, cast shadow or ground plane "
            "appears under the paws. The body may move within the frame, but "
            "the camera itself does not move.",
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
    builder = _BUILDERS.get(str(spec_contract.get("motion_class") or "").upper(), _micro)
    return builder(spec_contract, description)
