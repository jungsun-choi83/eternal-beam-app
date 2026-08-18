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

IDLE_AVOID_CLAUSE = (
    "Avoid rigid stillness, frozen torso, mechanical repetition, whole-body vertical "
    "bobbing, exaggerated breathing, large deliberate head turns, repeated side-to-side "
    "head movement, body translation, locomotion, walking, stepping, pose drift, scale "
    "drift, silhouette drift, camera movement, pan, tilt, zoom, dolly, reframing, anatomy "
    "changes, duplicated limbs, extra limbs, malformed paws, melting "
    "or morphing body parts, changing fur patterns, changing markings, changing identity, "
    "face distortion, abrupt motion, exaggerated reactions, sudden pose changes, or any "
    "movement that makes the pet look mechanically animated rather than quietly alive."
)

# ── 신체 완성(Body completion) 정책 ──────────────────────────────────────────
#
# 예전 FULL_BODY_COMPLETION_RULE 은 **무조건 완성**이었다("잘렸으면 전신으로 만들어라").
# 그게 위험했던 이유는 얼굴만 있는 컷아웃에서도 없는 몸통을 통째로 지어냈기 때문이다 —
# 그러면 정체성이 무너진다(다른 개가 된다).
#
# 지금 정책은 **조건부**다. 판단 기준은 "몸통 맥락이 얼마나 보이는가":
#   토르소가 충분히 보임  → 이동에 필요한 하체(다리·발·하복부·꼬리)만 자연스럽게 완성
#   얼굴/머리 위주 초상   → 몸을 지어내지 않는다. 보이는 영역만으로 수행
# 어느 쪽이든 중복 사지·여분 발·이상 관절은 절대 안 된다.
#
# 목적 문구(purpose)만 계열별로 갈아 끼운다 — COME_CLOSER 는 "전진 이동"이 목적이고
# 아이들/반응 계열은 "제자리 휴식 동작"이 목적이라, 같은 문장을 붙이면 아이들 클립에
# 걷기를 암시하게 된다.

_BODY_COMPLETION_CORE = (
    "BODY COMPLETION: if the source pet is partially cropped but still shows enough "
    "torso and body context to infer the missing anatomy reliably, naturally complete "
    "only the missing lower body parts needed for {purpose}, such as legs, paws, lower "
    "torso, or tail. "
    "Preserve all visible anatomy, proportions, fur pattern, breed traits, and identity. "
    "The completed anatomy must be physically plausible and consistent with the visible "
    "body. "
    "However, if the source is primarily a face-only or head-only portrait with little "
    "or no visible torso or body structure, do not invent a full unseen body. Keep the "
    "visible portrait identity and framing logic, and perform {fallback} using the "
    "visible head, neck, and upper-body region only. "
    "Never create duplicated limbs, extra paws, extra tails, malformed joints, or "
    "contradictory body geometry."
)


def body_completion_rule(*, purpose: str, fallback: str) -> str:
    """
    계열별 신체 완성 규칙.

    :param purpose:  완성이 왜 필요한가 (예: "forward locomotion")
    :param fallback: 몸을 못 만들 때 무엇을 대신 하는가 (예: "the approach")
    """
    return _BODY_COMPLETION_CORE.format(purpose=purpose, fallback=fallback)


#: COME_CLOSER — 실제로 걸어와야 하므로 하체가 가장 절실하다.
COME_CLOSER_BODY_COMPLETION = body_completion_rule(
    purpose="forward locomotion",
    fallback="the approach",
)

#: 아이들(BREATH) / 아이들 이벤트 — 이동이 아니라 제자리 미세 동작이 목적이다.
#: "locomotion" 이라고 쓰면 걷기를 암시하므로 문구를 바꾼다.
IDLE_BODY_COMPLETION = body_completion_rule(
    purpose="a complete, believable resting silhouette",
    fallback="the resting motion",
)

#: TOUCH / VOICE / NFC — 제자리 반응.
ACTION_BODY_COMPLETION = body_completion_rule(
    purpose="a complete, believable resting silhouette",
    fallback="the reaction",
)


# ── 아이들(Idle) 5종 공통 제약 — luma_idle_templates.py 와 동기화 ─────────────
# 원칙: **레퍼런스 컷아웃에 실제로 보이는 해부구조만** 애니메이션한다.
# 보이지 않는 부위를 드러내거나 새로 만들어 내지 않는다.
# 예전 문구는 "Legs, paws, torso, tail ... must retain ..." 처럼 전신을 전제해서,
# 머리만/상반신만 있는 컷아웃에서는 존재하지도 않는 부위를 요구하는 셈이었다.
IDLE_COMMON_CONSTRAINT = (
    "Camera angle and framing remain completely fixed. "
    
    "The pet stays anchored in the same overall resting position throughout the clip. "
    "Do not translate, rotate, walk, step, zoom, or substantially change the pet's "
    "overall scale or silhouette. Preserve identity, anatomy, visible crop, fur pattern, "
    "lighting, and all body parts visible in the reference. "

    # 보이는 것만 애니메이션 — 현재 정책상 신체 완성(FULL_BODY_COMPLETION_RULE)은
    # 꺼져 있다. 이 문단이 빠지면 모델이 잘린 컷아웃에서 없는 다리/꼬리를 지어낸다.
    "Everything visible in the reference must stay exactly as visible throughout the "
    "entire clip: preserve the visible crop, and keep the same fur pattern, same "
    "lighting, same background. Every body part that is visible in the reference must "
    "remain visible and structurally consistent in every frame. Do not zoom out, widen "
    "the framing, or change the shot — any completed anatomy is drawn inside the "
    "existing frame, never by pulling the camera back. "
    f"{IDLE_BODY_COMPLETION} "

    "The pet must NOT appear frozen. It should feel quietly alive while resting. "

    "PRIMARY IDLE MOTION: natural breathing originates in the chest, ribcage, and abdomen. "
    "The chest and belly gently expand during inhalation and soften during exhalation. "
    "This is a subtle anatomical expansion and contraction, not whole-body vertical bobbing. "
    "The paws and overall body placement remain anchored. "

    "Small secondary motion caused naturally by breathing is allowed: tiny movement in the "
    "shoulders, neck, fur, and posture balance may follow the breath. These motions must stay "
    "low-amplitude and must never become locomotion or visible body drift. "

    # 여기에는 문장 **조각**이 남아 있었다. 앞의 허용문("작은 고개 돌림을 허용한다")을
    # 지우면서 뒷절만 살아남아, 모든 BREATH 프롬프트에 주어 없는 비문이 실려 나갔다.
    # 허용문을 되살리지는 않는다 — 무엇을 움직이는가는 모션 모듈이 정한다. 여기서는
    # 모션 모듈이 **요청했을 때** 어떻게 움직여야 하는지만 규정한다.
    "If the motion description explicitly requests a small head or gaze adjustment, that "
    "movement must involve the neck and upper body naturally rather than looking like an "
    "isolated rotating head. "

    # ── 루프 종료 상태 ────────────────────────────────────────────────────────
    # **약화하면 안 되는 계약이다.** BREATH 클립의 t=0 이 곧 휴지 자세라는 전제 위에
    # IdleEvent 의 seam-aligned 복귀가 서 있다(lib/action-return-transition.ts).
    # "가깝게(close to)" 로 느슨해지면 이음매가 눈에 보이고, 런타임에서는 고칠 방법이
    # 없다. 실측상 포즈 드리프트는 단조(monotonic)라 한 방향으로 멀어지기만 하므로
    # 종료 상태를 **명시적으로** 못 박아야 한다.
    "LOOP CLOSURE: the clip must begin and end in the identical resting pose. The final "
    "frame must match the first frame's pose, position, scale, and head orientation as "
    "closely as possible so the video can loop naturally. "
    "Continuous breathing should complete naturally across the loop. "
    "A discrete micro-event, if requested, may occur once during the clip but must "
    "complete a full cycle and return to its starting state before the clip ends. "

    "Keep all motion calm, organic, asymmetrical, and low-amplitude. "
    "Avoid rigid stillness, mechanical repetition, whole-body bobbing, pose drift, "
    "camera movement, anatomy changes, or exaggerated reactions."
)

