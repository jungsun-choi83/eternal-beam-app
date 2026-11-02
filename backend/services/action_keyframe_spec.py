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
# v3 (2026-09-17): preferred_canonical_source 가 "raw" → "clean_plate". 정본
# raw 에는 모델이 그린 접지/투영 그림자가 들어 있고, 그걸 앵커로 먹이면
# 키프레임이 같은 얼룩을 물려받는다 (clean_plate_service 참고).
# v4 (2026-10-05): STAND_READY 가 **권위 있는 HOME 키프레임**이 된다. 홈
# (BREATHING)과 서 있는 홈 아이들 계열 전부가 NEUTRAL_IDLE 대신 STAND_READY
# 에서 시작한다 — 정본이 앉아/누워 있어도 홈은 항상 네 발로 선 차분한 자세다.
# STAND_READY 서술을 "alert / ready to move" 에서 차분한 중립 홈 자세로 완화,
# supported_action_ids 를 NEUTRAL_IDLE → STAND_READY 로 이관. 버전을 올리는
# 이유는 v2 와 같다: 재사용 게이트가 이 값을 비교한다.
KEYFRAME_SPEC_VERSION = "keyframe-spec-v4"
# v2 (2026-09-17): 그림자 금지 절 추가 — canonical-prompt-v2 와 같은 이유.
KEYFRAME_PROMPT_VERSION = "keyframe-prompt-v2"

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
    #: 신원 앵커로 쓸 정본 소스: "clean_plate" (누끼 + 고정 중립 배경).
    #: raw 는 증거로만 남는다 — 그림자/배경이 하류로 새지 않도록 (spec-v3).
    preferred_canonical_source: str
    #: Phase 6 영상 생성 호환 메타.
    video_compat: dict[str, Any]
    #: 이 키프레임을 시작 포즈로 쓸 수 있는 **기존** 액션 id 들.
    supported_action_ids: tuple[str, ...] = field(default_factory=tuple)
    #: 컴팩트 프롬프트(≤1000자) 전용 포즈 절. 요구 포즈는 required_pose 와 같고
    #: 문장만 짧다 — 신원/형태 특성 줄이 길이 상한 때문에 잘리지 않게 한다.
    #: None 이면 컴팩트 프롬프트도 required_pose 를 그대로 쓴다.
    compact_pose: Optional[str] = None
    #: 설정되면 생성 입력(첫 이미지)이 정본 플레이트가 아니라 **같은 정본 계보의
    #: 승인된 이 역할 키프레임 플레이트**다 (PLAY_BOW/PLAY_CROUCH → STAND_READY).
    #: 중간 포즈가 영상 세그먼트의 양끝 조건으로 쓰이려면 STAND_READY 와 같은
    #: 카메라 거리/프레이밍/배경이어야 하기 때문이다. 정본은 그대로 1차 신원
    #: 레퍼런스(QA 앵커)로 남는다. None 이면 기존 동작(정본 플레이트 입력).
    anchor_keyframe_role: Optional[str] = None
    #: 앵커 머리말의 프레이밍 문장 덮어쓰기 (풀 / 컴팩트). None 이면 공유 기본 문장
    #: ("같은 위치·크기·카메라 거리·프레이밍 … 포즈만 바꾼다")을 그대로 쓴다 —
    #: 기존 앵커 역할(PLAY_CROUCH)의 프롬프트는 바이트 단위로 불변이다. PLAY_BOW 는
    #: 몸·사지가 자연스럽게 재배치되어야 해서(앞발 전진, 가슴 하강) 서 있는
    #: 실루엣/발 위치 고정 요구를 풀고 카메라·프레이밍·전체 스케일·지면선만 잠근다.
    anchor_framing_clause: Optional[str] = None
    compact_anchor_framing_clause: Optional[str] = None


#: 시작 포즈가 LIE 인 액션 — NEUTRAL_IDLE 이 아니라 LIE 역할이 담당한다.
_LIE_START_ACTIONS: tuple[str, ...] = ("LIE_IDLE", "STAND_UP")

