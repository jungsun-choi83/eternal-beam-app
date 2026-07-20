"""
Luma Dream Machine I2V — 강아지만 영상화 (사람·목줄·손 제외).

누끼는 rembg 로 따로 처리. 여기는 **영상 생성 프롬프트**만 담당.
"""

from __future__ import annotations

# ── 공통: 반드시 지킬 규칙 (모든 Luma 요청에 붙임) ─────────────────────────────
LUMA_SUBJECT_RULE = (
    "Animate ONLY the single dog from the reference keyframe image. "
    "The dog is alone in the frame. "
    "No humans, no people, no hands, no arms, no fingers, no legs of a person. "
    "No leash, no lead rope, no collar strap, no harness, no chain, no owner walking the dog. "
    "No second animal. Stable camera, photorealistic fur detail, natural lighting."
)

LUMA_AVOID_CLAUSE = (
    "Do not show or invent: person, human, man, woman, child, hand, hands, arm, arms, "
    "finger, fingers, leash, lead, rope, strap, harness, collar band, chain, "
    "walking, holding, owner, pedestrian, walker, blurry, morphing, extra limbs, "
    "text, watermark, logo."
)

# ── 행동별 (4종) — 크레딧·배치 API용 ─────────────────────────────────────────
LUMA_ACTION_PROMPTS: dict[str, str] = {
    "IDLE": (
        "The dog sits calmly in the exact same pose as the reference image, "
        "with only very subtle idle motion: gentle chest breathing, occasional "
        "natural blinking, tiny ear twitch, minimal head sway. Static locked-off "
        "camera, no pan, no zoom, no dolly. The dog stays in place and returns "
        "close to its starting pose and position by the end of the clip, "
        "looking toward camera. Nobody enters the scene. No leash visible."
    ),
    "TOUCH": (
        "The dog reacts as if gently petted on the head — happy expression, "
        "soft nuzzle, slight tail wag. "
        "Do NOT show a human hand or arm; only the dog's reaction is visible."
    ),
    "VOICE": (
        "The dog perks ears and turns head as if hearing a familiar voice off-camera. "
        "Attentive eyes, no speaker or person in frame."
    ),
    "NFC": (
        "The dog looks around a familiar place, gentle sniffing motion, curious calm. "
        "No person accompanying, no leash tension."
    ),
}

# ── 단순 2종 (generate-pet-video 레거시) ────────────────────────────────────
LUMA_PROMPT_IDLE = (
    f"{LUMA_ACTION_PROMPTS['IDLE']} {LUMA_SUBJECT_RULE} {LUMA_AVOID_CLAUSE}"
)

LUMA_PROMPT_ACTION = (
    "The dog stands up and takes a few playful steps toward camera, natural gait, "
    f"tail wag. {LUMA_SUBJECT_RULE} {LUMA_AVOID_CLAUSE}"
)

# ── 단일 사진 → Luma (build_luma_prompt) ─────────────────────────────────────
def build_luma_pet_video_prompt(
    dog_breed: str = "dog",
    *,
    on_white_bg: bool = False,
    motion: str = "sitting calmly, natural breathing, subtle tail movement, looking at camera",
) -> str:
    bg = "solid white background, high contrast" if on_white_bg else "solid black background, studio"
    return (
        f"A photorealistic {dog_breed} from the uploaded photo, {motion}, "
        f"{bg}, cinematic 3D depth, extreme fur detail. "
        f"{LUMA_SUBJECT_RULE} {LUMA_AVOID_CLAUSE}"
    )


def build_scenario_luma_prompt(
    image_url: str,
    place_prompt: str,
    action_key: str,
    *,
    motion_ko_suffix: str = "",
) -> str:
    """장소 × 행동 40건용 최종 한 줄 프롬프트."""
    action_key = action_key.upper()
    motion = LUMA_ACTION_PROMPTS.get(
        action_key,
        "natural subtle motion appropriate to the scene",
    )
    extra = f" ({motion_ko_suffix})" if motion_ko_suffix else ""
    return (
        f"Image-to-video from keyframe {image_url}. "
        f"Environment: {place_prompt}. "
        f"Motion: {motion}{extra}. "
        f"{LUMA_SUBJECT_RULE} "
        "High-end minimalist cinematic lighting, hyper-realistic depth, no text. "
        f"{LUMA_AVOID_CLAUSE}"
    )