# ── 아이들 **이벤트** 전용 공통 제약 ─────────────────────────────────────────
#
# IDLE_COMMON_CONSTRAINT 를 이벤트에 그대로 쓸 수 없는 이유가 셋이다.
#
# 1) 주역 선언이 충돌한다. 저쪽은 "PRIMARY IDLE MOTION: natural breathing" 으로
#    **호흡을 주 모션으로 선언**한다 — BREATH 클립에서는 맞다. 그런데 이벤트 모션
#    모듈도 "the primary motion in this clip is one blink" 라고 선언하므로, 한
#    프롬프트에 주역이 둘 들어간다. 실측에서 이긴 쪽은 호흡이었다: BLINKING 은
#    눈을 감지 않았고(프레임 전수 확인), EAR_TWITCHING 은 BREATH 와 구별되지 않았다.
#
# 2) 분량이 뒤집혀 있었다. 저쪽은 3,014자로 최종 프롬프트의 42~46% 를 차지하는데,
#    실제로 요구하는 동작을 담은 모션 모듈은 17~25% 뿐이었다. 같은 내용을 두 번
#    말하는 문단(보이는 것 보존)과 꼬리의 Avoid 문장(부정 목록은 어차피 따로 붙는다)
#    을 덜어내 비중을 되돌린다.
#
# 3) 호흡은 지우면 안 된다 — 지우면 모델이 얼어붙어 이벤트 한 번 외에는 아무것도
#    살아 있지 않은 클립이 된다. 그래서 삭제가 아니라 **격하**한다: 계속되지만
#    이벤트와 경쟁하지 않는 배경 모션으로.
#
# ⚠️ "호흡만 있는 클립은 실패다" 류의 문장을 넣지 마라. EAR_TWITCHING /
# TAIL_WAGGING 의 안전한 실패 지시("귀·꼬리를 확인할 수 없으면 호흡만 남겨라")와
# 정면으로 모순되는 자기 취소 쌍이 된다 — 이 파일이 반복해서 밟은 함정이다.
#
# ⚠️ "the micro-event described above" 는 조립 순서에 의존한다.
# build_idle_event_prompt 는 **모션 모듈을 먼저** 놓는다. 순서를 뒤집으면 이
# 문장이 거짓말이 된다 (test_idle_event_prompts.py 가 순서를 고정한다).
IDLE_EVENT_COMMON_CONSTRAINT = (
    "Camera angle and framing remain completely fixed: no pan, no tilt, no zoom, no "
    "dolly, no reframing. "
    "The pet stays anchored in the same overall resting position throughout the clip. "
    "Do not translate, rotate, walk, step, or change the pet's overall scale or "
    "silhouette. Preserve identity, anatomy, the visible crop, fur pattern, markings, "
    "lighting, and background exactly as in the reference, and keep every body part "
    "that is visible in the reference visible and structurally consistent in every "
    "frame. Do not zoom out or widen the framing — any completed anatomy is drawn "
    "inside the existing frame, never by pulling the camera back. "
    f"{IDLE_BODY_COMPLETION} "

    "MOTION PRIORITY: the micro-event described above is the primary motion of this "
    "clip and must be plainly visible on playback. Quiet natural breathing continues "
    "underneath it as background motion only — a subtle expansion and relaxation of "
    "the chest and abdomen, low-amplitude, never whole-body vertical bobbing, and "
    "never large enough to compete with, mask, or stand in for the micro-event. "
    "Apart from breathing and the micro-event itself, the pet stays quietly still. "

    "LOOP CLOSURE: the clip begins and ends in the identical resting pose. The final "
    "frame must match the first frame's pose, position, scale, and head orientation as "
    "closely as possible so the clip can be cut back to the resting loop without a "
    "visible seam. The micro-event happens once, completes a full cycle, and returns to "
    "its starting state before the clip ends. "

    "Keep all motion calm, organic, and anatomically natural."
)

