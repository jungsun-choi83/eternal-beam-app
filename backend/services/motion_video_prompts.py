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

from ..scenarios.pet_scenarios import IDLE_EVENTS
from . import species_motion_variants

# v2 (2026-09-17): 그림자 금지 절 추가. I2V 는 정지 그림자를 **움직이는**
# 그림자로 살려내고, packed-alpha 매트가 그것을 전경으로 삼킨다.
# v3: unsupported conditioning is expressed in the prompt of the assigned model.
MOTION_VIDEO_PROMPT_VERSION = "motion-video-prompt-v3"
# The four commercial idle events use a motion-scoped recipe/version.  Keeping
# this separate avoids invalidating BREATHING and unrelated motion caches when
# only these body-part prompts change.
DURABLE_IDLE_EVENT_PROMPT_VERSION = "motion-video-prompt-v3-idle-event-v1"
DURABLE_IDLE_EVENT_IDS = frozenset(IDLE_EVENTS)
# v2: PET_HEAD now conditions Seedance with the same STAND_READY frame at both
# endpoints. Keep the invalidation scoped to this paid motion.
PET_HEAD_PROMPT_VERSION = "motion-video-prompt-v3-pet-head-v2"
# Generation invalidation for the new action is deliberately motion-scoped.
PAW_HELLO_PROMPT_VERSION = "motion-video-prompt-v3-paw-hello-v1"
# PLAY_WITH_ME: one version for both species variants — the variant itself is
# pinned in the contract (species_variant), and a pet's species does not change.
# v2: compacted below the fal Kling 2,500-character prompt limit (v1 was 3,009/2,910
# chars for DOG/CAT and was rejected live with HTTP 422 at result fetch).
# v3 (2026-10-29): two start/end-conditioned segments through the species intermediate
# keyframe (A: STAND_READY → lead-in → PLAY pose, B: PLAY pose → return → STAND_READY).
# Each segment prompt is built by build_play_with_me_segment_prompts and must stay
# under the same 2,500-character limit on its own.
# v6 (2026-10-30, DOG timing): segment A adds one short in-place bounce before the bow
# (lands before lowering); segment B holds briefly then rises naturally. The CAT segment
# text is byte-identical to v5 — the version is per motion, so a cat version stamped v5
# is merely not deduplicated by the builder; READY cat assets are never regenerated.
# v7 (2026-10-30, DOG hindquarters): the dog's rear stays near normal standing height
# (not lifted/arched) and the bow depth follows the individual build. CAT text unchanged.
PLAY_WITH_ME_PROMPT_VERSION = "motion-video-prompt-v3-play-with-me-v7"
# COME_PLAY (2026-10-30, DOG only): two start/end-conditioned segments through the
# near play-bow keyframe (A: STAND_READY at home → excitement → trot toward the viewer →
# deceleration → PLAY_BOW_NEAR, B: PLAY_BOW_NEAR → brief hold → rise → trot back home →
# STAND_READY). Motion-scoped: bumping this never touches PLAY_WITH_ME or COME_CLOSER.
# v2 (2026-10-30): explicit per-second timing inside each segment (the first live run spent 1.2 s
# idle, held the bow ~2.6 s across the seam and crammed the return into the last 0.5 s), an
# energetic bounding run, a smooth arc turn, and segment B at 8 s. Still one prompt per segment
# with start/end conditioning: Kling v3 `multi_prompt` is a multi-SHOT contract whose
# compatibility with `end_image_url` is undocumented, so it is not used (no silent loss of the
# end frame).
COME_PLAY_PROMPT_VERSION = "motion-video-prompt-v3-come-play-v2"
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

_DURABLE_IDLE_EVENT_RETURN = (
    "After completing the requested action, the moving body part returns continuously "
    "and naturally toward its starting resting orientation. Keep time moving forward: "
    "do not reverse the action or play frames backward, and do not snap, dissolve, "
    "teleport, morph or make a sudden corrective pose. Settle briefly into the same calm "
    "standing home pose used by BREATHING. The final frame closely matches the starting "
    "pose, position, scale and appearance so playback can return to BREATHING without a "
    "visible jump."
)