#: STAND_READY = **HOME 키프레임** (spec-v4). LIE 에서 시작하지 않는 모든 액션이
#: 이 하나의 서기 스틸을 공유한다: 홈(BREATHING), 홈 아이들 계열, 터치/보이스
#: 반응, 이동(COME_CLOSER), 기립→눕기 전이(LIE_DOWN). 모션마다 서기 키프레임을
#: 따로 만들지 않는다 — 같은 포즈에서 시작해야 아이들 사이에 자세 점프가 없다.
#: (RUN/WALK 는 motion_spec 에만 있는 미래 모션 id 라 여기 액션 레지스트리
#: 매핑에는 등장하지 않는다.)
_STAND_READY_ACTIONS: tuple[str, ...] = (
    tuple(ACTION_ORDER)          # IDLE, TOUCH(=PET_HEAD_START), VOICE, NFC
    # PET_ACTIONS 중 LIE 시작이 아닌 전부 — COME_CLOSER, PET_HEAD, LOOK_UP, LIE_DOWN.
    + tuple(a for a in PET_ACTIONS if a not in _LIE_START_ACTIONS)
    + (BREATHING_HOME_STATE,)    # 웹 홈 상태
    + tuple(IDLE_EVENTS)         # BLINKING, EAR_TWITCHING, HEAD_TILTING, TAIL_WAGGING
    + tuple(IDLE_TEMPLATE_ORDER) # IDLE_BREATH … IDLE_LOOK_AROUND
)

#: NEUTRAL_IDLE 은 spec-v4 부터 런타임 액션의 시작 포즈가 아니다 — 정본 자세를
#: 물려받는 역할이라 홈으로 쓰면 나쁜 정본 자세가 그대로 홈이 된다. 역할 자체는
#: 남는다 (기존 키프레임 계보 + 미래의 앉은 자세 전이용).
_NEUTRAL_ACTIONS: tuple[str, ...] = ()