# ── 행동(TOUCH/VOICE/NFC) 공통 제약 ─────────────────────────────────────────
# IDLE 과 달리 "정지/루프"를 요구하지 않는다 — 행동은 한 번 일어나는 반응이다.
# 여기 들어가는 건 **모든 행동에 공통인 것만**이다:
#   피사체 동일성 / 전신 유지 / 카메라 고정 / 위치·스케일 유지 /
#   걷기·큰 이동·큰 회전 금지 / 사람·목줄 금지 / 순정 검정 배경.
# 개별 행동의 "무엇을 움직이는가"(고개를 돌린다, 꼬리를 흔든다 등)는
# LUMA_ACTION_PROMPTS 쪽에 그대로 남긴다.
#
# 시작/끝 동일 포즈, 루프, "머리를 움직이지 마라" 같은 문구는 여기 두지 않는다 —
# 그건 IDLE 전용이거나 개별 행동 정의에 속한다.
ACTION_COMMON_CONSTRAINT = (
    "Animate only this one dog from the reference keyframe. "
    "Preserve the dog's identity, fur colour, markings, and body proportions exactly "
    "as in the reference. "
    "Keep the dog's visible body visible for the entire clip — no limbs or body parts "
    "may be cut off, duplicated, or removed. "
    f"{ACTION_BODY_COMPLETION} "
    "Camera is completely fixed: no pan, no tilt, no zoom, no dolly, no rotation. "
    "Keep the dog at approximately the same scale and the same position on screen "
    "throughout the clip. "
    "The dog stays where it is: no walking, no stepping, no running, no approaching "
    "or leaving the camera, no large translation of the body, and no large rotation "
    "of the body. "
    "No people, no human hands or arms, no leash, no collar strap, no harness. "
    "Pure solid black background only — no scenery, no environment, no floor, no "
    "horizon, no props, no objects, no gradient, no shadows cast on a surface."
)

# ── COME_CLOSER 전용 제약 ────────────────────────────────────────────────────
# ACTION_COMMON_CONSTRAINT 를 **쓸 수 없다**: 그쪽은 "같은 스케일·같은 화면 위치"를
# 요구하는데, COME_CLOSER 의 핵심이 바로 스케일 증가이기 때문이다. 정면 그대로,
# 걷지 않고, 크기만 커지며 다가오는 것처럼 보이게 하는 것이 목표다.
#
# 걷기를 금지하는 이유는 PM 의도이기도 하고 실측 근거이기도 하다: 다리 교차가
# 생기면 자기 가림(self-occlusion)이 생기고, 검정 키잉 경계가 무너진다.
COME_CLOSER_ORIENTATION_RULE = (
    "ORIENTATION DURING APPROACH: inspect the reference orientation. "
    "If the pet already faces the camera, preserve that forward-facing orientation. "
    "If the pet begins partly sideways, three-quarter, or looking away, it must begin "
    "moving toward the camera immediately while naturally turning its head, shoulders, "
    "and upper body toward the viewer. "
    "Do not insert a separate standing or turning phase before forward movement. "
    "Reorientation and forward approach must happen together from the beginning of the action. "
    "The turn should be gradual and anatomically natural, never a rigid whole-body spin. "
    "As the pet closes distance, it should progressively become more front-facing. "
    "Once substantially aligned toward the viewer, it continues approaching essentially "
    "straight toward the camera and remains visually engaged with the viewer. "
    "Do not preserve a side-facing orientation throughout COME_CLOSER. "
)

COME_CLOSER_CONSTRAINT = (
    "Animate only this one dog from the reference keyframe. "
    "Preserve the dog's identity, fur colour, markings, and body proportions exactly "
    "as in the reference. "
    f"{COME_CLOSER_BODY_COMPLETION} "
    f"{COME_CLOSER_ORIENTATION_RULE}"
    
    "PRIMARY ACTION — a real, physical approach: the dog walks and eagerly trots "
    "forward toward the fixed camera and finishes the clip very close to it, as if it "
    "has come all the way up to its owner. "
    "The causal chain must be visible on screen: the dog takes real forward steps → "
    "the distance between the dog and the camera shrinks → and because that distance "
    "shrinks the dog naturally occupies more and more of the frame. "
    "The dog must travel through space toward the camera, not merely change size while "
    "standing in one spot. "
    "Never simulate the approach by scaling a stationary dog, by camera zoom, by camera "
    "dolly, or by reframing the shot. "
    "Camera is completely fixed: no pan, no tilt, no zoom, no dolly, no rotation, no "
    "reframing. Every change in the dog's apparent size comes from the dog's own "
    "forward movement through space. "
    "The framing changes as a consequence of that movement: the shot opens on the whole "
    "dog at its original distance and ends as a close view in which the head and chest "
    "fill the frame. "
    "The dog is in motion from the first frame — the clip must not open with the dog "
    "standing still, idling, or breathing in place. Its apparent size grows from the "
    "very first second onward, more and more steeply as the gap closes. "
    "Because the dog genuinely arrives very close to the lens, it is expected and "
    "correct that parts of its body extend beyond the frame edges near the end. Do not "
    "shrink the dog, pull back, or reframe in order to keep the whole body in shot. "
    "The dog's face must remain visible to the camera throughout the approach — no rear "
    "view, no back view, no meaningful profile turn, no turning away from the camera. "
    "Preserve correct anatomy at every moment: no duplicated limbs, no extra limbs, no "
    "malformed, melting, or morphing body parts. "
    "The dog advances essentially straight toward the camera: keep lateral drift small, "
    "no wandering left or right, no diagonal path across the frame. "
    "Legs and paws move naturally with the gait, but must never fully cross in front of "
    "the face or hide the eyes and muzzle. "
    "No people, no human hands or arms, no leash, no collar strap, no harness. "
    "Pure solid black background only — no scenery, no environment, no floor, no "
    "horizon, no props, no objects, no gradient, no shadows cast on a surface."
)

# COME_CLOSER 전용 부정 목록.
# 공용 LUMA_AVOID_CLAUSE 를 쓸 수 없다 — 거기에는 "walking" 이 들어 있어서
# 이 액션이 원하는 바로 그 동작을 부정 토큰으로 밀어낸다(IDLE/TOUCH/VOICE/NFC
# 에서는 옳은 항목이므로 공용 쪽은 손대지 않는다).
# 사람이 개를 끌고 가는 상황은 LUMA_SUBJECT_RULE 의 "no owner walking the dog"
# 가 이미 막는다. 대신 실제 실패 모드(정지·후퇴)를 부정 토큰으로 추가한다.
COME_CLOSER_AVOID_CLAUSE = (
    "Do not show or invent: person, human, man, woman, child, hand, hands, arm, arms, "
    "finger, fingers, leash, lead, rope, strap, harness, collar band, chain, owner, "
    "blurry, morphing, malformed anatomy, duplicated limbs, extra limbs, "
    "paw duplication, leg crossing artifacts, tail morphing, tail duplication, "
    "text, watermark, logo, "

    "static pose, frozen subject, stationary pet, barely moving subject, weak approach, "
    "standing still at the start, idle opening, breathing in place, delayed start, "
    "motionless first half, "

    "pet staying at the same distance, scaling in place, growing without moving, "
    "constant framing, reframing, pulling back, zooming out, "
    "approach plateau, stopping before reaching the camera, late retreat, "
    "retreating, backing away, moving away from camera, shrinking subject, "

    "excessive lateral drift, strong profile turn, sustained side profile, rear turn, "
    "body turning away, face occlusion, eyes hidden, muzzle hidden, "

    "camera zoom, camera dolly, camera movement, sudden scale jump."
)