_DURABLE_IDLE_EVENT_IDENTITY_LOCK = (
    "Keep this the same pet for the whole clip: preserve the face, eyes, fur colors, "
    "markings, ear and tail anatomy, body proportions, paws and limb count. Preserve "
    "source occlusions; do not invent, remove, duplicate, stretch or reshape body parts. "
    "The background remains the same flat, matte, uniform neutral tone, and exposure, "
    "color and lighting remain constant. Preserve stable natural self-shading on the pet "
    "without adding or animating cast shadows, reflections or environmental effects. "
    "Keep the camera, crop, framing, perspective and focus fixed, and keep the full pet "
    "inside the frame. The clip is silent."
)


def prompt_version_for_motion(motion_id: str) -> str:
    """Generation-affecting prompt version without globally invalidating other motions."""
    normalized = (motion_id or "").strip().upper()
    if normalized in DURABLE_IDLE_EVENT_IDS:
        return DURABLE_IDLE_EVENT_PROMPT_VERSION
    if normalized == "PET_HEAD":
        return PET_HEAD_PROMPT_VERSION
    if normalized == "PAW_HELLO":
        return PAW_HELLO_PROMPT_VERSION
    if normalized == species_motion_variants.PLAY_WITH_ME:
        return PLAY_WITH_ME_PROMPT_VERSION
    if normalized == species_motion_variants.COME_PLAY:
        return COME_PLAY_PROMPT_VERSION
    return MOTION_VIDEO_PROMPT_VERSION