KEYFRAME_ROLES: dict[str, KeyframeRole] = {
    "NEUTRAL_IDLE": KeyframeRole(
        role="NEUTRAL_IDLE",
        # 정본 자세를 그대로 물려받는 포즈다 — 포즈를 **새로 고르는 단계가
        # 아니다** (spec-v2). spec-v4 부터 홈은 STAND_READY 가 담당한다.
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
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": True, "motion_class": "idle"},
        supported_action_ids=_NEUTRAL_ACTIONS,
    ),
    "STAND_READY": KeyframeRole(
        role="STAND_READY",
        required_pose=(
            "a calm neutral standing home pose: standing naturally on all four legs, "
            "all paws planted, with weight naturally balanced for this pet's own body "
            "structure. The whole body is oriented toward the camera in a front or "
            "slight front three-quarter view, with the chest and both front legs clearly "
            "visible. Do not keep the torso in side profile with only the head turned. "
            "Preserve the pet's exact natural morphology and proportions; do not change "
            "leg length, body length, chest shape, stance width, head proportions, tail "
            "or ear structure, or force a stance unnatural for this pet. Head level and "
            "naturally aligned with the body, relaxed mouth, ears and tail, full body visible."
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="medium",
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": True, "motion_class": "home"},
        supported_action_ids=_STAND_READY_ACTIONS,
        # 포즈 요구는 required_pose 와 동일하다 — 배경/그림자 금지는 컴팩트
        # 베이스가 이미 말하므로 반복하지 않는다. 장황한 포즈 문장보다 펫의
        # 신원 특성 줄(코트 색/종/귀)이 우선이다.
        compact_pose=(
            "calm neutral standing home pose: on all four legs, all paws planted, "
            "balanced and level, no crouch, lean or mid-step, whole body facing "
            "front or slight front 3/4, chest and both front legs visible, not a "
            "side-profile torso with only the head turned, head level, relaxed "
            "mouth, ears and tail, natural morphology, full body visible"
        ),
    ),
    "LIE": KeyframeRole(
        role="LIE",
        required_pose=(
            "lying down naturally on its belly, front legs extended forward, head "
            "upright and awake, relaxed and comfortable"
        ),
        required_visibility=("face", "full_body", "front_paws"),
        body_motion_complexity="micro",
        preferred_canonical_source="clean_plate",
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
        preferred_canonical_source="clean_plate",
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
        preferred_canonical_source="clean_plate",
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
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": False, "motion_class": "gesture"},
        supported_action_ids=(),
    ),
    # ── PLAY_WITH_ME 종별 중간 포즈 (2026-10-29) ──────────────────────────────
    # 액션 매핑은 비어 있다: PLAY_WITH_ME 의 **시작/끝** 포즈는 STAND_READY 이고
    # (role_for_action 불변), 이 역할들은 생성 계약이 종으로 고르는 **중간** 포즈다
    # (species_motion_variants.SpeciesVariant.keyframe_role). STAND_READY 플레이트를
    # 앵커로 생성해 같은 카메라/프레이밍/배경을 물려받는다. 스펙 버전은 올리지
    # 않는다 — 역할 추가는 기존 역할 행의 재사용 게이트(역할 단위)를 건드리지 않는다.
    "PLAY_BOW": KeyframeRole(
        role="PLAY_BOW",
        # 2026-10-30 고객 피드백: bow 는 자연스러운데 엉덩이가 과하게 들렸다. 뒷몸은
        # **평소 서 있는 높이 그대로**(들어 올리지도, 척추를 휘지도 않는다), bow 의 깊이는
        # 이 개체의 다리 길이·비율·유연성에 맞춘다. CAT/STAND_READY/전역 버전은 불변.
        required_pose=(
            "a recognizable full-body canine play bow held mid-invitation: the front paws "
            "extend slightly forward and the front legs reach into a comfortable bow as the "
            "shoulders and chest lower naturally toward the ground with realistic shoulder "
            "and elbow anatomy, while the hindquarters stay at about their normal standing "
            "height on naturally supported hind legs — hips not lifted above standing height, "
            "spine not arched. Bow depth follows this dog's own anatomy, leg length, body "
            "proportions and natural flexibility; a shallower bow is correct when the build "
            "allows no more, as long as the front body is clearly lower than the rear. All "
            "four paws rest on the ground with realistic weight distribution and natural "
            "ground contact; body height and silhouette change as the bow requires. The head "
            "is up toward the camera with a bright attentive gaze and a relaxed or softly open "
            "mouth. The ears keep this pet's own shape, size, fold and anatomy, with a natural "
            "attentive adjustment. Keep this pet's exact face, muzzle, eyes, coat colors, "
            "markings, fur texture, body structure and proportions, tail shape and distinctive "
            "features. Not an exaggerated stretch, head-only dip, sitting pose, lying pose or "
            "sprawl"
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="medium",
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": False, "motion_class": "play_pose",
                      "intermediate_for": ["PLAY_WITH_ME"], "species": "DOG"},
        supported_action_ids=(),
        # 컴팩트(≤1000자, Runway): 공유 베이스(~660자) + 가시성 줄 + 신원 특성 줄
        # (최악 3색 + 긴 귀 모양 ≈ 130자)이 전부 실리려면 포즈 절은 ~250자여야 한다.
        # 컴팩트 예산: 공유 베이스(~660자) + 가시성 줄 + 신원 특성 줄(최악 ≈ 130자) 뒤에
        # 포즈 절이 ~245자 이내여야 특성 줄이 하나도 잘리지 않는다 (테스트가 강제).
        compact_pose=(
            "play bow: front paws a bit forward, shoulders and chest lowered, rear at standing "
            "height, supported hind legs, not lifted/arched, depth suited to this dog, all "
            "four paws grounded, head up; ears keep own shape/fold; no head dip, sit, "
            "lie-down, sprawl"
        ),
        anchor_keyframe_role="STAND_READY",
        anchor_framing_clause=(
            "Keep the same camera, camera distance, framing, overall subject scale, ground "
            "line, background tone and lighting as the first image; the body, limbs and "
            "silhouette may reposition naturally into the requested pose and need not keep "
            "the standing outline or the exact paw positions."
        ),
        compact_anchor_framing_clause=(
            "keep the camera, framing, scale and ground line; body and limbs may move "
            "naturally."
        ),
    ),
    "PLAY_CROUCH": KeyframeRole(
        role="PLAY_CROUCH",
        required_pose=(
            "an alert, ready-to-pounce cat play crouch held mid-invitation: all four legs "
            "are bent, the chest is close to the ground and the hindquarters are gathered low "
            "and compact with the hind legs folded beneath the body, not sprawled flat; all "
            "four paws are planted on the ground. The head is up and facing the camera with "
            "a bright, focused playful gaze and ears forward. The body stays at exactly the "
            "same position, scale, camera distance and framing as the first supplied standing "
            "image; only the pose changes. Not lying flat on the side or belly, not an "
            "upright sit"
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="medium",
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": False, "motion_class": "play_pose",
                      "intermediate_for": ["PLAY_WITH_ME"], "species": "CAT"},
        supported_action_ids=(),
        compact_pose=(
            "alert cat play crouch held mid-invitation: all four legs bent, chest close to the "
            "ground, hindquarters low and compact with hind legs folded beneath the body, all "
            "four paws planted, head up facing the camera, bright focused gaze, ears forward; "
            "not lying flat, not an upright sit"
        ),
        anchor_keyframe_role="STAND_READY",
    ),
    # ── COME_PLAY 근접 play bow (2026-10-30, DOG 전용) ────────────────────────
    # COME_PLAY 는 홈에서 카메라 쪽으로 트롯해 와서 bow 하므로, 세그먼트 경계의 bow 는 홈
    # 스케일(PLAY_BOW)이 아니라 **카메라에 가까운** 스케일이어야 한다. 승인된 PLAY_BOW
    # 플레이트를 앵커로(같은 개체·같은 bow 형태·같은 카메라) 몇 걸음 다가온 위치의 같은
    # 포즈를 만든다. 앵커 체인: STAND_READY → PLAY_BOW → PLAY_BOW_NEAR (anchor_chain).
    # 액션 매핑은 비어 있다(시작/끝은 STAND_READY). 스펙/프롬프트 버전은 올리지 않는다.
    "PLAY_BOW_NEAR": KeyframeRole(
        role="PLAY_BOW_NEAR",
        required_pose=(
            "the same recognizable canine play bow as in the first supplied image, held "
            "mid-invitation after the dog has trotted a few steps closer to the camera: the dog "
            "appears clearly larger in the frame, roughly one and a half times its standing size, "
            "and lower in the frame, centered and facing the camera, still fully visible with all "
            "four paws and the tail inside the frame and never cropped. The front paws extend "
            "slightly forward as the shoulders and chest lower toward the ground with realistic "
            "shoulder and elbow anatomy, while the hindquarters stay at about their normal standing "
            "height on supported hind legs — hips not lifted above standing height, spine not "
            "arched; bow depth exactly as in the first image, natural to this dog's build. All four "
            "paws rest on the ground with realistic weight. The head is up toward the camera with a "
            "bright attentive gaze and a relaxed or softly open mouth; the ears keep this pet's own "
            "shape, size and fold. Keep this pet's exact face, muzzle, eyes, coat colors, markings, "
            "fur texture, body structure and proportions, tail shape and distinctive features. Not "
            "an exaggerated stretch, head-only dip, sitting pose, lying pose or sprawl"
        ),
        required_visibility=("face", "full_body", "ears", "front_paws"),
        body_motion_complexity="medium",
        preferred_canonical_source="clean_plate",
        video_compat={"loopable_base": False, "motion_class": "play_pose",
                      "intermediate_for": ["COME_PLAY"], "species": "DOG",
                      "camera_distance": "near"},
        supported_action_ids=(),
        # 컴팩트(≤1000자, Runway): 포즈 절은 ~250자 — 신원 특성 줄이 잘리지 않게.
        compact_pose=(
            "same play bow as the first image, a few steps nearer the camera: about 1.5x larger, "
            "lower in frame, centered, fully visible incl. all four paws; front paws a bit forward, "
            "chest lowered, rear at standing height, head up; no sit, lie-down, sprawl"
        ),
        anchor_keyframe_role="PLAY_BOW",
        anchor_framing_clause=(
            "Keep the same camera, lens, camera height, background tone and lighting as the first "
            "image and keep the same play-bow posture; the dog has moved a few steps toward the "
            "camera, so it appears about one and a half times larger and lower in the frame, "
            "centered, with the whole body including all four paws and the tail inside the frame."
        ),
        compact_anchor_framing_clause=(
            "same camera and play-bow posture; the dog is a few steps closer, about 1.5x larger "
            "and lower in frame, fully visible."
        ),
    ),
}

#: 결정론적 순서 (벤치마크 순서). 앞의 4개가 기계적으로 쓰이는 포즈 역할이다
#: (Phase 4) — LOOK_UP/HAPPY 는 매핑 없는 미래 벤치마크로 남는다. PLAY_BOW /
#: PLAY_CROUCH 는 PLAY_WITH_ME 의 종별 중간 포즈다 (뒤에 붙는다 — 앞 순서 불변).
#: PLAY_BOW_NEAR 는 COME_PLAY 의 근접 bow (PLAY_BOW 앵커) — 맨 뒤에 붙는다.
KEYFRAME_ROLE_ORDER: tuple[str, ...] = (
    "NEUTRAL_IDLE", "STAND_READY", "LIE", "SLEEP", "LOOK_UP", "HAPPY",
    "PLAY_BOW", "PLAY_CROUCH",
    "PLAY_BOW_NEAR",
)


def get_role(role: str) -> Optional[KeyframeRole]:
    return KEYFRAME_ROLES.get((role or "").strip().upper())


def anchor_chain(role: str) -> tuple[str, ...]:
    """역할의 **생성 순서**: 가장 바깥 앵커부터 역할 자신까지.

    예) PLAY_BOW → ("STAND_READY", "PLAY_BOW"); PLAY_BOW_NEAR → ("STAND_READY",
    "PLAY_BOW", "PLAY_BOW_NEAR"); 앵커 없는 역할 → (role,). 앵커 포즈는 자기 앵커가
    승인돼 있어야 만들 수 있으므로 실행(run)은 이 순서대로 핀하며 빌드한다. 순환은
    스펙 오류다 (임포트 시 _assert 가 잡는다)."""
    chain: list[str] = []
    current: Optional[str] = (role or "").strip().upper()
    while current:
        if current in chain or len(chain) > len(KEYFRAME_ROLES):
            raise ValueError(f"anchor cycle at {current}")
        chain.append(current)
        spec = KEYFRAME_ROLES.get(current)
        current = getattr(spec, "anchor_keyframe_role", None) if spec else None
    return tuple(reversed(chain))


for _role, _spec in KEYFRAME_ROLES.items():
    if _spec.anchor_keyframe_role:
        assert _spec.anchor_keyframe_role in KEYFRAME_ROLES, (_role, _spec.anchor_keyframe_role)
        anchor_chain(_role)  # 순환 없음


def role_for_action(action_id: str) -> Optional[str]:
    """기존 액션 id → 그 액션이 시작 포즈로 쓰는 키프레임 역할."""
    aid = (action_id or "").strip()
    for role in KEYFRAME_ROLE_ORDER:
        if aid in KEYFRAME_ROLES[role].supported_action_ids:
            return role
    return None


def role_spec_snapshot(spec: KeyframeRole) -> dict[str, Any]:
    """빌드 시점 스냅샷 — DB 에 박제되는 형태."""
    snapshot = {
        "spec_version": KEYFRAME_SPEC_VERSION,
        "role": spec.role,
        "required_pose": spec.required_pose,
        "required_visibility": list(spec.required_visibility),
        "body_motion_complexity": spec.body_motion_complexity,
        "preferred_canonical_source": spec.preferred_canonical_source,
        "video_compat": dict(spec.video_compat),
        "supported_action_ids": list(spec.supported_action_ids),
    }
    if spec.anchor_keyframe_role:
        # 앵커 역할이 있는 포즈만 기록한다 — 기존 역할의 스냅샷은 바이트 단위로 같다.
        snapshot["anchor_keyframe_role"] = spec.anchor_keyframe_role
    if spec.anchor_framing_clause:
        snapshot["anchor_framing_clause"] = spec.anchor_framing_clause
    return snapshot


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 프롬프트 (버전드)
# ══════════════════════════════════════════════════════════════════════════

# 두 베이스 모두 배경 톤 자리({background_tone})가 있는 템플릿이다 — 빌더가
# 이 계보의 정본이 고른 톤(pet_background)으로 채운다.
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
    "Plain solid neutral {background_tone} background: a single flat tone with no gradient, "
    "no floor plane and no horizon line. Even neutral lighting. "
    "No contact shadow under the pet, no cast shadow on the background, no reflection "
    "and no darkening around the paws, even where the pose puts weight on the ground. "
    "No beds, "
    "furniture, toys, scenery or any environmental objects. No accessories unless "
    "clearly part of the pet's identity in the supplied images. No additional "
    "animals. No human. No text. No stylization."
)