# 피한 전용 변형을 쓴다. 공용 규칙은 손대지 않는다.
COME_CLOSER_SUBJECT_RULE = LUMA_SUBJECT_RULE.replace(
    "no owner walking the dog", "no owner leading or handling the dog"
)

IDLE_BLINK_MOTION = (
    "The only clearly visible intentional motion in this clip, other than quiet natural "
    "breathing, is one complete eye blink. "
    "Both eyes visibly close fully and then reopen fully once. The eyelid closure "
    "must be unmistakable on playback — do not substitute a tiny squint, eye narrowing, "
    "facial twitch, or head movement for the blink. "
    
    "Begin with the eyes naturally open for a brief resting moment, then perform one "
    "normal relaxed blink: upper and lower eyelids move naturally, the eyes become "
    "visibly closed for a brief instant, and then reopen to the original open-eyed "
    "expression. Keep the eyes open after the blink until the clip ends. "
    
    "The blink should be clearly readable at normal playback speed while remaining "
    "natural and calm. Preserve the original eye shape, iris color, gaze direction, "
    "muzzle, facial identity, and breed characteristics before and after the blink. "
    
    "Quiet natural breathing continues throughout. Do not turn, tilt, nod, translate, "
    "or reorient the head. Keep ears, tail, paws, legs, torso, pose, scale, framing, "
    "and camera position stable. No walking, stepping, weight shift, or locomotion. "
    
    "End with the eyes fully open and the pet in the same relaxed resting pose and "
    "framing as the starting frame."
)

IDLE_EAR_TWITCH_MOTION = (
    "A clearly visible ear twitch is the primary motion in this clip. "
    "One ear performs a single small, quick flick — the ear tip or ear body moves "
    "briefly and then settles back to its original position. If this dog's anatomy "
    "makes it natural, both ears may twitch, either together or one just after the "
    "other; a single-ear twitch is equally correct. "

    "The twitch must be unmistakable on playback but must stay small and natural — "
    "the kind of reflexive flick a resting dog makes at a distant sound. Do not "
    "substitute a head movement, a body shudder, or a whole-face motion for it. "

    "PRESERVE EAR ANATOMY EXACTLY. Keep this breed's own ear shape, size, thickness, "
    "set, fold, and resting orientation — erect ears stay erect, folded ears stay "
    "folded, drop ears stay dropped. Do not straighten, raise, perk up, enlarge, "
    "lengthen, reshape, duplicate, or invent ears, and do not add ears that are not "
    "clearly visible in the reference. The ears must look like the same ears before "
    "and after the twitch. "

    # 안전한 실패: 귀가 잘 안 보이는 컷아웃(장모종·접힌 귀·머리만 크롭)에서 억지로
    # 귀를 움직이려 하면 모델이 머리 전체를 변형시킨다. 그럴 바에는 조용한 호흡만
    # 남기는 편이 낫다 — 런타임은 어차피 휴지 자세끼리 이어 붙이므로 손해가 없다.
    "IF THE EARS ARE NOT CLEARLY VISIBLE in the reference, or a natural ear movement "
    "is not possible without altering the head, then do not attempt the twitch at all: "
    "simply keep the pet resting with quiet natural breathing for the whole clip. "
    "A calm breathing clip is a correct result. Deforming, reshaping, or reconstructing "
    "the head or ears is never acceptable. "

    "Quiet natural breathing continues throughout. Do not turn, tilt, nod, translate, "
    "or reorient the head — the muzzle keeps pointing exactly where it points in the "
    "reference. Keep eyes, tail, paws, legs, torso, pose, scale, framing, and camera "
    "position stable. No walking, stepping, weight shift, or locomotion. "

    "End with the ears back in their original resting position and the pet in the same "
    "relaxed resting pose and framing as the starting frame."
)

IDLE_HEAD_TILT_MOTION = (
    "One small, natural, curious head tilt is the primary motion in this clip. "

    # tilt(roll) 과 turn(yaw) 을 말로 구분해 준다 — 그냥 "tilt" 라고만 하면 모델이
    # 고개를 옆으로 돌려 버려 실루엣과 이음매가 함께 무너진다.
    "The tilt is a SIDEWAYS ROLL of the head: one ear dips slightly lower than the "
    "other while the muzzle keeps pointing in the same direction it points in the "
    "reference. It is NOT a turn — the head does not rotate left or right to look "
    "elsewhere, and the face never moves toward profile. Roughly 10 to 20 degrees of "
    "roll is enough; the gesture should read as gentle curiosity, not surprise. "

    "The neck and upper chest follow the tilt naturally with small secondary motion, "
    "so it looks like one connected body rather than a head rotating on its own. "

    "The head returns smoothly to its original upright orientation and stays there "
    "until the clip ends. Do not hold the tilt at the end. "

    "Quiet natural breathing continues throughout. Eyes stay open and keep looking in "
    "the same direction. Keep ears, tail, paws, legs, torso, pose, scale, framing, and "
    "camera position stable. No stepping, weight shift, or locomotion of any kind. "

    "End with the head upright and the pet in the same relaxed resting pose and "
    "framing as the starting frame."
)

