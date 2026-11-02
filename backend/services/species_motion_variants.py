"""
종(species) 인식 모션 변형 — 하나의 상용 액션 id 가 종에 따라 다른 움직임으로 해석된다.

PLAY_WITH_ME ("Let's Play!") 가 첫 사례다:
  * DOG → PLAY_BOW    가슴/앞몸을 낮추고 엉덩이는 든 놀자 인사(play bow)
  * CAT → PLAY_CROUCH 놀이 준비 웅크림(play crouch) — 고양이는 play bow 를 하지 않는다

── 원칙 ─────────────────────────────────────────────────────────────────────
* 사용자가 보는 것은 **하나의 액션**(PLAY_WITH_ME)이다. 구매/소유/발행/재생 전부
  motion_id = PLAY_WITH_ME 로 흐르고, 종별 변형은 **생성 계약(contract) 안에서만**
  해석된다 (contract["species_variant"]).
* 종은 펫 모션 프로필(motion_reference_service.derive_motion_profile)의 species —
  형태 프로필 → YOLO animal_class → VLM species 순으로 이미 정직하게 측정된 값이다.
  여기서 종을 새로 추정하지 않는다.
* 지원하지 않는 종(RABBIT/OTHER/UNKNOWN)은 **과금 전에** 거절된다
  (premium_purchase 의 사전 게이트 + resolve_video_generation_spec 의 fail-closed).
* 소형견/대형견 구분은 **하지 않는다** — 움직임은 레퍼런스 이미지와 형태 프로필이
  결정한다. 품종 로직 금지(motion_spec 의 forbidden_logic 과 동일).
* 이 모듈은 motion_spec 을 import 하지 않는다 (motion_spec 이 이 모듈을 읽는다).

── v2 (2026-10-29): 종별 중간 키프레임 + 2 세그먼트 ─────────────────────────
STAND_READY → (짧은 놀이 기대) → PLAY 포즈 → STAND_READY → BREATHING.
  * 변형마다 **전용 키프레임 역할**(keyframe_role: PLAY_BOW / PLAY_CROUCH)이 있다.
    action_keyframe_spec 에 등록된 포즈 역할이고, 승인된 STAND_READY 플레이트를
    앵커로 한 번 생성해 pet_action_keyframes 에 저장·재사용된다.
  * 영상은 양끝 조건부 세그먼트 둘이다 — A: STAND_READY → PLAY 포즈(놀이 기대
    리드인 포함), B: PLAY 포즈 → STAND_READY(되감기 아님, 자연 복귀). 둘을 이어
    하나의 PLAY_WITH_ME 자산으로 만든다 (motion_video_segments).
  * QA 는 기존 종별 VLM 필드에 중간 포즈/리드인/이음매/제자리 유지 필드와
    결정론 세그먼트 끝점·이음매 검사를 더한다. PASS/REVIEW/FAIL 의미는 그대로다.

── DOG 타이밍 다듬기 (2026-10-30, PLAY_BOW 만) ──────────────────────────────
라이브 결과: bow 자체는 좋았지만 STAND_READY 에서 거의 곧장 bow 로 들어가고 포즈를
너무 오래 잡았다. 그래서 세그먼트 A 의 리드인에 **제자리 작은 바운스 한 번**(네 발이
같은 자리에 착지)을 넣고 착지 뒤에 bow 로 내려가게 했으며, 세그먼트 B 는 "짧게 잡고
자연스럽게 일어남"으로 명시했다. 바운스는 이동/접근이 아니다 — DOG 의 수용 계약
(allowed_secondary / blocking / vlm_guidance)이 "같은 자리에 착지하는 단 한 번의
제자리 바운스"를 명시적으로 허용하고, 카메라 쪽으로 착지하거나 반복 점프로 bow 를
대체하면 그대로 차단한다. CAT(PLAY_CROUCH) 텍스트는 바이트 단위로 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

PLAY_WITH_ME = "PLAY_WITH_ME"
VARIANT_PLAY_BOW = "PLAY_BOW"
VARIANT_PLAY_CROUCH = "PLAY_CROUCH"

#: COME_PLAY ("Come Play!", 2026-10-30) — DOG 전용 V1. STAND_READY(홈) → 신나는 짧은
#: 트롯 접근 → 감속 → 카메라 가까이에서 play bow(근접 PLAY_BOW_NEAR 키프레임) → 짧은
#: 유지 → 홈으로 자연 복귀 → STAND_READY → BREATHING. PLAY_WITH_ME(제자리) /
#: COME_CLOSER(접근만, RETURN_HOME 동반)와 **독립**이다 — 그 둘의 정의/트리거는 불변.
COME_PLAY = "COME_PLAY"
VARIANT_COME_PLAY_BOW = "COME_PLAY_BOW"
#: QA-only 정책 리비전 (COME_PLAY 전용 VLM 평가기 버전 선택).
#: v2 (2026-10-30): 첫 라이브 런 감사 — 명시 타이밍 프롬프트, B 8 s, 자문 보행 필드, 조밀 보행 샘플링.
COME_PLAY_POLICY_REVISION = "come-play-v2"

#: QA-only 정책 리비전 — motion_acceptance_evaluator_version 이 이 값으로 PLAY_WITH_ME
#: 전용 VLM 평가기 버전을 고른다. 종 변형은 acceptance["species_variant"] 에 기록된다.
#: v2: 중간 키프레임/2 세그먼트 계약 — 전용 필드 4개 추가 + 세그먼트 결정론 검사.
PLAY_WITH_ME_POLICY_REVISION = "play-with-me-v3"

SPECIES_DOG = "DOG"
SPECIES_CAT = "CAT"
SPECIES_UNKNOWN = "UNKNOWN"

#: 세그먼트 id — 계약/메타데이터/durable 작업 키에 그대로 쓰인다 (순서 고정).
SEGMENT_A = "A"
SEGMENT_B = "B"
PLAY_WITH_ME_SEGMENT_IDS: tuple[str, ...] = (SEGMENT_A, SEGMENT_B)


class SpeciesVariantError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class SpeciesVariant:
    variant_id: str
    species: str
    #: 생성 서술(전체 동작 요약) — 계약/표시용. 실제 세그먼트 프롬프트는
    #: lead_in / into_pose / hold / return_to_stand 절로 조립된다.
    description: str
    #: 프롬프트 빌더가 서술 뒤에 붙이는 종별 동작 절(순서 유지) — 혼동 동작 구분.
    prompt_clauses: tuple[str, ...]
    #: VLM 에게 전용 필드의 **종별 의미**를 설명하는 지침.
    vlm_guidance: str
    #: 베타 수용 계약의 종별 본문 — motion_spec.acceptance_for_variant 가 레지스트리의
    #: 종 중립 베이스라인 위에 덮어쓴다.
    acceptance: dict[str, Any]
    #: 중간 포즈 키프레임 역할 (action_keyframe_spec.KEYFRAME_ROLES 의 키).
    keyframe_role: str
    #: 세그먼트 A 리드인(짧은 놀이 기대) 서술.
    lead_in: str
    #: 세그먼트 A 본동작: STAND_READY → PLAY 포즈.
    into_pose: str
    #: 세그먼트 B 시작부: 포즈 유지(짧게, 살아 있는 보조 동작).
    hold: str
    #: 세그먼트 B 본동작: PLAY 포즈 → STAND_READY (앞으로 흐르는 자연 복귀).
    return_to_stand: str
    #: 사람이 읽는 포즈 이름 (프롬프트/QA 문장 안에서 쓴다).
    pose_label: str
    #: 세그먼트 계획(A, B)의 단계 라벨 — 계약(contract["segments"][*]["phase"])에 박제된다.
    #: 기본값은 PLAY_WITH_ME 의 라벨이라 기존 계약은 바이트 단위로 같다.
    segment_phases: tuple[str, str] = ("lead_in_into_pose", "hold_return_to_stand")
    #: 세그먼트별 길이(초, 정수). None 이면 두 세그먼트가 스펙 길이(build_output_spec)를
    #: 같이 쓴다(PLAY_WITH_ME). COME_PLAY 는 접근(A)보다 복귀(B)에 시간이 더 필요하다.
    segment_durations_sec: Optional[tuple[int, int]] = None


#: PLAY_WITH_ME 전용 VLM 필드. 이름은 두 종이 **공유**한다(검사 이름이 레지스트리에서
#: 정적이어야 required_checks/authority 가 종과 무관하게 같다). 각 필드의 종별 의미는
#: SpeciesVariant.vlm_guidance 가 설명한다. 전부 긍정 극성(yes = 좋음).
#: v2 추가: intermediate_pose_matches_keyframe(중간 포즈가 공급된 PLAY 키프레임과
#: 일치), playful_lead_in_visible(짧은 놀이 기대 리드인), segment_seam_smooth(이음매에
#: 점프/멈춤/되감기 없음), stays_in_place_no_approach(카메라로 달려오지 않고 제자리).
PLAY_WITH_ME_QA_FIELD_NAMES: tuple[str, ...] = (
    "play_posture_recognized",
    "front_body_lowered",
    "rear_posture_correct",
    "playful_hold_visible",
    "attentive_playful_expression",
    "foreleg_support_balanced",
    "limb_count_correct",
    "joint_anatomy_plausible",
    "posture_not_lying_or_sitting",
    "smooth_return_to_stand_ready",
    "intermediate_pose_matches_keyframe",
    "playful_lead_in_visible",
    "segment_seam_smooth",
    "stays_in_place_no_approach",
)

#: VLM 필드 → 레지스트리 검사 이름 (motion_video_qa 가 그대로 매핑한다).
PLAY_WITH_ME_CHECKS: tuple[tuple[str, str], ...] = (
    ("vlm_play_posture", "play_posture_recognized"),
    ("vlm_play_front_lowered", "front_body_lowered"),
    ("vlm_play_rear_posture", "rear_posture_correct"),
    ("vlm_play_hold", "playful_hold_visible"),
    ("vlm_play_expression", "attentive_playful_expression"),
    ("vlm_play_support", "foreleg_support_balanced"),
    ("vlm_play_limb_count", "limb_count_correct"),
    ("vlm_play_anatomy", "joint_anatomy_plausible"),
    ("vlm_play_not_down", "posture_not_lying_or_sitting"),
    ("vlm_play_return", "smooth_return_to_stand_ready"),
    ("vlm_play_intermediate_pose", "intermediate_pose_matches_keyframe"),
    ("vlm_play_lead_in", "playful_lead_in_visible"),
    ("vlm_play_seam", "segment_seam_smooth"),
    ("vlm_play_in_place", "stays_in_place_no_approach"),
)
PLAY_WITH_ME_CHECK_NAMES: tuple[str, ...] = tuple(c for c, _ in PLAY_WITH_ME_CHECKS)

#: 결정론 세그먼트 검사 (motion_video_segments.evaluate_segments) — VLM 과 무관하게
#: 두 세그먼트가 **실제 승인 끝점 이미지**로 조건부 생성됐는지, 이음매가 연속인지.
PLAY_WITH_ME_SEGMENT_CHECK_NAMES: tuple[str, ...] = (
    "segment_a_starts_at_stand_ready",
    "segment_a_reaches_play_pose",
    "segment_b_starts_at_play_pose",
    "segment_b_returns_to_stand_ready",
    "segment_seam_continuity",
)

#: 레지스트리 required_checks / check_authority 에 들어가는 전체 목록.
PLAY_WITH_ME_REQUIRED_CHECK_NAMES: tuple[str, ...] = (
    *PLAY_WITH_ME_CHECK_NAMES,
    *PLAY_WITH_ME_SEGMENT_CHECK_NAMES,
)

#: 희소 샘플링에서 "못 봤다"가 "없었다"로 굳으면 안 되는 **관찰성** 필드. 구조/해부/
#: 끝점/이음매/제자리 필드는 제외한다 — 그쪽의 no 는 한 프레임만 분명히 보여도 결함이다.
PLAY_WITH_ME_OBSERVATIONAL_FIELDS: tuple[str, ...] = (
    "play_posture_recognized",
    "front_body_lowered",
    "rear_posture_correct",
    "playful_hold_visible",
    "attentive_playful_expression",
    "intermediate_pose_matches_keyframe",
    "playful_lead_in_visible",
)

_SHARED_ALLOWED_SECONDARY = [
    "natural breathing", "blinking", "subtle ear, head or tail response",
    "small torso and weight adjustments needed for realistic balance",
    "a brief playful anticipation (ear or gaze attention, weight shift, a small tail movement) before the pose",
    "small paw repositioning required by the intended pose while the paws stay grounded and balanced",
    "the change in body height and silhouette that the bow or crouch itself requires (not a position, scale or framing change)",
    "natural ear, face and tail carriage: the ears need not point forward and the tail need not be raised",
]

_SHARED_TEMPORAL_POLICY = {
    "brief_event": True,
    "absence_requires_reliable_coverage": True,
    "sparse_non_observation": "REVIEW",
    "occlusion_or_perspective_uncertainty": "REVIEW",
    "subtle_or_ambiguous_response": "REVIEW",
}

_SHARED_BLOCKING_TAIL = [
    "A limb is duplicated, missing, merged, detached or anatomically implausible.",
    "The pet visibly falls, floats, collapses, or loses plausible support and balance.",
    "A human, hand, arm, person, leash, toy, platform or other added object appears.",
    "The pet runs or steps toward or away from the camera, or clearly changes its home position, scale or framing "
    "(a change in body height or silhouette caused by the pose itself does not count).",
    "A visible jump, freeze, duplicated pose, prolonged motionless hold or reversed playback appears where the two segments join.",
    "The pet does not return to a grounded STAND_READY-compatible ending when the endpoint is clearly observable.",
]

_SHARED_ADVISORY = [
    "The posture, brief hold, lead-in or playful expression is plausible but occluded, too subtle, "
    "foreshortened by the front view, or insufficiently sampled to confirm.",
    "A minor weight shift, a brief settling pause at the join, or a small final placement difference "
    "does not break balance or the BREATHING seam.",
]

_SHARED_SEGMENT_NOTE = (
    "The asset is two start/end-conditioned segments joined in time order: segment A "
    "(STAND_READY → brief playful lead-in → the approved intermediate PLAY pose) and "
    "segment B (the PLAY pose → natural forward-time return → STAND_READY)."
)

#: DOG 전용 — 리드인의 제자리 바운스 한 번은 허용 보조 동작이다 (CAT 목록에는 없다).
_DOG_IN_PLACE_BOUNCE = (
    "one short, lively in-place bounce during the lead-in: a small vertical hop with all four "
    "paws landing softly back on the same spot before the bow (not a run, lunge or approach)"
)

#: DOG 전용 — bow 의 깊이는 개체의 해부에 따른다 (얕은 bow 도 정답; 품종 로직 아님).
_DOG_BOW_DEPTH = (
    "a shallower or deeper bow according to this dog's own leg length, body proportions and "
    "flexibility, as long as the front body is clearly lower than the hindquarters"
)

#: DOG 전용 차단 꼬리 — 공유 꼬리와 같되, "홈 위치 변경" 항목만 제자리 바운스를
#: 명시적으로 제외한다. 카메라 쪽으로 착지하는 바운스는 여전히 위 항목이 차단한다.
_DOG_BLOCKING_TAIL = [
    (
        c + " Neither does the single brief in-place bounce of the lead-in that lands on the same spot."
        if c.startswith("The pet runs or steps toward or away from the camera") else c
    )
    for c in _SHARED_BLOCKING_TAIL
]

PLAY_BOW = SpeciesVariant(
    variant_id=VARIANT_PLAY_BOW,
    species=SPECIES_DOG,
    keyframe_role="PLAY_BOW",
    pose_label="play bow",
    # 서술과 절은 **서로 겹치지 않는다** — 프롬프트 빌더가 둘을 이어 붙이고, fal Kling
    # 의 2,500자 상한 안에 신원/카메라/끝점 절까지 들어가야 한다 (라이브 422 사례).
    description=(
        "From the calm upright STAND_READY pose, the dog shows a brief playful anticipation "
        "and one short, lively bounce in place (a small hop, all four paws landing back on the "
        "same spot), then lowers into a clear, friendly play bow: the elbows bend and sink "
        "toward the ground so the chest drops low while the hindquarters stay at about their "
        "normal standing height on standing hind legs (hips not lifted, spine not arched), "
        "the bow only as deep as this dog's own build allows, the front paws grounded and "
        "supporting. The dog holds "
        "the inviting bow briefly with a bright attentive gaze and a relaxed or softly open "
        "mouth, then rises naturally back to the exact original STAND_READY pose"
    ),
    # 2026-10-30: 리드인 = 짧은 기대 + **제자리 바운스 한 번**(착지 뒤 bow). 세그먼트 A 의
    # 프롬프트 예산(fal Kling 2,500자, 테스트 여유 100자) 안에서 문장을 조였다.
    lead_in=(
        "a brief playful anticipation (bright ears, gaze, tail), then one short, lively "
        "bounce in place: a small hop landing all four paws on the same spot, settling "
        "for a beat"
    ),
    # 2026-10-30: 뒷몸은 평소 서 있는 높이 — "raised" 는 과한 엉덩이 들림을 부추겼다.
    into_pose=(
        "once landed, the front paws reposition slightly forward as the shoulders and chest "
        "lower with natural elbow bend, the hindquarters near standing height on supported "
        "hind legs, the bow as deep as this dog's build allows, until it settles exactly "
        "into the supplied final play-bow frame"
    ),
    hold=(
        "a brief beat in the inviting play bow, held playfully low and ready with a bright "
        "attentive gaze, a relaxed or softly open mouth, gentle breathing and a small natural "
        "tail wag"
    ),
    return_to_stand=(
        "the dog rises naturally: the shoulders and chest come up as it pushes smoothly "
        "through the forelegs, the elbows straighten, the hindquarters relax to a normal "
        "stance, and it settles into the supplied final STAND_READY frame with calm breathing"
    ),
    prompt_clauses=(
        "One small vertical hop in place, never a run, lunge or step toward the camera; land, "
        "then lower. The whole front body lowers, not just the head; the rear stays near "
        "standing height, not lifted or arched, and never sinks into a sit or lie-down.",
    ),
    vlm_guidance=(
        " For PLAY_WITH_ME on a DOG (PLAY_BOW), separately answer whether a recognizable "
        "play bow occurs: the front body visibly lowers with the elbows sinking toward the "
        "ground and the chest dropping (front_body_lowered); the hindquarters stay up at "
        "about their normal standing height on standing hind legs rather than sinking, "
        "sitting or lying (rear_posture_correct) — hips lifted higher than standing or an "
        "arched spine are NOT required and NOT a reason to answer no; judge the rear only "
        "for staying up and supported, and accept a shallower bow from a short-legged or "
        "stocky dog as long as the front body is clearly lower than the rear; the posture "
        "is held briefly (playful_hold_visible) with an "
        "attentive, playful expression (attentive_playful_expression); the forelegs keep "
        "realistic grounded support and balance (foreleg_support_balanced) — a small forward "
        "repositioning of the front paws into the bow is expected and correct; limb count "
        "and joint anatomy remain correct; the pet never substitutes lying down or sitting "
        "(posture_not_lying_or_sitting); and it returns smoothly to STAND_READY. "
        "intermediate_pose_matches_keyframe: the mid-clip play bow matches the supplied "
        "INTERMEDIATE play-bow keyframe (same pet, same framing, same posture). "
        "playful_lead_in_visible: a brief playful anticipation (ears, gaze, tail) and one "
        "short, lively in-place bounce — a small vertical hop with all four paws landing back "
        "on the same spot — precede the bow; that single hop is expected and correct. "
        "segment_seam_smooth: no visible jump, freeze, duplicated pose or reversed playback "
        "where the clip passes through the held bow. "
        "stays_in_place_no_approach: the dog keeps its home spot, scale and framing and "
        "never travels or steps toward or away from the camera; the lower body height and "
        "changed silhouette of the bow itself, small grounded paw repositioning, and the "
        "single brief in-place hop of the lead-in (landing on the same spot) are NOT a "
        "position change, sliding or a step, nor locomotion. A hop that lands clearly nearer to "
        "or farther from the camera, a run, or repeated hopping that replaces the bow IS a "
        "defect. Ears that do not point forward or a tail "
        "that is not raised are NOT defects: judge the pet's own natural ear, face and tail "
        "carriage. A head-only dip without the chest and elbows lowering is NOT a play bow. "
        "A clearly sampled no is a defect, but occlusion, front-view foreshortening or "
        "insufficient temporal coverage is unknown."
    ),
    acceptance={
        "description": (
            "From STAND_READY the dog shows a brief playful anticipation and one short, lively "
            "in-place bounce (a small hop landing all four paws back on the same spot), then "
            "performs a recognizable play bow: the chest and front body lower with the elbows "
            "sinking toward the ground while the hindquarters stay near their normal standing "
            "height (not lifted or arched), the bow as deep as this dog's own build allows, "
            "the forelegs keep realistic support, the playful inviting posture matching the "
            "approved PLAY_BOW "
            "keyframe is held briefly, and the dog rises naturally (forward in time, never "
            "reversed) to the same grounded STAND_READY pose with intact four-limb anatomy, "
            "staying in place throughout: the single bounce is part of the lead-in, not "
            "locomotion or an approach. " + _SHARED_SEGMENT_NOTE
        ),
        "expected_start_state": (
            "STAND_READY: calm upright stance with all four paws naturally grounded."
        ),
        "expected_intermediate_state": (
            "PLAY_BOW: shoulders and chest lowered toward the ground, front paws slightly "
            "forward and grounded, hindquarters at about normal standing height on supported "
            "hind legs (not lifted higher or arched), bow depth natural to this dog's build, "
            "attentive playful expression."
        ),
        "expected_end_state": (
            "STAND_READY: the dog is upright again on all four paws and the pose joins "
            "BREATHING smoothly without a visible jump."
        ),
        "blocking_criteria": [
            "Reliable evidence clearly shows no play bow, or a different primary action "
            "(a head-only dip, lying down, sitting, a run, or sustained repeated jumping that "
            "never becomes a bow) replaces it.",
            "The hindquarters clearly sink to a sit or lie-down instead of staying up near "
            "standing height.",
            "The lead-in bounce travels: the dog lands clearly nearer to or farther from the "
            "camera than it took off, or the single bounce becomes a sustained hopping sequence.",
            *_DOG_BLOCKING_TAIL,
        ],
        "advisory_criteria": [
            *_SHARED_ADVISORY,
            "The lead-in bounce is small, partly occluded or sampled only around its landing, "
            "so its height or liveliness cannot be confirmed.",
            "The hindquarters look lifted higher than standing or the spine arched — an "
            "exaggerated bow that is still a recognizable, supported play bow.",
        ],
        "allowed_secondary_motion": [
            *_SHARED_ALLOWED_SECONDARY,
            _DOG_IN_PLACE_BOUNCE,
            _DOG_BOW_DEPTH,
        ],
        "temporal_evidence_policy": dict(_SHARED_TEMPORAL_POLICY),
    },
)

PLAY_CROUCH = SpeciesVariant(
    variant_id=VARIANT_PLAY_CROUCH,
    species=SPECIES_CAT,
    keyframe_role="PLAY_CROUCH",
    pose_label="play crouch",
    description=(
        "From the calm upright STAND_READY pose, the cat shows a brief playful anticipation, "
        "then settles into a playful, ready-to-pounce play crouch: all four legs bend, the "
        "chest comes close to the ground and the hindquarters gather low and compact with the "
        "hind legs folded beneath the body rather than sprawled flat. The gaze is attentive "
        "and bright, with a subtle playful hindquarter wiggle and a small tail movement. The "
        "cat holds the crouch briefly, then rises smoothly back to the exact original "
        "STAND_READY pose"
    ),
    lead_in=(
        "a brief playful anticipation: the ears and gaze sharpen with natural attention, the "
        "pupils lock on with bright focus, the tail tip flicks and the weight shifts lightly "
        "for about one second"
    ),
    into_pose=(
        "all four legs bend, the chest comes close to the ground and the hindquarters gather "
        "low and compact with the hind legs folded beneath the body rather than sprawled flat; "
        "body height and silhouette change as the crouch requires, until the cat settles "
        "exactly into the supplied final play-crouch frame"
    ),
    hold=(
        "a brief beat in the alert play crouch with a bright attentive gaze, gentle breathing, "
        "a subtle hindquarter wiggle and a small natural tail movement"
    ),
    return_to_stand=(
        "the legs extend and the body rises smoothly out of the crouch, the hindquarters come "
        "up to a normal stance, and the cat settles into the supplied final STAND_READY frame "
        "with calm breathing"
    ),
    prompt_clauses=(
        "This is an alert play crouch, not lying flat on the side or belly and not a sit.",
    ),
    vlm_guidance=(
        " For PLAY_WITH_ME on a CAT (PLAY_CROUCH), separately answer whether a "
        "recognizable playful crouch occurs: the body visibly lowers with the legs bending "
        "and the chest coming close to the ground (front_body_lowered); the hindquarters "
        "gather low and compact with the hind legs folded beneath the body rather than "
        "sprawling flat (rear_posture_correct); the posture is held briefly "
        "(playful_hold_visible) with an attentive, playful expression "
        "(attentive_playful_expression); the forelegs keep realistic grounded support and "
        "balance (foreleg_support_balanced) — small paw repositioning into the crouch is "
        "expected and correct; limb count and joint anatomy remain correct; the pet "
        "never substitutes lying flat or sitting upright (posture_not_lying_or_sitting); "
        "and it returns smoothly to STAND_READY. intermediate_pose_matches_keyframe: the "
        "mid-clip crouch matches the supplied INTERMEDIATE play-crouch keyframe (same pet, "
        "same framing, same posture). playful_lead_in_visible: a brief playful anticipation "
        "(ears, gaze, tail flick, weight shift) precedes the crouch. segment_seam_smooth: no "
        "visible jump, freeze, duplicated pose or reversed playback where the clip passes "
        "through the held crouch. stays_in_place_no_approach: the cat keeps its home spot, "
        "scale and framing and never travels or steps toward or away from the camera; the "
        "lower body height and changed silhouette of the crouch itself, and small grounded "
        "paw repositioning, are NOT a position change, sliding or a step. Ears that do not "
        "point forward or a tail that is not raised are NOT defects: judge the pet's own "
        "natural ear, face and tail carriage. A clearly sampled no is a defect, but "
        "occlusion, front-view foreshortening or insufficient temporal coverage is unknown."
    ),
    acceptance={
        "description": (
            "From STAND_READY the cat shows a brief playful anticipation, then settles into a "
            "recognizable playful crouch: the body lowers with the legs bending and the chest "
            "near the ground, the hindquarters gather low and compact, the gaze stays "
            "attentive and playful, the posture matching the approved PLAY_CROUCH keyframe is "
            "held briefly, and the cat returns smoothly (forward in time, never reversed) to "
            "the same grounded STAND_READY pose with intact four-limb anatomy, staying in "
            "place throughout. " + _SHARED_SEGMENT_NOTE
        ),
        "expected_start_state": (
            "STAND_READY: calm upright stance with all four paws naturally grounded."
        ),
        "expected_intermediate_state": (
            "PLAY_CROUCH: all four legs bent, chest near the ground, hindquarters low and "
            "compact with the hind legs folded beneath the body, attentive playful gaze."
        ),
        "expected_end_state": (
            "STAND_READY: the cat is upright again on all four paws and the pose joins "
            "BREATHING smoothly without a visible jump."
        ),
        "blocking_criteria": [
            "Reliable evidence clearly shows no playful crouch, or a different primary action "
            "(lying flat, an upright sit, a full-body step) replaces it.",
            "The cat clearly flops onto its side or belly, or sits upright, instead of crouching.",
            *_SHARED_BLOCKING_TAIL,
        ],
        "advisory_criteria": list(_SHARED_ADVISORY),
        "allowed_secondary_motion": list(_SHARED_ALLOWED_SECONDARY),
        "temporal_evidence_policy": dict(_SHARED_TEMPORAL_POLICY),
    },
)


# ══════════════════════════════════════════════════════════════════════════
# COME_PLAY — DOG 전용 "달려와서 놀자" (2026-10-30)
# ══════════════════════════════════════════════════════════════════════════
#
#   A (5 s): STAND_READY(홈) ──(0.5 s 흥분 → 3 s 달려옴 → 1 s 제동·bow 하강)──▶ PLAY_BOW_NEAR
#   B (8 s): PLAY_BOW_NEAR ──(0.5 s 비트 → 기립 → 호 회전 → 트롯 복귀 → 홈 회전 → 정착)──▶ STAND_READY
#
# v2 (2026-10-30, 첫 라이브 런 감사): 초 단위 타이밍을 프롬프트에 명시, B 6→8 s, 이음매에서 정지
# 리드인/과도한 bow 유지 트리밍(motion_video_segments v2 + motion_profile), VLM 에 조밀 보행 창
# 2개와 자문 보행/회전 필드, 결정론 모션 프로파일 진단(QUALITY_ADVISORY).
#
# PLAY_BOW_NEAR 는 승인된 PLAY_BOW(홈 스케일) 키프레임을 앵커로 생성된 **근접** bow 스틸이다
# (action_keyframe_spec). 그래서 세그먼트 양끝이 전부 실제 승인 이미지이고, 결정론 끝점
# 검사가 "접근해서 커졌다 / 홈 스케일로 돌아왔다" 를 VLM 과 무관하게 잰다.

#: COME_PLAY 전용 VLM 필드. 전부 긍정 극성(yes = 좋음). PLAY_WITH_ME 와 이름이 겹치는
#: 필드(front_body_lowered …)는 의미가 같다 — 검사 이름(vlm_come_play_*)은 별개라 레지스트리
#: 권위/캐시가 섞이지 않는다.
COME_PLAY_QA_FIELD_NAMES: tuple[str, ...] = (
    "playful_approach_visible",
    "approach_gait_natural",
    "deceleration_before_bow",
    "play_bow_recognized",
    "front_body_lowered",
    "rear_posture_correct",
    "playful_hold_visible",
    "attentive_playful_expression",
    "near_bow_matches_keyframe",
    "limb_count_correct",
    "joint_anatomy_plausible",
    "posture_not_lying_or_sitting",
    "return_travel_to_home_visible",
    "returns_to_home_scale_and_position",
    "smooth_return_to_stand_ready",
    "segment_seam_smooth",
    "return_played_forward",
    # v2 자문 필드 — 조밀 보행 창(연속 프레임)에서 답한다. no 는 REVIEW 지 FAIL 이 아니다.
    "run_gait_coordinated",
    "run_ground_contact_plausible",
    "turn_natural",
)

COME_PLAY_CHECKS: tuple[tuple[str, str], ...] = (
    ("vlm_come_play_approach", "playful_approach_visible"),
    ("vlm_come_play_gait", "approach_gait_natural"),
    ("vlm_come_play_deceleration", "deceleration_before_bow"),
    ("vlm_come_play_bow", "play_bow_recognized"),
    ("vlm_come_play_front_lowered", "front_body_lowered"),
    ("vlm_come_play_rear_posture", "rear_posture_correct"),
    ("vlm_come_play_hold", "playful_hold_visible"),
    ("vlm_come_play_expression", "attentive_playful_expression"),
    ("vlm_come_play_near_bow_keyframe", "near_bow_matches_keyframe"),
    ("vlm_come_play_limb_count", "limb_count_correct"),
    ("vlm_come_play_anatomy", "joint_anatomy_plausible"),
    ("vlm_come_play_not_down", "posture_not_lying_or_sitting"),
    ("vlm_come_play_return_travel", "return_travel_to_home_visible"),
    ("vlm_come_play_home_scale", "returns_to_home_scale_and_position"),
    ("vlm_come_play_return", "smooth_return_to_stand_ready"),
    ("vlm_come_play_seam", "segment_seam_smooth"),
    ("vlm_come_play_forward_time", "return_played_forward"),
)
COME_PLAY_CHECK_NAMES: tuple[str, ...] = tuple(c for c, _ in COME_PLAY_CHECKS)

#: v2 자문 검사 (QUALITY_ADVISORY): 보행 협응 / 접지 / 자연스러운 회전. 극성은 yes=PASS,
#: no=REVIEW, 그 외 unknown — 움직임의 **질**은 전달을 막지 않고 자문으로 남는다 (요구 8).
#: 해부 파손은 여전히 anatomy_plausible_all_frames / joint_anatomy_plausible 이 하드로 막는다.
COME_PLAY_ADVISORY_CHECKS: tuple[tuple[str, str], ...] = (
    ("vlm_come_play_gait_coordination", "run_gait_coordinated"),
    ("vlm_come_play_ground_contact", "run_ground_contact_plausible"),
    ("vlm_come_play_turn", "turn_natural"),
)
COME_PLAY_ADVISORY_CHECK_NAMES: tuple[str, ...] = tuple(c for c, _ in COME_PLAY_ADVISORY_CHECKS)

#: 결정론 세그먼트 검사 — 이름만 다르고 규칙은 PLAY_WITH_ME 와 같다(motion_video_segments).
#: A 의 끝 = 근접 bow 스틸(접근이 실제로 일어나 커졌다), B 의 끝 = 홈 STAND_READY(홈 스케일 복귀).
COME_PLAY_SEGMENT_CHECK_NAMES: tuple[str, ...] = (
    "segment_a_starts_at_stand_ready",
    "segment_a_reaches_near_bow",
    "segment_b_starts_at_near_bow",
    "segment_b_returns_to_stand_ready",
    "segment_seam_continuity",
)

COME_PLAY_REQUIRED_CHECK_NAMES: tuple[str, ...] = (
    *COME_PLAY_CHECK_NAMES,
    *COME_PLAY_SEGMENT_CHECK_NAMES,
)

#: 희소 샘플링에서 no → unknown 으로 누그러지는 **관찰성** 필드 (짧은 사건: 감속·bow·유지·
#: 표정·근접 bow 일치). 접근/복귀 이동은 세그먼트 대부분을 차지해 희소 격자도 놓치지 않으므로
#: **하드**로 둔다 — "접근이 분명히 없다" 는 희소 샘플링을 핑계로 통과하지 않는다 (요구 11).
#: 보행/해부/홈 스케일/이음매/순방향 재생도 하드다.
COME_PLAY_OBSERVATIONAL_FIELDS: tuple[str, ...] = (
    "deceleration_before_bow",
    "play_bow_recognized",
    "front_body_lowered",
    "rear_posture_correct",
    "playful_hold_visible",
    "attentive_playful_expression",
    "near_bow_matches_keyframe",
)

_COME_PLAY_SEGMENT_NOTE = (
    "The asset is two start/end-conditioned segments joined in time order: segment A "
    "(STAND_READY at home → an instant of playful excitement → an energetic bounding run toward "
    "the viewer → braking over the last strides → the approved NEAR play-bow pose close to the "
    "camera) and segment B (the near play bow → a half-second beat → rise → a smooth arc turn away "
    "→ trot away from the camera to the home spot → turn back to face the camera → STAND_READY at "
    "the original home position and scale). Stationary lead-in and excess bow-hold frames are "
    "trimmed at the join, so the hold is short by design."
)

_COME_PLAY_ALLOWED_SECONDARY = [
    "natural breathing", "blinking", "subtle ear, head or tail response",
    "a bright playful expression, a relaxed or softly open mouth and a lively tail wag",
    "a brief playful anticipation (ear or gaze attention, weight shift, a small bounce) before the approach",
    "the change in apparent size, framing position and silhouette that the approach, the bow and the return "
    "themselves require (the camera stays fixed; the dog moves)",
    "small paw repositioning and weight adjustments needed for realistic braking, the bow and balance",
    "a short turn or a few extra steps while re-orienting toward home or toward the camera",
    "a shallower or deeper bow according to this dog's own leg length, body proportions and flexibility, "
    "as long as the front body is clearly lower than the hindquarters",
    "natural ear, face and tail carriage: the ears need not point forward and the tail need not be raised",
]

_COME_PLAY_TEMPORAL_POLICY = {
    "brief_event": True,
    "absence_requires_reliable_coverage": True,
    "sparse_non_observation": "REVIEW",
    "occlusion_or_perspective_uncertainty": "REVIEW",
    "subtle_or_ambiguous_response": "REVIEW",
}

COME_PLAY_BOW = SpeciesVariant(
    variant_id=VARIANT_COME_PLAY_BOW,
    species=SPECIES_DOG,
    keyframe_role="PLAY_BOW_NEAR",
    pose_label="near play bow",
    segment_phases=("approach_into_bow", "hold_return_home"),
    # A 5 s: 흥분(0.5) + 달려옴(3.0) + 제동·bow 하강(1.0) + 도착(0.5).
    # B 8 s (v2, 6→8): 비트(0.5) + 기립(1.0) + 호 회전(1.5) + 트롯 복귀(3.0) + 홈 회전(1.5) + 정착(0.5).
    # 첫 라이브 런에서 6 s 복귀는 급회전·측면 보행·마지막 0.5 s 축소로 구겨졌다(2026-10-30 감사).
    # fal Kling v3: 정수 3..15 s. 합 13 s = 이어진 자산의 **계획** 길이 (트리밍 전).
    segment_durations_sec=(5, 8),
    description=(
        "From the calm upright STAND_READY pose at its home spot, the dog lights up with playful "
        "excitement and launches into an energetic bounding run straight toward the viewer, "
        "growing larger in the fixed frame, then brakes over the last strides and lowers into a "
        "clear, friendly play bow close to the camera: elbows bend so the chest drops low while the "
        "hindquarters stay at normal standing height, bow depth natural to this dog's build, front "
        "paws grounded. After a short beat with a bright attentive gaze it rises, turns away in a "
        "smooth arc, trots back to its home spot becoming smaller again, turns to face the camera "
        "and settles into the exact original STAND_READY pose, position and scale"
    ),
    # 세그먼트 A: lead_in = 흥분 + 달려옴, into_pose = 제동 + bow 하강 + 도착. 초 단위 타이밍을
    # 프롬프트에 **명시**한다 — v1 의 "burst of excitement" 는 1.2 s 정지로, "arrive as the clip
    # closes" 는 0.8 s 조기 유지로 렌더됐다 (Kling 은 시간 배분을 스스로 정한다).
    # 프롬프트 예산: fal Kling 2,500자, 테스트 여유 100자 → 세그먼트당 ≤ 2,400자.
    lead_in=(
        "0.0-0.5 s: still at home, an instant of excitement: eyes bright, tail wagging fast, a "
        "small eager hop. 0.5-3.5 s: it launches straight at the viewer in a bounding playful run: "
        "front legs reaching forward, hind legs driving under the body, each stride a grounded step "
        "with clear weight transfer, the body rising and dipping with every bound, ears and tail "
        "flying; it accelerates out of the first stride and grows steadily larger in frame"
    ),
    into_pose=(
        "3.5-4.5 s: it brakes over the last two strides: hind legs gather under the body, front "
        "paws plant and slide a little forward, chest and shoulders drop as the elbows bend, "
        "hindquarters at normal standing height. 4.5-5.0 s: it settles into the supplied final "
        "near play-bow frame only as the clip ends, head up, still breathing"
    ),
    # 세그먼트 B: hold = 짧은 비트, return_to_stand = 기립 + 호 회전 + 트롯 복귀 + 홈 회전 + 정착.
    hold=(
        "0.0-0.5 s: begin exactly in the supplied first frame, already alive: one tail wag, a "
        "breath, bright eyes, no longer"
    ),
    return_to_stand=(
        "0.5-1.5 s: it pushes up out of the bow: chest rises, elbows straighten, weight shifts "
        "back over the hind legs. 1.5-3.0 s: in the same flow it turns away, head and shoulders "
        "swinging to one side as it steps around in a smooth arc until its back is to the camera. "
        "3.0-6.0 s: it trots back away from the camera to its home spot, hind legs alternating, "
        "paws landing with clear ground contact, tail up and swaying, growing steadily smaller and "
        "higher in frame. 6.0-7.5 s: arriving home it slows and turns in a short arc to face the "
        "camera. 7.5-8.0 s: it settles square on all four paws into the supplied final STAND_READY "
        "frame"
    ),
    prompt_clauses=(
        "A few bounding strides, not a leap into the lens; the dog never leaves the frame. The "
        "whole front body lowers, not just the head; the rear stays at standing height, hips not "
        "lifted above standing, never a sit or lie-down.",
    ),
    vlm_guidance=(
        " For COME_PLAY on a DOG (COME_PLAY_BOW), separately answer: playful_approach_visible — "
        "the dog clearly travels toward the camera from its home spot, becoming larger in the fixed "
        "frame, with lively playful energy (not a head-only gesture, not an in-place bounce). "
        "approach_gait_natural — the approach is real locomotion with limb motion and ground contact; "
        "answer no ONLY for a slide, float, zoom or teleport with no real limb motion — an imperfect "
        "but real gait is yes, and its quality belongs in run_gait_coordinated. deceleration_before_bow — it visibly "
        "slows and settles before lowering (an abrupt stop that still shows braking is fine; a "
        "teleport or jump-cut into the bow is not). play_bow_recognized / front_body_lowered — the "
        "front body visibly lowers with the elbows sinking toward the ground and the chest dropping. "
        "rear_posture_correct — the hindquarters stay up near normal standing height on standing "
        "hind legs rather than sinking into a sit or lie; hips lifted higher than standing or an "
        "arched spine are NOT required and NOT a reason to answer no; accept a shallower bow from a "
        "short-legged or stocky dog as long as the front body is clearly lower than the rear. "
        "playful_hold_visible / attentive_playful_expression — the bow is held briefly with a bright "
        "playful face. near_bow_matches_keyframe — the mid-clip bow matches the supplied INTERMEDIATE "
        "near play-bow keyframe (same dog, same camera, larger and lower in frame than at home). "
        "limb_count_correct / joint_anatomy_plausible — four limbs with plausible joints throughout "
        "the run, the bow and the return. posture_not_lying_or_sitting — the dog never substitutes "
        "a sit or lie-down. return_travel_to_home_visible — after the bow the dog rises and travels "
        "back away from the camera, becoming smaller, until it is at its home spot. "
        "returns_to_home_scale_and_position — the final frames show the dog at about the same size, "
        "position and framing as the opening frames, facing the camera; a small placement or size "
        "difference is fine, a dog that is still clearly near the camera or off to one side is not. "
        "smooth_return_to_stand_ready — it settles into a grounded STAND_READY-compatible ending. "
        "segment_seam_smooth — no visible jump, freeze, duplicated pose or stall where the clip "
        "passes through the held bow. return_played_forward — the return is new forward-time motion "
        "(the dog turns and walks away or backs off naturally), never the approach played in "
        "reverse. The growing and shrinking of the dog, its changing position in the frame and the "
        "lower silhouette of the bow are the requested action, NOT camera motion, zoom or a defect. "
        "Ears that do not point forward or a tail that is not raised are NOT defects. A head-only "
        "dip without the chest and elbows lowering is NOT a play bow. A clearly sampled no is a "
        "defect, but occlusion, front-view foreshortening or insufficient temporal coverage is unknown. "
        "GAIT WINDOWS (advisory quality fields, judged ONLY on the consecutive frames listed under "
        "gait_windows in the temporal evidence provenance, about a sixth of a second apart): "
        "run_gait_coordinated — across the approach window the legs move in a plausible running or "
        "trotting pattern (front and hind legs alternating or bounding together, strides progressing), "
        "not frozen legs under a sliding body or limbs cycling randomly. run_ground_contact_plausible — "
        "paws visibly land and push off the ground with believable weight transfer; no hovering or "
        "skating. turn_natural — across the return window the turn is a stepped body rotation (the dog "
        "steps around, hindquarters and legs following), not a snap rotation, a morph or a mirrored "
        "flip. These three are quality judgements: answer no only for a clearly visible problem in the "
        "window, unknown when the window is blurred, foreshortened or ambiguous; they never replace the "
        "anatomy fields."
    ),
    acceptance={
        "description": (
            "From STAND_READY at its home spot the dog shows playful excitement, trots a few lively "
            "steps toward the viewer with a natural grounded gait (growing larger in the fixed frame), "
            "decelerates smoothly and performs a recognizable play bow close to the camera matching the "
            "approved PLAY_BOW_NEAR keyframe: chest and front body lowered with the elbows sinking "
            "toward the ground, hindquarters near normal standing height (not lifted or arched), bow "
            "depth natural to this dog's build, forelegs supporting. It holds the inviting bow briefly, "
            "rises, travels back (forward in time, never reversed) to its home spot, turns to face the "
            "camera and settles into the same grounded STAND_READY pose, position and scale with intact "
            "four-limb anatomy. " + _COME_PLAY_SEGMENT_NOTE
        ),
        "expected_start_state": (
            "STAND_READY at HOME: calm upright stance with all four paws naturally grounded, at the "
            "home position and scale."
        ),
        "expected_intermediate_state": (
            "PLAY_BOW_NEAR: the dog a few steps closer to the camera (clearly larger and lower in the "
            "frame, fully visible), shoulders and chest lowered toward the ground, front paws slightly "
            "forward and grounded, hindquarters at about normal standing height on supported hind legs, "
            "attentive playful expression."
        ),
        "expected_end_state": (
            "STAND_READY at HOME: the dog is back at its original home position and scale, upright on "
            "all four paws, facing the camera, and the pose joins BREATHING smoothly without a visible jump."
        ),
        "blocking_criteria": [
            "Reliable evidence clearly shows no approach toward the camera (the dog stays at its home "
            "spot and merely bows, bounces or gestures in place), or no play bow (a head-only dip, a "
            "sit, a lie-down, or sustained jumping that never becomes a bow replaces it).",
            "The approach or the return is a zoom, slide or float with no real limb motion, or the dog "
            "teleports or jump-cuts between positions.",
            "The hindquarters clearly sink to a sit or lie-down instead of staying up near standing height.",
            "A limb is duplicated, missing, merged, detached or anatomically implausible.",
            "The pet visibly falls, floats, collapses, or loses plausible support and balance.",
            "A human, hand, arm, person, leash, toy, platform or other added object appears.",
            "The camera visibly moves, zooms or reframes (the dog's own change of size and position does not count).",
            "The return is the approach played backward (reversed playback), or the dog does not travel "
            "back and ends clearly near the camera or clearly away from its home position and scale.",
            "A visible jump, freeze, duplicated pose, prolonged motionless hold or reversed playback appears where the two segments join.",
            "The pet does not return to a grounded STAND_READY-compatible ending when the endpoint is clearly observable.",
        ],
        "advisory_criteria": [
            "The approach, deceleration, bow, brief hold or playful expression is plausible but occluded, "
            "too subtle, foreshortened by the front view, or insufficiently sampled to confirm.",
            "Imperfect gait, unidentifiable footfalls, a short re-orientation, minor speed changes or brief "
            "partial paw cropping during the approach or the return.",
            "Uncoordinated-looking strides, soft ground contact or a slightly abrupt turn that are still "
            "real locomotion (run_gait_coordinated / run_ground_contact_plausible / turn_natural = no).",
            "The hindquarters look lifted higher than standing or the spine arched — an exaggerated bow "
            "that is still a recognizable, supported play bow.",
            "A small final placement or size difference, a brief settling pause at the join, or a minor "
            "final weight shift that does not break balance or the BREATHING seam.",
        ],
        "allowed_secondary_motion": list(_COME_PLAY_ALLOWED_SECONDARY),
        "temporal_evidence_policy": dict(_COME_PLAY_TEMPORAL_POLICY),
    },
)

#: motion_id → species → variant. 새 종 인식 액션은 여기에 표만 더한다.
SPECIES_VARIANTS: dict[str, dict[str, SpeciesVariant]] = {
    PLAY_WITH_ME: {
        SPECIES_DOG: PLAY_BOW,
        SPECIES_CAT: PLAY_CROUCH,
    },
    # COME_PLAY V1 은 DOG 만 — CAT/기타는 과금 전 게이트 + 계약 해석에서 SPECIES_UNSUPPORTED.
    COME_PLAY: {
        SPECIES_DOG: COME_PLAY_BOW,
    },
}

#: 모션별 QA 프로필 — 세그먼트 모션의 전용 VLM 필드/검사/관찰성/결정론 세그먼트 검사/도메인.
#: motion_video_qa · vlm_identity · motion_video_service · motion_spec 이 motion id 로 조회한다.
#: 새 세그먼트 모션은 여기에 한 항목을 더한다.
MOTION_QA_PROFILES: dict[str, dict[str, Any]] = {
    PLAY_WITH_ME: {
        "policy_revision": PLAY_WITH_ME_POLICY_REVISION,
        "qa_field_names": PLAY_WITH_ME_QA_FIELD_NAMES,
        "checks": PLAY_WITH_ME_CHECKS,
        "check_names": PLAY_WITH_ME_CHECK_NAMES,
        "segment_check_names": PLAY_WITH_ME_SEGMENT_CHECK_NAMES,
        "required_check_names": PLAY_WITH_ME_REQUIRED_CHECK_NAMES,
        "observational_fields": PLAY_WITH_ME_OBSERVATIONAL_FIELDS,
        "qa_domain": "play_with_me",
    },
    COME_PLAY: {
        "policy_revision": COME_PLAY_POLICY_REVISION,
        "qa_field_names": COME_PLAY_QA_FIELD_NAMES,
        "checks": COME_PLAY_CHECKS,
        "check_names": COME_PLAY_CHECK_NAMES,
        "segment_check_names": COME_PLAY_SEGMENT_CHECK_NAMES,
        "required_check_names": COME_PLAY_REQUIRED_CHECK_NAMES,
        "advisory_checks": COME_PLAY_ADVISORY_CHECKS,
        "advisory_check_names": COME_PLAY_ADVISORY_CHECK_NAMES,
        "observational_fields": COME_PLAY_OBSERVATIONAL_FIELDS,
        "qa_domain": "come_play",
    },
}


def qa_profile(motion_id: Optional[str]) -> Optional[dict[str, Any]]:
    """세그먼트/종 인식 모션의 QA 프로필. 그 외 모션은 None."""
    return MOTION_QA_PROFILES.get(_normalize(motion_id))


def segment_check_names(motion_id: Optional[str]) -> tuple[str, ...]:
    """결정론 세그먼트 검사 이름 — 모르는 모션은 PLAY_WITH_ME 이름(기존 기본값)."""
    profile = qa_profile(motion_id)
    return tuple(profile["segment_check_names"]) if profile else PLAY_WITH_ME_SEGMENT_CHECK_NAMES


def all_segment_check_names() -> frozenset[str]:
    return frozenset(n for p in MOTION_QA_PROFILES.values() for n in p["segment_check_names"])


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def requires_species_variant(motion_id: str) -> bool:
    return _normalize(motion_id) in SPECIES_VARIANTS


def supported_species(motion_id: str) -> tuple[str, ...]:
    return tuple(SPECIES_VARIANTS.get(_normalize(motion_id), {}).keys())


def variants_for(motion_id: str) -> tuple[SpeciesVariant, ...]:
    return tuple(SPECIES_VARIANTS.get(_normalize(motion_id), {}).values())


def intermediate_keyframe_roles(motion_id: str) -> tuple[str, ...]:
    """이 모션의 종별 중간 키프레임 역할 전부 (어느 종인지 모를 때 핀 검사용)."""
    return tuple(v.keyframe_role for v in variants_for(motion_id) if v.keyframe_role)


def variant_for_keyframe_role(role: str) -> Optional[SpeciesVariant]:
    wanted = _normalize(role)
    for table in SPECIES_VARIANTS.values():
        for variant in table.values():
            if variant.keyframe_role == wanted:
                return variant
    return None


def select_variant(motion_id: str, species: Optional[str]) -> Optional[SpeciesVariant]:
    """지원 종이면 변형, 아니면 None. 모션이 종 변형을 쓰지 않으면 None."""
    return SPECIES_VARIANTS.get(_normalize(motion_id), {}).get(_normalize(species))


def require_variant(motion_id: str, species: Optional[str]) -> SpeciesVariant:
    """fail-closed 선택. 지원하지 않는 종/UNKNOWN 은 SPECIES_UNSUPPORTED."""
    mid = _normalize(motion_id)
    variant = select_variant(mid, species)
    if variant is None:
        raise SpeciesVariantError(
            "SPECIES_UNSUPPORTED",
            f"{mid} 는 {', '.join(supported_species(mid))} 에서만 생성됩니다 "
            f"(이 펫의 종: {_normalize(species) or SPECIES_UNKNOWN}).",
            status=409,
        )
    return variant


def variant_payload(variant: SpeciesVariant) -> dict[str, Any]:
    """계약(contract)에 박제되는 형태 — 재개/재QA 가 같은 변형을 읽는다."""
    return {
        "id": variant.variant_id,
        "species": variant.species,
        "description": variant.description,
        "prompt_clauses": list(variant.prompt_clauses),
        "vlm_guidance": variant.vlm_guidance,
        "keyframe_role": variant.keyframe_role,
        "pose_label": variant.pose_label,
        "lead_in": variant.lead_in,
        "into_pose": variant.into_pose,
        "hold": variant.hold,
        "return_to_stand": variant.return_to_stand,
        "segment_phases": list(variant.segment_phases),
        "segment_durations_sec": (
            list(variant.segment_durations_sec) if variant.segment_durations_sec else None
        ),
    }


def variant_from_contract(contract: Optional[dict[str, Any]]) -> Optional[SpeciesVariant]:
    """계약에 박제된 변형 id → 현행 변형 정의. 모르는 id 는 None."""
    payload = (contract or {}).get("species_variant") or {}
    motion_id = _normalize((contract or {}).get("motion_id"))
    wanted = _normalize(payload.get("id"))
    for variant in variants_for(motion_id):
        if variant.variant_id == wanted:
            return variant
    return None


async def resolve_pet_species(user_id: str, pet_id: str) -> str:
    """
    과금 **전** 게이트용 종 해석. 생성 계약과 같은 측정 소스(형태 프로필 → 신원
    프로필)를 같은 함수(derive_motion_profile)로 읽는다 — 다만 키프레임 계보에 핀된
    형태 버전이 아니라 **최신** 프로필을 본다(계보는 키프레임이 정해져야 알 수 있다).
    계약 단계가 핀된 버전으로 다시 fail-closed 판정하므로 여기서의 차이는 "조금 더
    일찍 거절"일 뿐 "잘못 허용"이 아니다. 조회 실패는 UNKNOWN(= 거절)이다.
    """
    from . import motion_reference_service, pet_identity_service, pet_morphology_service

    identity = None
    morphology = None
    try:
        identity = await pet_identity_service.get_profile(user_id=user_id, pet_id=pet_id)
    except pet_identity_service.PetIdentityError:
        identity = None
    try:
        morphology = await pet_morphology_service.get_profile(user_id=user_id, pet_id=pet_id)
    except pet_morphology_service.PetMorphologyError:
        morphology = None
    profile = motion_reference_service.derive_motion_profile(
        identity, morphology_profile=morphology, confidence_floor="medium"
    )
    return _normalize(profile.get("species")) or SPECIES_UNKNOWN


# ══════════════════════════════════════════════════════════════════════════
# 세그먼트 후처리 / 프로파일 정책 (모션 스코프) — COME_PLAY 만 값을 돌려준다
# ══════════════════════════════════════════════════════════════════════════


def segment_trim_policy(motion_id: Optional[str]):
    """이음매 트리밍 정책 (motion_profile.TrimPolicy) — COME_PLAY 만. PLAY_WITH_ME 는 None (v1 concat)."""
    if _normalize(motion_id) != COME_PLAY:
        return None
    from . import motion_profile

    return motion_profile.come_play_trim_policy()


def segment_join_version(motion_id: Optional[str]) -> str:
    """모션별 이음매 규칙 버전 — COME_PLAY 는 트리밍 v2, 나머지는 v1 그대로."""
    from . import motion_video_segments

    if _normalize(motion_id) == COME_PLAY:
        return motion_video_segments.COME_PLAY_SEGMENT_JOIN_VERSION
    return motion_video_segments.SEGMENT_JOIN_VERSION


def has_motion_profile(motion_id: Optional[str]) -> bool:
    """이어진 자산에 결정론 모션 프로파일 진단을 붙이는 모션 — COME_PLAY 만."""
    return _normalize(motion_id) == COME_PLAY


def advisory_checks(motion_id: Optional[str]) -> tuple[tuple[str, str], ...]:
    profile = qa_profile(motion_id)
    return tuple(profile.get("advisory_checks") or ()) if profile else ()