#: 앵커 키프레임 역할(anchor_keyframe_role) 전용 머리말 — 첫 이미지가 정본이 아니라
#: 승인된 STAND_READY 스틸이라는 사실과 프레이밍 고정 요구를 명시한다. 기존 역할의
#: 프롬프트(_PROMPT_BASE 그대로)는 바이트 단위로 불변이다.
_ANCHORED_PROMPT_HEAD = (
    "The first supplied image is the pet's approved {anchor_role} still (the same pet, "
    "the same camera, the same framing and background); the second supplied image is the "
    "canonical identity reference of the same pet, and any further images are real photos "
    "of it. Create a photorealistic still image of the EXACT SAME pet — this is a pose "
    "change from the first image, not a new interpretation of the animal. {framing}\n"
)
#: 공유 기본 프레이밍 문장 — anchor_framing_clause 가 없는 앵커 역할이 쓴다.
_ANCHORED_FRAMING_DEFAULT = (
    "Keep the pet at the same position, scale, camera distance, framing, background tone "
    "and lighting as the first image; change only the pose."
)
_COMPACT_ANCHORED_FRAMING_DEFAULT = (
    "change only the pose from the first image; keep its position, scale, camera "
    "distance, framing and background."
)


def _prompt_base_for(spec: KeyframeRole) -> str:
    anchor = getattr(spec, "anchor_keyframe_role", None)
    if not anchor:
        return _PROMPT_BASE
    # 첫 문장("The first supplied image is the canonical reference …")을 앵커 머리말로
    # 바꾼다. 나머지(보존/변경/배경/금지 절)는 그대로다.
    head_end = _PROMPT_BASE.index("Preserve exactly:")
    framing = spec.anchor_framing_clause or _ANCHORED_FRAMING_DEFAULT
    return _ANCHORED_PROMPT_HEAD.format(anchor_role=anchor, framing=framing) + _PROMPT_BASE[head_end:]