IDLE_TAIL_WAG_MOTION = (
    "One gentle, natural tail wag is the primary motion in this clip. "
    "The tail sweeps softly from side to side two or three times and then settles back "
    "to its original resting carriage. The wag is relaxed and content — a resting dog's "
    "small happy wag, not an excited whole-body wag. "

    "PRESERVE TAIL ANATOMY EXACTLY. Keep this breed's own tail length, thickness, fur, "
    "curl, and resting carriage — a curled tail stays curled, a low tail stays low, a "
    "plumed tail keeps its plume. Do not lengthen, thicken, straighten, duplicate, or "
    "re-position the tail. A tail may only appear if the body-completion rule above "
    "allows it — that is, when enough torso is visible to infer it reliably. "

    # 안전한 실패 — EAR_TWITCHING 과 같은 철학. 꼬리는 잘리거나 몸에 가려지는 일이
    # 잦고(앉은 자세·뒤가 잘린 크롭·장모종), 없는 꼬리를 만들라고 하면 모델이
    # 엉덩이와 뒷다리까지 재구성한다.
    "IF THE TAIL CANNOT BE ESTABLISHED — the source is a face-only or head-only "
    "portrait, or the tail is ambiguous and cannot be inferred reliably from visible "
    "torso context — then do not attempt the wag at all: simply keep the pet resting "
    "with quiet natural breathing for the whole clip. A calm breathing clip is a "
    "correct result. Guessing a tail from a portrait with no visible body is never "
    "acceptable. "

    "The wag must come from the tail itself. Do not substitute a whole-body sway, a hip "
    "swing, a torso rotation, or a shifting of weight for the tail movement — the body "
    "stays anchored and only the tail moves. "

    "Quiet natural breathing continues throughout. Do not turn, tilt, nod, or reorient "
    "the head. Keep eyes, ears, paws, legs, torso, pose, scale, framing, and camera "
    "position stable. No stepping, no weight shift, no locomotion. "

    "End with the tail back in its original resting position and the pet in the same "
    "relaxed resting pose and framing as the starting frame."
)

# ── 아이들 **이벤트** 전용 부정 목록 ─────────────────────────────────────────
#
# ⚠️ 이름을 IDLE_AVOID_CLAUSE 로 두면 안 된다 — 이 파일 상단에 같은 이름의 상수가
# 이미 있고, 파이썬은 나중 정의를 쓴다. 그러면 위쪽 정의가 조용히 죽는다.
#
# ⚠️ 부정 목록은 **이벤트마다 다르다**. 공통 하나로 묶을 수 없다: 기본형에는
# "head tilt" 가 부정어로 들어 있는데, 그게 바로 HEAD_TILTING 이 요구하는 동작이다.
# COME_CLOSER 가 공용 목록의 "walking" 때문에 전진을 못 했던 것과 같은 함정이라,
# 여기서는 처음부터 기본 + 이벤트별 조합으로 나눠 둔다.

#: 모든 아이들 이벤트에 공통인 금지 — 이동·카메라·스케일·해부구조·눈·귀·꼬리 무결성.
#: 머리 움직임은 여기 **없다**(이벤트마다 다르므로).
IDLE_EVENT_AVOID_BASE = (
    f"{LUMA_AVOID_CLAUSE} {IDLE_AVOID_CLAUSE} "
    "Also do not show: standing up, sitting down, lying down, crouching, stepping, "
    "pawing, shifting weight, walking, running, approaching camera, moving away from "
    "camera, camera pan, camera tilt, camera zoom, camera dolly, camera shake, "
    "reframing, overall scale change, whole-body pose change, body repositioning, "
    "winking, squinting, startled expression, wide-eyed alarm, "
    "eyes closed at the end, eyes still closed in the final frame, "
    "reshaped ears, enlarged ears, lengthened ears, straightened ears, perked-up ears, "
    "added ears, duplicated ears, missing ears, ears changing shape or set, "
    "duplicated tail, extra tail, lengthened tail, straightened tail, "
    "malformed tail, detached tail, malformed joints, extra paws, "
    "deformed head, reconstructed head, morphing skull, "
    "open mouth, barking, panting, yawning, tongue out."
)

#: 머리가 **고정**돼야 하는 이벤트용 추가 금지 (BLINKING / EAR_TWITCHING / TAIL_WAGGING).
IDLE_EVENT_AVOID_HEAD_LOCKED = (
    "Additionally do not show: head turn, head tilt, head roll, head rotation, nodding, "
    "looking away, profile view, rear view, or any change of head orientation."
)

#: HEAD_TILTING 전용 — 기울임(roll)은 **허용**하되 돌아보기(yaw)만 막는다.
IDLE_EVENT_AVOID_HEAD_TILT = (
    "Additionally do not show: head turn, head yaw, looking away, profile view, rear "
    "view, turning the muzzle toward a different direction, full head rotation, "
    "exaggerated or cartoonish tilt, tilt held at the end of the clip, "
    "neck stretching, neck lengthening, or head detaching from the neck."
)

#: 하위호환 별칭 — 기본형(머리 고정)을 가리킨다. 기존 참조가 그대로 동작한다.
IDLE_EVENT_AVOID_CLAUSE = f"{IDLE_EVENT_AVOID_BASE} {IDLE_EVENT_AVOID_HEAD_LOCKED}"

#: 머리 방향 변화가 **의도된** 이벤트. 여기 없는 이벤트는 머리를 고정한다.
HEAD_MOVING_IDLE_EVENTS: frozenset[str] = frozenset({"HEAD_TILTING"})

#: 귀가 **움직여야** 하는 이벤트.
#:
#: HEAD_TILTING 과 똑같은 함정이 귀에도 있었다. 기본 목록의 "ears changing shape
#: or set" 은 사람에게는 "귀 형태/부착을 바꾸지 마라"로 읽히지만, 부정 토큰으로는
#: **귀가 원래 자리에서 움직이는 것 자체**를 억제한다 — 그게 EAR_TWITCHING 이
#: 요구하는 유일한 동작이다. 실측에서 EAR_TWITCHING 이 BREATH 와 구별되지 않은 데는
#: 이것이 한몫했다(다른 한몫은 IDLE_COMMON_CONSTRAINT 의 호흡 주역 선언).
EAR_MOVING_IDLE_EVENTS: frozenset[str] = frozenset({"EAR_TWITCHING"})

#: EAR_MOVING_IDLE_EVENTS 에서 기본 목록에서 빼는 문구. **이것만** 뺀다.
#: 형태·개수 가드(reshaped / enlarged / lengthened / straightened / perked-up /
#: added / duplicated / missing ears)는 그대로 남긴다 — 그쪽은 **지속적 변형**을
#: 막는 품질 가드이고, IDLE_EAR_TWITCH_MOTION 도 같은 것을 금지하므로 요구 동작과
#: 모순되지 않는다.
_EAR_SET_NEGATION = "ears changing shape or set, "