def _durable_idle_event_micro(spec_contract: dict[str, Any], description: str) -> str:
    """Observable body-part motion for the four durable commercial idle events.

    These motions all start from STAND_READY today.  Fail closed if that registry
    contract changes: lying and sleeping MICRO motions keep their existing prompt
    path rather than inheriting standing/paw constraints by accident.
    """
    role = str((spec_contract.get("start_keyframe") or {}).get("role") or "").upper()
    if role and role != "STAND_READY":
        raise ValueError(
            f"durable idle event prompt requires STAND_READY, got {role!r}"
        )
    return "\n".join(
        [
            f"{description.rstrip('.')}.",
            (
                "Make the requested action above the dominant and clearly readable "
                "intentional movement. Keep the pet in its supplied calm standing pose "
                "and in the same place, with all support paws naturally grounded and its "
                "overall center of mass stable. Explicitly allow the named body part and "
                "anatomically connected nearby tissue to move; its local silhouette may "
                "change naturally during that motion. Other body regions remain calm, "
                "with quiet breathing and only the small secondary motion physically "
                "needed for the requested action. Do not add another intentional gesture "
                "or let breathing compete with the requested action. Do not walk, step, "
                "slide, lift a paw, make a large weight shift, change posture, drift in "
                "the frame or move the whole body as a substitute for the requested action."
            ),
            _DURABLE_IDLE_EVENT_RETURN,
            _DURABLE_IDLE_EVENT_IDENTITY_LOCK,
        ]
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
        parts.append(
            "The pet independently performs the described emotional response. "
            "Only the original pet is visible throughout the scene. "
            "No additional subjects or objects appear."
        )

    if compat.get("returns_to_start_pose"):
        parts.append(
            "The pet settles naturally back to its original starting pose "
            "by the final frame."
        )
    return "\n".join(parts)


def _paw_hello_interaction(spec_contract: dict[str, Any], description: str) -> str:
    """One-paw offer with explicit support, anatomy and same-pose endpoint semantics."""
    if str(spec_contract.get("motion_class") or "").upper() != "INTERACTION":
        raise ValueError("PAW_HELLO must use INTERACTION")
    return "\n".join([
        _DURABLE_IDLE_EVENT_IDENTITY_LOCK,
        (
            "The first and last supplied frames are the same approved STAND_READY pose. "
            "Perform the complete paw greeting between those endpoints; do not remain still "
            "and do not replace the action with a head gesture or full-body step."
        ),
        f"Motion: {description.rstrip('.') }.",
        (
            "Transfer weight subtly over the three supporting paws. Lift exactly one front "
            "paw clearly off the ground, extend it a short comfortable distance toward the "
            "viewer, hold it briefly, then retract and lower it continuously to its original "
            "grounded placement. The supporting paws stay grounded without sliding, while "
            "the torso may make the small connected adjustment needed for realistic balance."
        ),
        (
            "Maintain quiet natural breathing plus subtle physically connected ear, head and "
            "tail movement. Preserve exactly four limbs and plausible shoulder, elbow, wrist "
            "and paw anatomy; never duplicate, merge, detach, hide or invent a limb."
        ),
        (
            "Only the original pet appears. No human, human hand, arm, person, leash, toy, "
            "platform or added object enters the frame."
        ),
        (
            "Finish by matching the supplied final STAND_READY frame, with all four paws "
            "grounded and the same position, scale and framing, so BREATHING resumes smoothly "
            "without a snap, teleport, freeze, reverse playback or visible jump."
        ),
    ])


def _play_with_me_variant(spec_contract: dict[str, Any]):
    """Fail closed: a dog must never receive the cat clauses (or a neutral prompt)."""
    if str(spec_contract.get("motion_class") or "").upper() != "INTERACTION":
        raise ValueError("PLAY_WITH_ME must use INTERACTION")
    variant = species_motion_variants.variant_from_contract(spec_contract)
    if variant is None:
        raise ValueError("PLAY_WITH_ME prompt requires a resolved species_variant in the contract")
    return variant


_PLAY_WITH_ME_ANATOMY = (
    "Keep gentle breathing and small playful secondary movements (ear, head or tail) "
    "throughout. Exactly four limbs with plausible shoulder, elbow, hip, knee and paw "
    "anatomy; never duplicate, merge, detach or invent a limb. Paws reposition only as much "
    "as realistic grounding and balance need; they never skate or float."
)
_PLAY_WITH_ME_COMPOSITION = (
    "Only the original pet appears: no human, hand, arm, person, leash, toy, platform or "
    "added object. Camera and home spot stay fixed: the pet never travels, runs, walks or "
    "steps toward or away from the camera, though its body height and silhouette change "
    "naturally with the pose."
)


# Segment B describes the rise back to STAND_READY, so the variant's into-pose shape
# clauses (e.g. "the front body drops") must not appear there; this guard replaces them.
_PLAY_WITH_ME_RISE_GUARD = (
    "During the rise the rear never sinks into a sit or lie-down, and the pet stays balanced "
    "on four grounded paws until fully upright."
)


def build_play_with_me_segment_prompts(spec_contract: dict[str, Any]) -> dict[str, str]:
    """Two start/end-conditioned segment prompts for the species variant.

    A: STAND_READY (first frame) → brief playful lead-in → the approved intermediate
       PLAY pose (last frame).
    B: the PLAY pose (first frame) → brief hold → natural forward-time return →
       STAND_READY (last frame).
    Both endpoints of each segment are the actual approved stills the service sends as
    start/end conditioning, so the prompts talk about "the supplied first/last frame".
    """
    variant = _play_with_me_variant(spec_contract)
    pose = variant.pose_label
    segment_a = "\n".join([
        _PLAY_WITH_ME_IDENTITY_LOCK,
        (
            f"The first supplied frame is the pet's approved STAND_READY pose and the last "
            f"supplied frame is the same pet's approved {pose} pose. Progress from the first "
            f"to the last as one continuous movement through time, in place; do not stay "
            f"still and do not replace the action with a head-only gesture, a sit, a "
            f"lie-down or a step."
        ),
        f"Start calm and upright, then show {variant.lead_in}.",
        f"Then {variant.into_pose}.",
        *variant.prompt_clauses,
        _PLAY_WITH_ME_ANATOMY,
        _PLAY_WITH_ME_COMPOSITION,
        (
            f"Arrive at the supplied final {pose} frame exactly as the clip closes, alive "
            "with gentle breathing; do not reach it early and hold it motionless. The next "
            "clip continues from this exact frame: no pause, snap, freeze or visible jump."
        ),
    ])
    segment_b = "\n".join([
        _PLAY_WITH_ME_IDENTITY_LOCK,
        (
            f"The first supplied frame is the pet's approved {pose} pose and the last "
            f"supplied frame is the same pet's approved STAND_READY pose. Progress from the "
            f"first to the last as one continuous movement through time, in place — a "
            f"natural rise played forward, never reversed or rewound playback — and do not "
            f"add a sit, a lie-down or a step."
        ),
        (
            f"Begin exactly in the supplied first frame, already alive: {variant.hold}. Keep "
            "this beat brief — the previous clip just arrived here; do not repeat the "
            "settle, freeze, or extend the hold."
        ),
        f"Then {variant.return_to_stand}.",
        _PLAY_WITH_ME_RISE_GUARD,
        _PLAY_WITH_ME_ANATOMY,
        _PLAY_WITH_ME_COMPOSITION,
        (
            "Finish matching the supplied final STAND_READY frame exactly, upright on all "
            "four grounded paws at the same position, scale and framing, breathing calmly, so "
            "BREATHING resumes smoothly without a snap, freeze or visible jump."
        ),
    ])
    return {
        species_motion_variants.SEGMENT_A: segment_a,
        species_motion_variants.SEGMENT_B: segment_b,
    }


def _come_play_variant(spec_contract: dict[str, Any]):
    """Fail closed: COME_PLAY is DOG-only and must carry its resolved variant."""
    if str(spec_contract.get("motion_class") or "").upper() != "INTERACTION":
        raise ValueError("COME_PLAY must use INTERACTION")
    if str(spec_contract.get("motion_id") or "").upper() != species_motion_variants.COME_PLAY:
        raise ValueError("COME_PLAY prompt builder received another motion")
    variant = species_motion_variants.variant_from_contract(spec_contract)
    if variant is None:
        raise ValueError("COME_PLAY prompt requires a resolved species_variant in the contract")
    return variant


# COME_PLAY: the dog travels, so the composition clause is the opposite of PLAY_WITH_ME's
# "home spot fixed" — the camera is fixed and the dog's size/position change is the action.
_COME_PLAY_ANATOMY = (
    "Exactly four limbs with plausible joints; never duplicate, merge, detach or invent a limb. "
    "Every stride is a real grounded step; the body never slides, floats or changes size without "
    "leg movement."
)
_COME_PLAY_COMPOSITION = (
    "Only the original dog appears: no human, hand, person, leash, toy or added object. Camera, "
    "crop and framing stay fixed with no zoom or reframing; the dog itself moves, so its size and "
    "position change; the whole dog stays in frame."
)
_COME_PLAY_RISE_GUARD = (
    "The rear never sinks into a sit or lie-down, the dog stays balanced on grounded paws, and "
    "the walk home is new forward-time motion, never the approach in reverse."
)


def build_come_play_segment_prompts(spec_contract: dict[str, Any]) -> dict[str, str]:
    """Two start/end-conditioned segment prompts for COME_PLAY (DOG).

    A (5 s): STAND_READY at home (first frame) → 0.5 s excitement → 3 s bounding run toward the
       viewer, growing larger → 1 s braking into the bow → the approved NEAR play bow (last frame).
    B (8 s): the near play bow (first frame) → 0.5 s beat → rise → smooth arc turn away → trot
       away to the home spot, growing smaller → arc turn to face the camera → STAND_READY at the
       original home position and scale (last frame).
    v2 writes the per-second timing into the prompt: the first live run let Kling allocate time
    on its own (1.2 s idle, 2.6 s bow hold, return crammed into the last 0.5 s).
    Both endpoints of each segment are the actual approved stills the service sends as
    start/end conditioning, so the prompts talk about "the supplied first/last frame".
    """
    variant = _come_play_variant(spec_contract)
    pose = variant.pose_label
    durations = variant.segment_durations_sec or (5, 6)
    segment_a = "\n".join([
        _PLAY_WITH_ME_IDENTITY_LOCK,
        (
            f"First supplied frame: the dog's approved STAND_READY pose at home. Last: its approved "
            f"{pose} a few steps closer to the camera, larger and lower in frame. One continuous "
            f"{int(durations[0])}-second take; the dog must actually travel toward the viewer, never "
            f"standing still, bowing in place or zooming. Timing:"
        ),
        f"{variant.lead_in}.",
        f"{variant.into_pose}.",
        *variant.prompt_clauses,
        _COME_PLAY_ANATOMY,
        _COME_PLAY_COMPOSITION,
        (
            f"Do not reach the final {pose} frame early and hold it motionless. The next clip "
            "continues from this exact frame: no pause, snap, freeze or visible jump."
        ),
    ])
    segment_b = "\n".join([
        _PLAY_WITH_ME_IDENTITY_LOCK,
        (
            f"First supplied frame: the dog's approved {pose} close to the camera. Last: its "
            f"approved STAND_READY pose back at home, smaller and higher in frame. One continuous "
            f"{int(durations[1])}-second take: rise, turn and trot home, played forward, never reversed "
            f"or rewound; no sit, lie-down or second approach; the dog never walks backward. Timing:"
        ),
        f"{variant.hold}.",
        f"{variant.return_to_stand}.",
        _COME_PLAY_RISE_GUARD,
        _COME_PLAY_ANATOMY,
        _COME_PLAY_COMPOSITION,
        (
            "Finish matching the supplied final STAND_READY frame exactly: upright on four grounded "
            "paws, facing the camera, at the same home position, size and framing as the action "
            "began, so BREATHING resumes without a snap, freeze or jump."
        ),
    ])
    return {
        species_motion_variants.SEGMENT_A: segment_a,
        species_motion_variants.SEGMENT_B: segment_b,
    }


def build_segment_prompts(spec_contract: dict[str, Any]) -> dict[str, str]:
    """Per-segment provider prompts for segmented motions (segment id → prompt).

    PLAY_WITH_ME and COME_PLAY are segmented today; any other motion fails closed so a
    segmented contract can never be generated with a single-clip prompt by accident.
    """
    motion_id = str(spec_contract.get("motion_id") or "").upper()
    if motion_id == species_motion_variants.PLAY_WITH_ME:
        return build_play_with_me_segment_prompts(spec_contract)
    if motion_id == species_motion_variants.COME_PLAY:
        return build_come_play_segment_prompts(spec_contract)
    raise ValueError(f"{motion_id} has no segment prompt builder")


def _play_with_me_interaction(spec_contract: dict[str, Any], description: str) -> str:
    """Display/record form of the PLAY_WITH_ME prompts: both segments, labelled.

    The generation service never sends this combined text to a provider — it sends
    build_play_with_me_segment_prompts()[segment] per segment. This string is what is
    persisted on the motion version row (one prompt column) and shown to operators.
    """
    segments = build_play_with_me_segment_prompts(spec_contract)
    return "\n\n".join(
        f"[SEGMENT {segment_id}]\n{text}" for segment_id, text in segments.items()
    )


def _come_play_interaction(spec_contract: dict[str, Any], description: str) -> str:
    """Display/record form of the COME_PLAY prompts (both segments, labelled) — the
    generation service sends build_come_play_segment_prompts()[segment] per segment."""
    segments = build_come_play_segment_prompts(spec_contract)
    return "\n\n".join(
        f"[SEGMENT {segment_id}]\n{text}" for segment_id, text in segments.items()
    )


# PLAY_WITH_ME-local compaction of _DURABLE_IDLE_EVENT_IDENTITY_LOCK (same requirements,
# ~35% shorter). The shared constant is left untouched so the idle events, PET_HEAD and
# PAW_HELLO prompts — and their versions — do not move.
_PLAY_WITH_ME_IDENTITY_LOCK = (
    "Same pet throughout: keep the face, eyes, coat colors, markings, ear and tail "
    "anatomy, proportions, paws and limb count; do not invent, remove, duplicate or "
    "reshape body parts. Ears, face and tail keep this pet's own natural shape and carriage "
    "(ears not forced forward, tail not forced up). Flat matte neutral background with "
    "constant exposure and lighting; stable self-shading, no new cast shadows or effects. "
    "Fixed camera, crop, framing, perspective and focus; the whole pet stays in frame. "
    "Silent clip."
)


_BUILDERS = {
    "MICRO": _micro,
    "TRANSITION": _transition,
    "LOCOMOTION": _locomotion,
    "INTERACTION": _interaction,
}


def build_motion_video_prompt(spec_contract: dict[str, Any], description: str) -> str:
    """Phase 5.1 contract + description -> versioned, class-specific prompt."""
    fallbacks = spec_contract.get("conditioning_fallbacks") or []
    if "supports_end_frame" in fallbacks:
        # Descriptions may refer to a target image that this model cannot receive.
        description = description.replace("shown in the second frame", "described below")
    motion_id = str(spec_contract.get("motion_id") or "").strip().upper()
    if motion_id in DURABLE_IDLE_EVENT_IDS:
        if str(spec_contract.get("motion_class") or "").upper() != "MICRO":
            raise ValueError(f"durable idle event {motion_id} must use MICRO")
        parts = [_durable_idle_event_micro(spec_contract, description)]
    elif motion_id == "PAW_HELLO":
        parts = [_paw_hello_interaction(spec_contract, description)]
    elif motion_id == species_motion_variants.PLAY_WITH_ME:
        parts = [_play_with_me_interaction(spec_contract, description)]
    elif motion_id == species_motion_variants.COME_PLAY:
        parts = [_come_play_interaction(spec_contract, description)]
    else:
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