def build_keyframe_prompt(
    spec: KeyframeRole,
    visual_identity: dict[str, Any],
    *,
    background_tone: Optional[str] = None,
) -> str:
    """정본 이미지 + 확인된 특성 제약 + 역할 포즈 절 → 키프레임 프롬프트.

    background_tone: 이 계보의 정본이 고른 배경 톤 (pet_background.prompt_tone).
    None 이면 기본 "light-gray" — 플레이트 색과 프롬프트 말이 어긋나지 않게 한다.
    """
    from .canonical_prompt import _with_tone, confident_trait_lines

    parts = [_with_tone(_prompt_base_for(spec), background_tone), f"Requested pose: {spec.required_pose}."]
    if spec.required_visibility:
        parts.append(
            "The following must be clearly visible: "
            + ", ".join(v.replace("_", " ") for v in spec.required_visibility)
            + "."
        )
    parts.extend(confident_trait_lines(visual_identity or {}))
    return "\n".join(parts)


# ── 컴팩트 키프레임 프롬프트 (Runway promptText ≤ 1000자 — 라이브 검증 계약) ──
KEYFRAME_COMPACT_PROMPT_VERSION = "keyframe-prompt-compact-v2"

_COMPACT_PROMPT_BASE = (
    "The first supplied image is the canonical reference of a specific pet; any "
    "additional images are real photos of the same pet. Create a photorealistic "
    "still of the EXACT SAME pet — a pose change only, not a new interpretation. "
    "Preserve face, coat colors, markings, ear shape, body proportions, paws and "
    "tail exactly as supplied; invent nothing. Natural anatomy. Plain solid "
    "neutral {background_tone} background (one flat tone, no floor plane), even "
    "lighting. No contact shadow, no cast shadow. No objects, no scenery, no "
    "other animals, no human, no text, no stylization."
)