#: 아이들 이벤트 id → 모션 모듈.
#: Phase 1A = BLINKING, Phase 2 = EAR_TWITCHING, Phase 4 = HEAD_TILTING / TAIL_WAGGING.
IDLE_EVENT_MOTIONS: dict[str, str] = {
    "BLINKING": IDLE_BLINK_MOTION,
    "EAR_TWITCHING": IDLE_EAR_TWITCH_MOTION,
    "HEAD_TILTING": IDLE_HEAD_TILT_MOTION,
    "TAIL_WAGGING": IDLE_TAIL_WAG_MOTION,
}


def is_idle_event(action_key: str) -> bool:
    """이 액션 키가 아이들 이벤트인가 (프리미엄 액션과 조립 규칙이 다르다)."""
    return (action_key or "").upper() in IDLE_EVENT_MOTIONS


def idle_event_avoid_clause(event_key: str) -> str:
    """
    이 아이들 이벤트에 맞는 부정 목록.

    머리를 움직이는 이벤트(HEAD_TILTING)에는 머리 고정 금지를 붙이지 않는다 —
    붙이면 요구 동작 자체를 부정 토큰으로 밀어내게 된다. 귀를 움직이는
    이벤트(EAR_TWITCHING)에서 귀 위치 고정 문구를 빼는 것도 같은 이유다.
    """
    key = (event_key or "").upper()
    tail = (
        IDLE_EVENT_AVOID_HEAD_TILT
        if key in HEAD_MOVING_IDLE_EVENTS
        else IDLE_EVENT_AVOID_HEAD_LOCKED
    )
    base = IDLE_EVENT_AVOID_BASE
    if key in EAR_MOVING_IDLE_EVENTS:
        base = base.replace(_EAR_SET_NEGATION, "")
    return f"{base} {tail}"


def build_idle_event_prompt(event_key: str) -> str:
    """
    아이들 이벤트 모션 문장 = **모션 모듈 먼저**, 그 뒤에 이벤트 전용 공통 제약.

    순서가 계약이다. 요구 동작을 맨 앞에 놓아야 모델이 그것을 주역으로 읽고,
    IDLE_EVENT_COMMON_CONSTRAINT 의 "the micro-event described above" 가 성립한다.

    쓰는 제약이 IDLE_COMMON_CONSTRAINT 가 **아니라는 점**이 중요하다 — 그쪽은
    호흡을 주 모션으로 선언해서 이벤트 모듈과 경합한다(해당 상수 위 주석 참고).

    부정 목록/피사체 규칙은 build_scenario_luma_prompt 가 붙인다 — 다른 액션과
    조립 지점을 통일해 둬야 한 곳만 고치면 전부 반영된다.
    """
    key = (event_key or "").upper()
    motion = IDLE_EVENT_MOTIONS.get(key)
    if motion is None:
        raise ValueError(f"Unknown idle event: {event_key!r}")
    return f"{motion} {IDLE_EVENT_COMMON_CONSTRAINT}"


IDLE_COMMON_CONSTRAINT_HEAD_ROTATION = (
    "Camera angle completely fixed, identical to the original photo's exact angle "
    "and framing. Subject's body position and pose must remain unchanged. No body "
    "rotation, no turning of the torso, no shifting of body position. Same fur "
    "pattern, same lighting, same background. Head rotation is allowed only as "
    "specifically described below — everything else must stay perfectly still."
)

