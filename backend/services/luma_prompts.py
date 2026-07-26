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

# ── 아이들(Idle) 5종 공통 제약 — luma_idle_templates.py 와 동기화 ─────────────
IDLE_COMMON_CONSTRAINT = (
    "Camera angle completely fixed, identical to the original photo's exact angle "
    "and framing. Subject's body position, head orientation, and pose must remain "
    "unchanged. No rotation, no turning, no shifting of body or head position. "
    "Same fur pattern, same lighting, same background. Only the specifically "
    "described micro-movement below is allowed — everything else must stay "
    "perfectly still."
)

IDLE_COMMON_CONSTRAINT_HEAD_ROTATION = (
    "Camera angle completely fixed, identical to the original photo's exact angle "
    "and framing. Subject's body position and pose must remain unchanged. No body "
    "rotation, no turning of the torso, no shifting of body position. Same fur "
    "pattern, same lighting, same background. Head rotation is allowed only as "
    "specifically described below — everything else must stay perfectly still."
)

# ── 행동별 (4종) — 크레딧·배치 API용 ─────────────────────────────────────────
LUMA_ACTION_PROMPTS: dict[str, str] = {
    "IDLE": (
        f"{IDLE_COMMON_CONSTRAINT} "
        "Only the chest and rib area rises and falls very subtly, as if breathing "
        "calmly. The movement is barely visible — no more than a few millimeters of "
        "expansion. Head, legs, tail, and camera angle do not move at all."
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


# ── 배경 전용 앰비언트 모션 (custom_photo_bg 파이프라인 전용) ──────────────────
# 사용자 사진에서 강아지를 지우고 인페인팅(LaMa)으로 채운 "강아지 없는 배경"
# 이미지에 Luma로 미세한 환경 모션만 입힌다. LUMA_ACTION_PROMPTS["IDLE"]과 같은
# 스타일(고정 카메라 + 미세 모션 + 반복 가능)로 작성했지만, 대상이 강아지가 아니라
# 배경(환경) 그 자체라서 LUMA_SUBJECT_RULE(강아지 1마리만 애니메이션)은 붙이지
# 않는다 — 대신 여기서도 사람/텍스트/로고 금지는 LUMA_AVOID_CLAUSE로 그대로 유지.
LUMA_BACKGROUND_AMBIENT_PROMPT = (
    "Animate ONLY subtle, looping environmental motion in this background scene: "
    "gentle breeze moving leaves, grass or branches, soft light flicker or shimmer, "
    "slowly drifting clouds, mist, or dust motes where appropriate to the scene. "
    "The composition, framing, colors and overall scene layout must stay essentially "
    "unchanged throughout the clip — this is ambient background motion, not a new "
    "scene. Camera is completely static: no pan, no zoom, no dolly, no rotation. "
    "No dog, no other animal, no person, no human, no hands ever appear in the frame."
)

# 재시도 시 "카메라 흔들림/컷 전환" 오검출을 줄이기 위해 덧붙이는 보강 문구.
LUMA_BACKGROUND_STATIC_CAMERA_BOOST = (
    "Keep the camera perfectly locked-off and completely static the entire time — "
    "absolutely no camera pan, tilt, zoom, dolly, shake, or scene cut."
)


def build_background_ambient_prompt(*, retry_boost: bool = False) -> str:
    """인페인팅된 배경 이미지 1장 → Luma 배경 앰비언트 모션 프롬프트.

    retry_boost=True면(예: 이전 시도에서 카메라가 흔들렸거나 강아지/사람이 다시
    생성된 경우) 정적 카메라 보강 문구를 추가해 재시도한다.
    """
    parts = [LUMA_BACKGROUND_AMBIENT_PROMPT]
    if retry_boost:
        parts.append(LUMA_BACKGROUND_STATIC_CAMERA_BOOST)
    parts.append(LUMA_AVOID_CLAUSE)
    return " ".join(parts)


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