def build_compact_keyframe_prompt(
    spec: KeyframeRole,
    visual_identity: dict[str, Any],
    *,
    max_chars: int = 1000,
    background_tone: Optional[str] = None,
) -> str:
    """짧은 전용 변형 — 신원은 이미지가 정본이고, 포즈 절만 필수다."""
    from .canonical_prompt import _compact_trait_lines, _with_tone

    compact_base = _COMPACT_PROMPT_BASE
    anchor = getattr(spec, "anchor_keyframe_role", None)
    if anchor:
        compact_base = (
            f"First image: the pet's approved {anchor} still. Second: its canonical "
            "reference. Further images: real photos of it. Create a photorealistic still of "
            "the EXACT SAME pet — "
            + (spec.compact_anchor_framing_clause or _COMPACT_ANCHORED_FRAMING_DEFAULT)
            + " "
            + _COMPACT_PROMPT_BASE[_COMPACT_PROMPT_BASE.index("Preserve face"):]
        )
    base = _with_tone(compact_base, background_tone)

    pose = f"Requested pose: {getattr(spec, 'compact_pose', None) or spec.required_pose}."
    vis = (
        "Clearly visible: " + ", ".join(v.replace("_", " ") for v in spec.required_visibility) + "."
        if spec.required_visibility
        else ""
    )
    lines = [x for x in (vis,) if x] + _compact_trait_lines(visual_identity or {})
    while True:
        prompt = " ".join([base, pose] + lines)
        if len(prompt) <= max_chars or not lines:
            return prompt
        lines.pop()