# ── 행동별 (4종) — 크레딧·배치 API용 ─────────────────────────────────────────
LUMA_ACTION_PROMPTS: dict[str, str] = {
    # 컷아웃이 전신인지 상반신인지 얼굴만인지 아직 분류하지 않으므로, 어느 쪽이든
    # 안전하도록 "보이는 부분에서만" 움직이라고 조건부로 지시한다.
    "IDLE": (
        "Animate only subtle natural idle micro-movement within the parts of the pet "
        "that are already visible in the reference. "

        "If the chest or upper torso is visibly present, show gentle natural breathing "
        "through a small expansion and relaxation of the chest and abdomen. "

        "While resting and continuing to breathe naturally, the pet may make one small, "
        "slow, curious head turn to one side and then gently return toward its original "
        "head orientation. The head turn must remain subtle and natural. "
        "The body stays anchored in the same position; do not turn or translate the torso. "

        "If the visible crop is mainly the head or face, subtle portrait-style life such "
        "as a soft blink, tiny gaze adjustment, minimal ear twitch, or small head turn "
        "is allowed. "

        "Do not zoom, move the camera, create locomotion, or substantially change the "
        "overall body pose or framing. "

        # 신체 완성은 IDLE_COMMON_CONSTRAINT 의 BODY COMPLETION 조항이 관장한다.
        # 여기서 다시 금지하면 그 조항을 부정 토큰으로 밀어내게 된다.
        "Any body part that is not completed under the body-completion rule above must "
        "not be hinted at, half-drawn, or faded in — either it is completed properly or "
        "it stays absent."
    ),
    # TOUCH — 머리를 쓰다듬는 손길에 반응하는 순간. 이벤트 클립이지 루프가 아니다.
    #
    # 예전 문구는 "slight tail wag" 를 **필수**로 요구했다. 꼬리가 안 보이는 컷아웃
    # (앉은 자세, 뒤가 잘린 크롭, 장모종)에서는 없는 꼬리를 만들어 내라는 뜻이 되어
    # PM 원칙("보이는 해부구조만 애니메이션")을 정면으로 어겼다. 이제 조건부다.
    #
    # 표현 강도: IDLE 보다는 살짝 크고, 이동 동작보다는 훨씬 작다. 닻을 내린
    # (anchored) 반응이어야 한다.
    # COME_CLOSER — 주인을 알아보고 다가오는 프리미엄 액션 (웹 전용, 1회 재생).
    # 걷지 않고 **스케일 증가**로 접근을 표현한다. 자체 제약 블록을 쓴다
    # (ACTION_COMMON_CONSTRAINT 의 "같은 스케일" 조항과 정면충돌하기 때문).
    "COME_CLOSER": (
        "The dog sees the viewer, lights up with recognition, and comes all the way to "
        "them. "
        "Beat 1 — the very first frame: the dog is ALREADY moving toward the camera. "
        "It is mid-step from frame one, at its original distance and full-body framing, "
        "with an eager happy expression. There is no standing still, no pause, no idle "
        "beat, and no breathing-in-place section — the clip opens on motion that is "
        "already under way. "
        "Beat 2 — middle: the dog keeps walking and eagerly trotting straight toward "
        "the camera, one forward step after another, never stopping, never settling, "
        "never turning back. It grows larger in frame from the very first second and "
        "keeps growing, faster and faster as the gap closes. "
        "Beat 3 — final moments: the dog is now very close to the lens. Its head and "
        "chest fill most of the frame, the opening full-body view has naturally become "
        "a close view, and parts of its body pass beyond the frame edges. It finishes "
        "right in front of the viewer with its face dominating the frame. "
        "The forward travel is continuous from the first second to the last, and the "
        "final framing must be dramatically closer than the opening — a viewer should "
        "see the dog cross most of the distance to the camera, not edge slightly "
        "nearer. "
        "The natural vertical bounce of an eager gait is welcome. "
        "If this dog's ears are naturally expressive, an eager ear perk is allowed; "
        "ears may also stay still — do not force ear movement. If a tail is naturally "
        "visible in the reference, a happy wag is allowed; if no tail can be "
        "established under the body-completion rule, do not guess one. "
        "Eyes stay open, bright and locked on the viewer. The mouth may open into a "
        "soft happy expression, but do not bark. "
        "The dog must never turn away from the camera, never retreat, and never move "
        "backwards or further away."
    ),
    "TOUCH": (
        "The dog reacts as if being gently petted on the head — a warm, contented, "
        "happy response. "
        "Allow only a very small nuzzle or lean of the head toward the touch. The head "
        "keeps facing essentially the same direction as in the reference: no profile "
        "turn, no looking away, no large head rotation. "
        "If this dog's ears are naturally expressive, a subtle ear response is allowed; "
        "ears may also stay still — do not force ear movement. "
        "If a tail is naturally visible in the reference, a slight gentle wag is "
        "allowed; if no tail can be established under the body-completion rule, do "
        "not guess one. "
        "The whole response stays anchored — slightly more expressive than resting, but "
        "far smaller than any walking, approaching, or locomotion movement. "
        "All paws stay planted exactly where they are: no stepping, no pawing, no "
        "shifting weight to another leg, no walking, no standing up, no sitting down, "
        "no lying down, no change of posture, and no large rotation of the body. "
        "Do NOT show a human hand, arm, or any person — only the dog's reaction is "
        "visible. "
        "By the end of the clip the dog settles back to a posture close to the starting "
        "pose so that returning to idle does not look abrupt — an exact match between "
        "the first and last frame is not required."
    ),
    # VOICE — 익숙한 보호자 목소리를 듣고 "귀 기울이는" 순간. 이벤트 클립이지
    # 루프가 아니다(첫/끝 프레임 일치 요구 없음). 다만 끝 자세가 시작과 크게
    # 달라지면 IDLE 로 돌아갈 때 툭 튀므로 "가깝게"까지만 요구한다.
    #
    # 각도를 숫자로 못 박는 이유: 예전 문구("turns head")는 상한이 없어서 모델이
    # 옆모습까지 돌려 버렸다(실측 클립에서 확인). 5~10° 는 "알아챘다"가 읽히는
    # 최소치이면서 실루엣이 무너지지 않는 범위다.
    #
    # 귀 움직임은 **조건부**다. 접힌 귀/장모종처럼 귀가 표현적이지 않은 개에게
    # 강제하면 없는 해부구조를 만들어 내려 한다.
    "VOICE": (
        "The dog hears a familiar owner's voice coming from off-camera and becomes "
        "quietly attentive. "
        "Allow only a small change in head orientation — a gentle turn or tilt toward "
        "the sound of roughly 5 to 10 degrees at most. The head must keep facing "
        "essentially the same direction as in the reference: no profile turn, no "
        "looking away, no large head rotation, no full turn of the muzzle. "
        "If this dog's ears are naturally expressive, a subtle ear perk or twitch is "
        "allowed; ears may also stay still — do not force ear movement. "
        "A very small attentive lift or shift of the chest and upper body is allowed. "
        "All paws stay planted exactly where they are: no stepping, no pawing, no "
        "shifting weight to another leg, no walking, no standing up, no sitting down, "
        "no change of posture. "
        "Eyes stay open and alert. Do not open the mouth, do not bark, do not pant, "
        "and do not dip or lower the neck toward the ground. "
        "By the end of the clip the dog settles back to a posture close to the starting "
        "pose so that returning to idle does not look abrupt — an exact match between "
        "the first and last frame is not required."
    ),
    # NFC — 익숙한 장소를 알아보고 차분히 둘러보는 순간. 이벤트 클립이지 루프가 아니다.
    #
    # 표현 강도 사다리: IDLE(회전 없음) < VOICE(5~10°) < NFC(10~15°) < 이동(금지).
    # NFC 는 VOICE 보다 "조금 더" 움직이지만 여전히 작다. 숫자로 못 박는 이유는
    # VOICE 와 같다 — 상한 없는 "looks around" 는 옆모습까지 돌아가 버린다.
    #
    # 킁킁거림(sniff)은 가장 위험한 지시다. 그대로 두면 코를 바닥까지 내리고
    # 웅크리며 냄새를 쫓아 걷는다 — 즉 이동 동작이 된다. 그래서 "코끝만"으로
    # 한정하고 바닥/웅크림/추적을 명시적으로 막는다.
    # NFC — 익숙한 장소가 나타난 걸 알아채는 순간. 이벤트 클립이지 루프가 아니다.
    #
    # 1차 배치는 3/3 전부 실패했다(90~180° 회전 + 걸어 나감). 원인은 각도 상한이
    # 아니라 **동사**였다: "look around"(둘러보다)와 "sniff"(킁킁거리다)가 각각
    # '주위를 살펴라', '냄새를 쫓아라'로 읽혀 회전과 이동을 정당화했다.
    # "to one side and back" 도 왕복 회전을 지시하는 셈이었다.
    #
    # 대조군이 명확하다: 같은 모델·같은 개·같은 설정에서 VOICE(5~10°, 둘러보기
    # 동사 없음, 킁킁 없음)는 프로필 턴이 0/3 이었다. 그래서 NFC 를 VOICE 의
    # 검증된 구조로 되돌리고, 정면 유지를 명시적으로 요구한다.
    "NFC": (
        "The dog notices that a familiar place has appeared and shows calm recognition "
        "— a quiet, contented awareness. "
        "Allow only a very small change in head orientation, roughly 5 to 10 degrees at "
        "most. "
        "The dog's face must remain visible to the camera in every single frame. Never "
        "turn the head away from the camera, never turn the body away from the camera. "
        "No rear view, no back view, no profile turn, no looking away, no large head "
        "rotation. "
        "If this dog's ears are naturally expressive, a subtle ear response is allowed; "
        "ears may also stay still — do not force ear movement. A soft change of "
        "expression in the eyes is allowed. "
        "The body stays completely anchored. All paws stay planted exactly where they "
        "are: no stepping, no pawing, no shifting weight to another leg, no walking, no "
        "standing up, no sitting down, no lying down, no crouching, no change of "
        "posture, and no rotation of the body. "
        "Do not sniff, do not search, do not follow a scent, and do not lower the nose "
        "or neck toward the ground. "
        "The mood is calm and content, not alarmed or excited. No person accompanying, "
        "no leash. "
        "By the end of the clip the dog settles back to a posture close to the starting "
        "pose so that returning to idle does not look abrupt — an exact match between "
        "the first and last frame is not required."
    ),
}

# ── 단순 2종 (generate-pet-video 레거시) ────────────────────────────────────
# ⚠️ IDLE_COMMON_CONSTRAINT 를 **반드시** 앞에 붙인다.
#
# 이게 빠져 있었다. 메인 BREATH 경로(/generate-pet-video → build_idle_action_prompts)
# 는 이 상수 하나만 쓰는데, 여기에 공통 제약이 없어서 고정 카메라·정체성 보존·
# 이동 금지·**루프 종료 상태**가 전부 모델에 전달되지 않았다. 크레딧 경로
# (build_scenario_luma_prompt)는 common 으로 따로 붙이고 있어서 두 경로가 갈라져 있었다.
#
# 루프 종료 상태가 특히 중요하다 — IdleEvent 의 seam-aligned 복귀가 "BREATH 의 t=0 이
# 곧 휴지 자세"라는 약속 위에 서 있다. 그 약속을 만드는 문장이 바로 이 제약이다.
#
# 중복 주의: build_scenario_luma_prompt 는 IDLE 에 common 을 **따로** 붙이므로
# LUMA_ACTION_PROMPTS["IDLE"] 본문에는 넣지 않는다. 경로마다 정확히 한 번씩만 들어간다.
LUMA_PROMPT_IDLE = (
    f"{IDLE_COMMON_CONSTRAINT} {LUMA_ACTION_PROMPTS['IDLE']} "
    f"{LUMA_SUBJECT_RULE} {LUMA_AVOID_CLAUSE}"
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
    action_key: str,
    *,
    motion_ko_suffix: str = "",
) -> str:
    """
    System B 행동 영상용 최종 한 줄 프롬프트 — **펫 전용 레이어**.

    장소(place)는 더 이상 프롬프트에 들어가지 않는다. 기기 구조상 배경은 별개
    레이어이기 때문이다: 앱이 /demo/play 로 theme_id 를 보내면 Pi 가 배경 영상을
    틀고, 펫 클립은 S23 에서 키잉되어 그 위에 얹힌다. 예전처럼 "Environment: 눈 덮인
    숲..." 을 넣으면 모델이 배경을 그려 넣고, 그 픽셀이 검정 키를 통과해 Pi 배경
    위에 또 다른 숲을 덧그리게 된다.

    place_key 자체는 과금·저장 경로·모션 조회·/device/sync 에서 그대로 쓰인다 —
    여기서 빠지는 건 "모델에게 장소를 설명하는 것"뿐이다.
    """
    action_key = action_key.upper()
    # 아이들 이벤트는 모듈 조립(공통 제약 + 모션)으로 만든다 — LUMA_ACTION_PROMPTS
    # 표에 넣지 않는 이유는 조립 규칙 자체가 다르기 때문이다.
    if is_idle_event(action_key):
        motion = build_idle_event_prompt(action_key)
    else:
        motion = LUMA_ACTION_PROMPTS.get(
            action_key,
            "natural subtle motion appropriate to the scene",
        )
    extra = f" ({motion_ko_suffix})" if motion_ko_suffix else ""
    # 액션별 공통 제약 선택:
    #   IDLE        → 자체 제약을 이미 갖고 있다 (중복 부착 금지)
    #   COME_CLOSER → 전용 제약. ACTION_COMMON_CONSTRAINT 의 "같은 스케일" 조항과
    #                 정면충돌하므로 절대 붙이지 않는다.
    #   그 외        → ACTION_COMMON_CONSTRAINT
    if action_key == "IDLE":
        common = f"{IDLE_COMMON_CONSTRAINT}"
    elif is_idle_event(action_key):
        # build_idle_event_prompt() 가 이미 IDLE_EVENT_COMMON_CONSTRAINT 를 모션
        # **뒤에** 붙였다. 여기서 또 붙이면 같은 제약이 두 번 들어가고, 게다가
        # BREATH 용 제약을 붙이면 주역 선언이 충돌한다.
        common = ""
    elif action_key == "COME_CLOSER":
        common = f"{COME_CLOSER_CONSTRAINT} "
    else:
        common = f"{ACTION_COMMON_CONSTRAINT} "
    # 부정 목록도 액션별로 고른다. 공용 목록의 "walking" 이 COME_CLOSER 가
    # 요구하는 전진 자체를 억제하고 있었다 — 다른 액션에서는 그대로 옳다.
    if action_key == "COME_CLOSER":
        avoid = COME_CLOSER_AVOID_CLAUSE
    elif is_idle_event(action_key):
        # 이벤트별로 다르다 — HEAD_TILTING 은 "head tilt" 를 부정하면 안 된다.
        avoid = idle_event_avoid_clause(action_key)
    elif action_key == "IDLE":
        # 공용 금지(사람·목줄·워터마크)를 반드시 함께 붙인다 — IDLE_AVOID_CLAUSE 는
        # 스타일 금지만 담고 있어서 단독으로 쓰면 사람/목줄 방어가 사라진다.
        avoid = f"{LUMA_AVOID_CLAUSE} {IDLE_AVOID_CLAUSE}"
    else:
        avoid = LUMA_AVOID_CLAUSE

    subject = COME_CLOSER_SUBJECT_RULE if action_key == "COME_CLOSER" else LUMA_SUBJECT_RULE
    return (
        f"Image-to-video from keyframe {image_url}. "
        f"{common}"
        f"Motion: {motion}{extra}. "
        f"{subject} "
        "High-end minimalist cinematic lighting, hyper-realistic depth, no text. "
        f"{avoid}"
    )
