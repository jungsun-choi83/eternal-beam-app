"""
모션 스펙 + 키프레임 라우팅 (Phase 5.1) — Phase 6 영상 생성의 정본 입력 계약.

── 트리거 vs 모션 ──────────────────────────────────────────────────────────
트리거(TOUCH/VOICE/NFC, 레거시 슬롯 IDLE)는 센서/이벤트다 — 몸의 움직임이
아니다. 트리거는 **모션으로 해석된다** (TRIGGERS 매핑). 모션 id 는:
  * 이미 존재하는 것은 그대로 쓴다: BREATHING, BLINKING, EAR_TWITCHING,
    HEAD_TILTING, TAIL_WAGGING (pet_scenarios.IDLE_EVENTS), COME_CLOSER.
  * 오늘 어디에도 없는 모션만 여기서 새로 정의한다 (LIE_DOWN, RUN, PET_HEAD …).
    다른 이름 체계의 중복 정의는 없다 — 이 파일이 모션의 단일 정본이다.
IDLE_TEMPLATE_ORDER 의 5개 키는 BREATHING 계열의 **생성 변형**이지 런타임
모션이 아니다 — 여기 등장하지 않는다.

── 키프레임 재사용 ─────────────────────────────────────────────────────────
모션은 시작(및 전이면 목표) 키프레임 **역할**만 가리킨다. STAND_READY(HOME)
하나가 호흡/깜빡임/귀/머리/꼬리 모션과 서서 시작하는 액션 전부를 감당한다 —
불필요한 스틸을 만들지 않는다 (v16 이전에는 NEUTRAL_IDLE 이 이 자리였다).

── 전략 ────────────────────────────────────────────────────────────────────
MICRO       IMAGE_TO_VIDEO          시작 포즈로 되돌아오는 작은 움직임
                                    (기존 루프 봉합 계약과 일치)
TRANSITION  START_END_FRAME         시작+목표 키프레임 쌍 — Phase 6 이 텍스트로
                                    목표 포즈를 추측하게 두지 않는다
LOCOMOTION  IMAGE_TO_VIDEO_WITH_MOTION_REF (선호) — 모션 레퍼런스 라이브러리는
                                    아직 없다: 메타데이터만 준비, 없으면 경고와
                                    함께 IMAGE_TO_VIDEO 로 폴백
INTERACTION IMAGE_TO_VIDEO          v1 은 사람 손을 요구하지 않는다 —
                                    interaction-ready 포즈에서 시작 (요구 7)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Optional

from ..scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
from .action_keyframe_spec import BREATHING_HOME_STATE, KEYFRAME_ROLES
from .business_qa import (
    AuthorityClass,
    BREATHING_AUTHORITY_V1,
    BREATHING_AUTHORITY_VERSION,
)

# v2: PET_HEAD 에 allow_generated_hand 추가.
# v3 (Phase 6.6): WALK / ROLL_OVER / SIT_DOWN 모션 추가 (기존 어디에도 없던
#     모션만 — 병행 명명 없음), 모션별 선호 카메라 뷰/이동 방향 메타 추가,
#     모션 레퍼런스 해석이 라이브러리 리졸버(motion_reference_service)로 위임됨.
# v4: BREATHING 서술 강화 — 라이브 v5 (Runway seedance2_5) 가 신원/안정성은
#     통과했지만 호흡이 거의 보이지 않았다. 가슴/흉곽 팽창-수축, 상체의 미세한
#     오르내림, 클립당 약 2회 호흡 주기를 명시한다 (다른 모션 서술은 불변).
# v5: BREATHING 서술 재교정 — 라이브 v1/v2 실측에서 v4 의 "clearly visible" +
#     "상체 오르내림"이 국소 흉곽 운동 대신 **전신 줌/스케일 펄스와 프레이밍
#     드리프트**를 유발했다 (VLM 소견이 두 클립 모두에서 zoom/framing drift 를
#     기록). 운동을 가슴/흉곽/옆구리로 국소화하고, 주기를 클립 길이 독립적으로
#     (2~3초당 1회) 명시하며, 전신 펄스·줌·상하 요동·이동을 명시적으로 금지한다.
#     (다른 모션 서술은 불변 — BREATHING 한 항목만 바뀐다.)
# v6: MICRO 길이 고정 4.0s — Runway seedance2_5 의 최소 길이가 4s 라 3s 요청은
#     계약 위반으로 제출 전에 거절된다(TAIL_WAGGING 라이브 실측, run ebbc11f5).
#     개발 중 비용/지연 절감과 MICRO 타이밍 일관성을 겸해 당분간 범위가 아니라
#     고정값으로 둔다. 서술/전략/다른 클래스 길이는 불변이다.
# v8: PET_HEAD 서술 정리 — 엔지니어링 메모가 프롬프트로 새던 것을 제거하고
#     긍정형 영어 장면 묘사로 교체 (wan 라이브 테스트에서 발견). 다른 모션 불변.
# v9: LOOK_UP 상용화 — 시작 키프레임 LOOK_UP→NEUTRAL_IDLE(재사용, 전용 스틸
#     불필요) + 긍정형 영어 서술. VOICE 트리거의 목적지. 다른 모션 불변.
# v10: 자세 전이 3종(LIE_DOWN/STAND_UP/LIE_IDLE) 상용화 — 긍정형 영어 서술로
#      교체 (v8/v9 스타일). 역할/전략/길이 불변. 다른 모션 불변.
# v7: BREATHING 서술을 긍정형으로 재작성 — v5 는 금지 명령 18개 vs 동작 서술
#     1문장이라 모델이 두 극단으로 붕괴했다(전부 동결 + 준정지 틱 0.2%, 또는
#     금지 무시 전신 펄스 3.9%; 16클립 실측). 자연 호흡의 목표 대역(흉곽 국소
#     0.3~2%, 어깨 동반, 약간 불규칙한 리듬)을 **하라는 말**로 명시하고 금지는
#     카메라/이동/균일 스케일 셋만 남긴다. breathing-temporal-qa-v2 보정과 한
#     쌍이다. 다른 모션 서술은 불변.
# v11 (2026-09-10, Phase 4 키프레임 역할 확장): 서서 시작하는 모션들의 시작
#     키프레임을 STAND_READY 로 라우팅 — LOCOMOTION 3종(COME_CLOSER/RUN/WALK)
#     + LIE_DOWN. NEUTRAL_IDLE 은 keyframe-spec-v2 부터 정본 자세를 물려받아
#     앉아 있을 수 있으므로, 이동/기립 시작점은 명시적 서기 포즈가 필요하다.
#     서술(프롬프트)/전략/길이/레퍼런스 전부 불변 — 시작 역할만 바뀐다.
#     ⚠️ 이 범프로 v10 이하 모션 버전에 핀된 FAILED 런은 재시도 시
#     _stale_motion_pin 이 언핀하고 현행 스펙으로 재생성한다(유료).
#     v11 확장 (같은 날 — v11 아티팩트 생성 전이라 재범프 없음): 앉은 홈 ↔
#     STAND_READY 이음매 브리지 전이 SIT_TO_STAND / STAND_TO_SIT 추가.
#     기존 모션 정의는 바이트 단위로 불변이다.
# v12 (Phase 4 authoritative motion registry): 기존 모션 id/키프레임 라우팅/전략은
#     그대로 유지하고, 각 모션에 정본 요구사항(requirements)을 추가한다.
#     (class/type, 역할, 길이, loop/interrupt, morphology 민감도,
#      reference/provider/QA 요구사항). Phase 6.7 매칭/소비 로직은 바꾸지 않는다.
# v14 (Phase 6): motion video QA 에 구조/해부학 도메인 체크
#     structural_morphology_consistency 를 정본 요구사항으로 선언.
# v15: 모션 클래스 기본 + 모션별 override 가능한 영상 provider 순서를 이 파일의
#      정본으로 이동. 생성 서비스/adapter registry 는 이 순서를 소비할 뿐이다.
# v16 (2026-10-05, STAND_READY = HOME): 홈(BREATHING)과 서 있는 홈 아이들 계열
#      (BLINKING/EAR_TWITCHING/HEAD_TILTING/TAIL_WAGGING/LOOK_UP/PET_HEAD)의 시작
#      키프레임을 NEUTRAL_IDLE → STAND_READY 로 이관하고, STAND_UP 의 목표도
#      STAND_READY 로 맞춘다 (홈으로 복귀하는 전이). 이제 홈·아이들·COME_CLOSER·
#      LIE_DOWN 이 **하나의** STAND_READY 스틸을 공유한다 — 아이들 사이 자세
#      점프도, 정본의 나쁜 자세가 홈이 되는 일도 없다. BREATHING 서술은
#      제품 계약의 정확한 문구로 고정하고, 전략/길이/provider 순서는 불변이다.
#      ⚠️ v11 과 같은 주의: 이 범프로 v15 이하에 핀된 FAILED 런은 재시도 시
#      _stale_motion_pin 이 언핀하고 현행 스펙으로 재생성한다(유료).
MOTION_SPEC_VERSION = "motion-spec-v17"
# v2 (Phase 6.6): pet_motion_profile 추가 + motion_reference 가 라이브러리에서
# 해석된 실제 자산/버전/호환성/출처를 담는다 (미해석 시 기존 v1 형태 + 경고 유지).
# v3 (Phase 4): resolve 계약에 motion_type + requirements 를 동봉한다.
PHASE6_CONTRACT_VERSION = "phase6-contract-v3"
MOTION_REGISTRY_CONTRACT_VERSION = "motion-registry-v1"
# QA-only contract version.  Unlike MOTION_SPEC_VERSION, changing this must not
# invalidate or repurchase an already generated video.
MOTION_QA_CONTRACT_VERSION = "motion-qa-contract-v3"
MOTION_ACCEPTANCE_VERSION = "motion-acceptance-v1"
MOTION_REVIEW_POLICY = "publish_with_advisory"

CLASS_MICRO = "MICRO"
CLASS_TRANSITION = "TRANSITION"
CLASS_LOCOMOTION = "LOCOMOTION"
CLASS_INTERACTION = "INTERACTION"
MOTION_CLASSES = (CLASS_MICRO, CLASS_TRANSITION, CLASS_LOCOMOTION, CLASS_INTERACTION)

STRATEGY_I2V = "IMAGE_TO_VIDEO"
STRATEGY_START_END = "START_END_FRAME"
STRATEGY_I2V_MOTION_REF = "IMAGE_TO_VIDEO_WITH_MOTION_REF"

# ── 모션 영상 provider 라우팅 정본 ────────────────────────────────────────
#
# 값은 adapter 구현명이 아니라 안정적인 registry id 다. 실제 adapter/transport
# 선택은 video_motion_providers 가 담당한다. 따라서 모델 교체는 이 표만 바꾸고,
# 생성 서비스에는 motion id → provider 분기가 생기지 않는다.
PROVIDER_WAN_3_STANDARD = "wan_3_standard"
PROVIDER_SEEDANCE = "seedance"
PROVIDER_KLING_3 = "kling_3"
PROVIDER_MINIMAX_H3_MAX_TURBO = "minimax_h3_max_turbo"
PROVIDER_GROK_IMAGINE_LITE = "grok_imagine_lite"

MOTION_PROVIDER_ORDER_BY_CLASS: dict[str, tuple[str, ...]] = {
    CLASS_MICRO: (PROVIDER_WAN_3_STANDARD, PROVIDER_SEEDANCE),
    CLASS_INTERACTION: (PROVIDER_SEEDANCE, PROVIDER_KLING_3),
    CLASS_LOCOMOTION: (PROVIDER_WAN_3_STANDARD, PROVIDER_KLING_3),
    CLASS_TRANSITION: (PROVIDER_KLING_3, PROVIDER_WAN_3_STANDARD),
}

# 특정 모션만 클래스 기본과 다르게 운영할 때 이 표에 추가한다.
# 예: "LOOK_UP": (PROVIDER_SEEDANCE, PROVIDER_WAN_3_STANDARD)
MOTION_PROVIDER_ORDER_OVERRIDES: dict[str, tuple[str, ...]] = {}

# A motion uses exactly one logical model: env > motion default > first class/override
# entry. Capability adaptation and retries stay within that model.
MOTION_MODEL_ENV_PREFIX = "MOTION_MODEL_"
MOTION_MODEL_DEFAULTS: dict[str, str] = {
    # Change these assignments or MOTION_MODEL_<ID> to explicitly select a model.
    BREATHING_HOME_STATE: PROVIDER_WAN_3_STANDARD,
    "COME_CLOSER": PROVIDER_GROK_IMAGINE_LITE,
}

REF_NONE = "none"
REF_PREFERRED = "preferred"
REF_REQUIRED = "required"

AUTHORITY_INTEGRITY_HARD = AuthorityClass.INTEGRITY_HARD.value
AUTHORITY_IDENTITY_SUPPORT = AuthorityClass.IDENTITY_SUPPORT.value
AUTHORITY_QUALITY_ADVISORY = AuthorityClass.QUALITY_ADVISORY.value
AUTHORITY_DIAGNOSTIC_ONLY = AuthorityClass.DIAGNOSTIC_ONLY.value
QA_AUTHORITY_CLASSES = tuple(authority.value for authority in AuthorityClass)


class MotionSpecError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class MotionSpec:
    motion_id: str
    motion_class: str
    #: Generation description. Retain this name for stored contracts/callers.
    description: str
    #: 역할 기반 시작 키프레임. start_from_derived 가 설정된 모션(RETURN_HOME)은
    #: 역할 키프레임이 아니라 **런별 파생 이미지**에서 시작하므로 None 이다.
    start_keyframe_role: Optional[str] = None
    target_keyframe_role: Optional[str] = None
    requires_target_keyframe: bool = False
    #: 설정되면 시작 이미지가 역할 키프레임이 아니라 런별 파생 자산에서 온다
    #: (close_position = COME_CLOSER 종단 프레임). resolve_video_generation_spec 는
    #: start_image_override 를 받아 start_keyframe 페이로드를 구성한다. 현재
    #: RETURN_HOME 하나만 쓴다. 역할 시작 모션(그 외 전부)은 None 이다.
    start_from_derived: Optional[str] = None
    motion_reference_id: Optional[str] = None
    #: none | preferred | required.
    motion_reference_policy: str = REF_NONE
    preferred_video_strategy: str = STRATEGY_I2V
    fallback_video_strategy: Optional[str] = None
    duration_range_sec: tuple[float, float] = (3.0, 6.0)
    loopable: bool = False
    interruptible: bool = True
    video_compat: dict[str, Any] = field(default_factory=dict)
    #: 레퍼런스 매칭용 선호 카메라 뷰/이동 방향 (Phase 6.6). None = 무관.
    preferred_camera_view: Optional[str] = None
    preferred_travel_direction: Optional[str] = None
    #: class 보다 세분화한 타입(도메인 용어). 기존 런타임 id 와는 독립.
    motion_type: str = "GENERIC"
    #: authoritative motion registry 요구사항 (Phase 4).
    requirements: dict[str, Any] = field(default_factory=dict)
    #: Independent acceptance criteria; legacy motions retain their old QA text.
    qa_acceptance_spec: dict[str, Any] = field(default_factory=dict)


#: MICRO 는 클래스 전체가 **고정 4.0s** 다 (v6). 프로바이더 최소(Runway
#: seedance2_5 = 4s)와 개발 비용/지연, 타이밍 일관성이 이유다. 모션별 duration
#: 파라미터를 두지 않는 것이 정책이다 — 값이 하나뿐이어야 어긋날 수 없다.
MICRO_DURATION_SEC = 4.0


def _micro(motion_id: str, desc: str, role: str, *, loopable: bool = False) -> MotionSpec:
    return MotionSpec(
        motion_id=motion_id, motion_class=CLASS_MICRO, description=desc,
        start_keyframe_role=role, preferred_video_strategy=STRATEGY_I2V,
        duration_range_sec=(MICRO_DURATION_SEC, MICRO_DURATION_SEC), loopable=loopable,
        # MICRO 는 시작 포즈로 되돌아온다 — 기존 seam-aligned 반환 계약과 일치.
        video_compat={"returns_to_start_pose": True, "motion_scale": "micro"},
    )


MOTIONS: dict[str, MotionSpec] = {
    # ── MICRO — 기존 런타임 모션 id 그대로 ──────────────────────────────
    BREATHING_HOME_STATE: _micro(
        BREATHING_HOME_STATE,
        "The pet is calm and relaxed, quietly alive in its natural standing pose. "
        "Slow, soft resting breathing creates a very subtle natural movement through "
        "the whole body, with the chest, shoulders, neck, head and fur all responding "
        "gently and together. During the idle, occasional small natural signs of life "
        "occur: a gentle blink, a tiny ear movement, and, if the pet has a tail, a slow, "
        "relaxed tail sway once or twice, never a fast wag. These micro-movements are "
        "sparse, unhurried and never happen all at once or repeat mechanically. The gaze "
        "remains calm and stable. The mouth, jaw and tongue remain exactly as they "
        "appear in the starting image",
        "STAND_READY", loopable=True,
    ),
    "BLINKING": _micro(
        "BLINKING",
        "The only clearly visible intentional action is one complete, natural bilateral "
        "blink. From the starting open-eyed gaze, both visible eyelids close fully "
        "together, pause only for an instant, then reopen fully to the same gaze and "
        "remain open. Preserve natural eye shape, iris color, facial identity and source "
        "occlusion; do not substitute a wink, squint, facial twitch or head movement",
        "STAND_READY",
    ),
    "EAR_TWITCHING": _micro(
        "EAR_TWITCHING",
        "The dominant action is one clearly visible, small, quick ear twitch. One "
        "anatomically visible ear tip or ear body flicks briefly away from its resting "
        "orientation and returns; both visible ears may twitch only when that is natural "
        "for this pet. Preserve the source ear shape, size, fold, set and occlusion; do "
        "not straighten, perk up, enlarge, invent or duplicate an ear, and do not "
        "substitute a head shake or body shudder",
        "STAND_READY",
    ),
    "HEAD_TILTING": _micro(
        "HEAD_TILTING",
        "The dominant action is one clearly visible, gentle sideways roll of the head, "
        "roughly 10 to 20 degrees. One ear becomes slightly lower than the other while "
        "the muzzle and gaze keep pointing in the same direction; this is not a left or "
        "right turn, profile rotation or nod. The neck and upper chest may follow with "
        "small connected secondary motion. The head then returns smoothly to its "
        "original upright orientation. Preserve ear and neck anatomy while the tail, "
        "paws and lower body remain calm",
        "STAND_READY",
    ),
    "TAIL_WAGGING": _micro(
        "TAIL_WAGGING",
        "The dominant action is one gentle, natural tail wag. The anatomically evidenced "
        "visible tail moves side to side from its base two or three times, then settles "
        "smoothly back to its original resting carriage. Preserve the source tail's "
        "length, thickness, curl, fur and occlusion; do not straighten, lengthen, invent "
        "or duplicate it. The motion comes from the tail rather than a hip swing, torso "
        "rotation or whole-body sway, while the head and planted paws remain calm",
        "STAND_READY",
    ),

    "LOOK_UP": _micro(
        "LOOK_UP",
        "The pet lifts its head and looks up attentively, as if hearing a "
        "familiar voice from above. It holds the upward gaze for a moment with "
        "a soft curious expression, then naturally lowers its head back to the "
        "exact starting pose",
        "STAND_READY",
    ),
    "HAPPY": _micro("HAPPY", "반가운 알림 반응 — 귀 쫑긋, 밝은 표정, 가벼운 몸짓", "HAPPY"),
    "LIE_IDLE": _micro(
        "LIE_IDLE",
        "The pet rests calmly lying on its belly, exactly as in the reference image. "
        "The pet breathes calmly and naturally with very small body movement while staying relaxed and lying in place. "
        "It returns to the exact starting resting pose so the clip loops smoothly",
        "LIE",
        loopable=True,
    ),
    "SLEEP_BREATH": _micro("SLEEP_BREATH", "잠든 채 고른 숨쉬기", "SLEEP", loopable=True),
    # ── TRANSITION — 시작+목표 쌍 명시 ──────────────────────────────────
    "LIE_DOWN": MotionSpec(
        motion_id="LIE_DOWN", motion_class=CLASS_TRANSITION,
        description="From its standing pose, the pet naturally bends its legs "
        "and settles down to lie on its belly, ending calm and relaxed in the "
        "lying pose shown in the second frame",
        start_keyframe_role="STAND_READY", target_keyframe_role="LIE",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.5, 5.0), loopable=False,
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),

    "SIT_TO_STAND": MotionSpec(
        motion_id="SIT_TO_STAND", motion_class=CLASS_TRANSITION,
        description="From its current relaxed neutral pose, the pet smoothly "
        "rises to stand on all four legs, ending calm and balanced in the "
        "standing pose shown in the second frame, ready to move",
        start_keyframe_role="NEUTRAL_IDLE", target_keyframe_role="STAND_READY",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.0, 4.0), loopable=False,
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    "STAND_TO_SIT": MotionSpec(
        motion_id="STAND_TO_SIT", motion_class=CLASS_TRANSITION,
        description="From standing on all four legs, the pet calmly settles "
        "back into its natural relaxed neutral pose shown in the second "
        "frame, ending still and comfortable",
        start_keyframe_role="STAND_READY", target_keyframe_role="NEUTRAL_IDLE",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.0, 4.0), loopable=False,
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    "STAND_UP": MotionSpec(
        motion_id="STAND_UP", motion_class=CLASS_TRANSITION,
        description="From lying on its belly, the pet pushes up with its front "
        "legs and rises smoothly back to the standing pose shown in the second "
        "frame, ending calm and balanced",
        start_keyframe_role="LIE", target_keyframe_role="STAND_READY",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.0, 4.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    "FALL_ASLEEP": MotionSpec(
        motion_id="FALL_ASLEEP", motion_class=CLASS_TRANSITION,
        description="엎드린 채 스르르 잠들기",
        start_keyframe_role="LIE", target_keyframe_role="SLEEP",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    "WAKE_UP": MotionSpec(
        motion_id="WAKE_UP", motion_class=CLASS_TRANSITION,
        description="잠에서 깨어 고개 들기",
        start_keyframe_role="SLEEP", target_keyframe_role="LIE",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.0, 4.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    # ── LOCOMOTION ──────────────────────────────────────────────────────
    "COME_CLOSER": MotionSpec(
        motion_id="COME_CLOSER", motion_class=CLASS_LOCOMOTION,
        description=(
            "The pet runs naturally toward its owner, who is standing just behind the camera. "
            "The camera stays completely still with no zoom or reframing. "
            "{gait_text}. "
            "Keep the exact same pet throughout the clip: same face, muzzle, eyes, ears, fur color, markings, and body proportions. "
            "Use a natural grounded gait with clear ground contact. "
            "No floating, sliding, limb merging, or morphing. "
            "Keep the whole pet inside the frame, including the paws. "
            "The pet becomes only slightly larger as it approaches, then slows down and stops calmly while remaining fully visible in the frame, front-facing or nearly front-facing."
        ),
        start_keyframe_role="STAND_READY",
        # TEMP (2026-10-22): DOG_APPROACH 모션 레퍼런스를 **일시 제거**한다. COME_CLOSER 는
        # 당분간 단일 시작 I2V 로만 생성한다 — 시작 이미지는 STAND_READY, 엔드 이미지 없음,
        # 모션 레퍼런스 없음. 되돌릴 때는 motion_reference_id="DOG_APPROACH",
        # motion_reference_policy=REF_PREFERRED, preferred_video_strategy=
        # STRATEGY_I2V_MOTION_REF, fallback_video_strategy=STRATEGY_I2V 를 복원하면 된다.
        # RETURN_HOME·파생 CLOSE_POSITION·QA·프로바이더 순서는 영향받지 않는다.
        preferred_video_strategy=STRATEGY_I2V,
        duration_range_sec=(5.0, 7.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
        preferred_camera_view="FRONT",
        preferred_travel_direction="TOWARD_CAMERA",
    ),
    # RETURN_HOME — COME_CLOSER 의 시스템 소유 무료 동반 복귀 모션 (2026-10-22, 하이브리드).
    # 구매 불가·Behavior Library 비노출·크레딧 0 (motion_delivery_service.SYSTEM_MOTIONS).
    # 시작은 **런별 파생 CLOSE_POSITION** (COME_CLOSER 가 성공한 뒤 그 영상에서 고른
    # 종단 프레임을 그대로 PNG 로 박제한 것 — close_position_service). 역할 키프레임이
    # 아니므로 start_keyframe_role 은 None 이고 start_from_derived="close_position" 이다.
    # 끝은 STAND_READY(HOME) 역할 키프레임. START_END 로 양끝이 생성을 규정하고 런타임은
    # COME_CLOSER 뒤에 이 클립을 이어 홈으로 돌아간다. 이 단계에서는 신원 복원/이미지
    # 편집을 하지 않는다 — 고른 프레임을 그대로 쓴다(기본 모션 흐름 검증용).
    "RETURN_HOME": MotionSpec(
        motion_id="RETURN_HOME", motion_class=CLASS_LOCOMOTION,
        description=(
            "The same pet performs RETURN_HOME in one continuous shot. "
            "The camera remains completely stationary. "

            "The pet begins at CLOSE_POSITION, near the camera, exactly as shown "
            "in the supplied starting image. "

            "The pet turns away from the camera and walks naturally toward HOME_IDLE. "
            "It continues moving away without reversing direction. "
            "As it retreats, it gradually becomes smaller while remaining centered. "

            "Upon reaching its HOME_IDLE position, the pet stops walking. "
            "It then turns in place to face the camera, without stepping forward "
            "or changing its distance from the camera. "

            "The final pose must be compatible with the supplied HOME_IDLE reference "
            "and transition naturally into BREATHING. "
            "The pet must remain at its home location after arriving. "

            "There must be no second approach toward the camera, "
            "no return to CLOSE_POSITION, and no forward movement after retreat. "
        ),
        start_from_derived="close_position",
        target_keyframe_role="STAND_READY",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(6.0, 8.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
        preferred_camera_view="FRONT",
        preferred_travel_direction="AWAY_FROM_CAMERA",
    ),
    "RUN": MotionSpec(
        motion_id="RUN", motion_class=CLASS_LOCOMOTION,
        description="신나게 달리기 (미래 모션)",
        start_keyframe_role="STAND_READY",
        motion_reference_id="DOG_RUN", motion_reference_policy=REF_PREFERRED,
        preferred_video_strategy=STRATEGY_I2V_MOTION_REF,
        fallback_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
    ),
    "WALK": MotionSpec(
        motion_id="WALK", motion_class=CLASS_LOCOMOTION,
        description="자연스러운 걸음걸이 (미래 모션)",
        start_keyframe_role="STAND_READY",
        motion_reference_id="WALK_REF", motion_reference_policy=REF_PREFERRED,
        preferred_video_strategy=STRATEGY_I2V_MOTION_REF,
        fallback_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
    ),
    "ROLL_OVER": MotionSpec(
        motion_id="ROLL_OVER", motion_class=CLASS_TRANSITION,
        description="엎드린 채 한 바퀴 구르고 다시 엎드리기 (미래 모션)",
        start_keyframe_role="LIE", target_keyframe_role="LIE",
        # 같은 포즈로 돌아오므로 목표 프레임 필수는 아니다. 텍스트 생성 신뢰도가
        # 벤치마크에서 낮게 나오면 정책을 required 로 올린다 (증거 기반, 요구 12).
        requires_target_keyframe=False,
        motion_reference_id="ROLL_OVER_REF", motion_reference_policy=REF_PREFERRED,
        preferred_video_strategy=STRATEGY_I2V_MOTION_REF,
        fallback_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": True, "motion_scale": "body"},
    ),
    "SIT_DOWN": MotionSpec(
        motion_id="SIT_DOWN", motion_class=CLASS_TRANSITION,
        description="선 자세에서 앉기 (미래 모션 — NEUTRAL_IDLE 포즈군 내 전이)",
        start_keyframe_role="NEUTRAL_IDLE", target_keyframe_role="NEUTRAL_IDLE",
        requires_target_keyframe=False,
        preferred_video_strategy=STRATEGY_I2V,
        duration_range_sec=(2.0, 4.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    # ── INTERACTION — v1 은 사람 손을 키프레임에 요구하지 않는다 ─────────
    "PET_HEAD": MotionSpec(
        motion_id="PET_HEAD", motion_class=CLASS_INTERACTION,
        description=(
            "The pet performs a clearly visible, gentle and affectionate head response. "
            "Starting from its calm upright standing pose, the pet slowly lowers and "
            "leans its head slightly forward and downward with a natural movement of "
            "the neck. Its eyes soften or briefly close, and its facial expression "
            "becomes peaceful and contented. The pet holds this relaxed expression "
            "briefly while continuing subtle natural breathing, gentle ear adjustments "
            "and small connected movements of the neck and upper body. The pet remains "
            "naturally alive throughout the entire animation, never frozen or rigid. "
            "The movement is an affectionate head dip, not a sideways head tilt, "
            "repeated nodding or exaggerated bow. The pet then smoothly raises its "
            "head, opens its eyes and returns to the exact original calm upright "
            "standing pose. Keep all paws grounded and the pet's position unchanged. "
            "Preserve the entire head, face, ears, muzzle, body and paws throughout. "
            "Only the original pet is visible, with no additional subjects or objects"
        ),
        # The action hands the same approved HOME pose to Seedance as both
        # endpoints.  The target remains non-required for QA purposes: it is a
        # generation control that removes the PET_HEAD -> BREATHING seam, not a
        # new transition acceptance policy.
        start_keyframe_role="STAND_READY",
        target_keyframe_role="STAND_READY",
        preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(3.0, 5.0),
        interruptible=False,
        video_compat={"returns_to_start_pose": True, "motion_scale": "micro",
                      "interaction": "head_touch", "requires_human_in_keyframe": False,
                      "allow_generated_hand": False,
                      "end_frame_conditioning_required": True},
    ),
    "PAW_HELLO": MotionSpec(
        motion_id="PAW_HELLO", motion_class=CLASS_INTERACTION,
        description=(
            "Starting from the approved calm upright STAND_READY pose, the pet makes a "
            "small natural weight transfer over the three supporting paws, then lifts "
            "exactly one front paw cleanly from the ground and gently offers it a short "
            "distance toward the viewer. The lifted paw remains anatomically connected, "
            "relaxed and clearly readable. Hold the offered paw briefly, then retract and "
            "lower it along one continuous natural path until it returns to its original "
            "grounded placement. Maintain quiet natural breathing and subtle connected "
            "head, ear, tail and torso responses while preserving realistic balance. The "
            "other three supporting paws stay grounded without sliding. Preserve the "
            "pet's identity, four-limb anatomy, joint structure and contact with the "
            "ground. Finish in the exact original STAND_READY pose so BREATHING can resume "
            "without a visible jump. Only the original pet is visible: no human, human "
            "hand, person, leash, toy, platform or added object"
        ),
        start_keyframe_role="STAND_READY",
        target_keyframe_role="STAND_READY",
        preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(4.0, 5.0),
        interruptible=False,
        video_compat={"returns_to_start_pose": True, "motion_scale": "body-part",
                      "interaction": "paw_hello", "requires_human_in_keyframe": False,
                      "allow_generated_hand": False,
                      "end_frame_conditioning_required": True},
    ),
}


_MOTION_TYPES: dict[str, str] = {
    BREATHING_HOME_STATE: "IDLE_BREATH",
    "BLINKING": "IDLE_FACE_MICRO",
    "EAR_TWITCHING": "IDLE_EAR_MICRO",
    "HEAD_TILTING": "IDLE_HEAD_MICRO",
    "TAIL_WAGGING": "IDLE_TAIL_MICRO",
    "LOOK_UP": "ATTENTIVE_LOOK",
    "HAPPY": "AFFECTIVE_MICRO",
    "LIE_IDLE": "LIE_IDLE_LOOP",
    "SLEEP_BREATH": "SLEEP_BREATH_LOOP",
    "LIE_DOWN": "POSE_TRANSITION_DOWN",
    "SIT_TO_STAND": "POSE_TRANSITION_UP",
    "STAND_TO_SIT": "POSE_TRANSITION_DOWN",
    "STAND_UP": "POSE_TRANSITION_UP",
    "FALL_ASLEEP": "STATE_TRANSITION_SLEEP",
    "WAKE_UP": "STATE_TRANSITION_WAKE",
    "COME_CLOSER": "APPROACH_LOCOMOTION",
    "RETURN_HOME": "RETURN_LOCOMOTION",
    "RUN": "RUN_LOCOMOTION",
    "WALK": "WALK_LOCOMOTION",
    "ROLL_OVER": "ROLL_TRANSITION",
    "SIT_DOWN": "SIT_TRANSITION",
    "PET_HEAD": "HUMAN_INTERACTION_REACTION",
    "PAW_HELLO": "PAW_OFFER_INTERACTION",
}

_MORPHOLOGY_SENSITIVITY: dict[str, str] = {
    BREATHING_HOME_STATE: "LOW",
    "BLINKING": "LOW",
    "EAR_TWITCHING": "LOW",
    "HEAD_TILTING": "LOW",
    "TAIL_WAGGING": "MEDIUM",
    "LOOK_UP": "LOW",
    "HAPPY": "MEDIUM",
    "LIE_IDLE": "MEDIUM",
    "SLEEP_BREATH": "MEDIUM",
    "LIE_DOWN": "MEDIUM",
    "SIT_TO_STAND": "MEDIUM",
    "STAND_TO_SIT": "MEDIUM",
    "STAND_UP": "MEDIUM",
    "FALL_ASLEEP": "MEDIUM",
    "WAKE_UP": "MEDIUM",
    "COME_CLOSER": "HIGH",
    "RETURN_HOME": "HIGH",
    "RUN": "HIGH",
    "WALK": "HIGH",
    "ROLL_OVER": "HIGH",
    "SIT_DOWN": "MEDIUM",
    "PET_HEAD": "MEDIUM",
    "PAW_HELLO": "HIGH",
}


def is_loop_same_start_end(spec: MotionSpec) -> bool:
    """루프 가능한 MICRO 아이들 모션은 **시작 역할 키프레임을 양끝**으로 쓴다.

    규칙은 특정 모션(BREATHING)이나 특정 역할(STAND_READY)을 박지 않는다 —
    클래스(MICRO)·loopable·returns_to_start_pose 세 속성만 본다. 해당하면
    build 단계가 provider 능력을 보고 START_END(같은 키프레임을 start=end)로
    올리거나, end-frame 미지원이면 기존 단일 시작 I2V 로 둔다. start_from_derived
    (RETURN_HOME)·target_keyframe_role 을 선언한 모션은 자체 START_END 계약이
    따로 있으므로 제외한다.
    """
    return (
        spec.motion_class == CLASS_MICRO
        and bool(spec.loopable)
        and bool(spec.video_compat.get("returns_to_start_pose"))
        and spec.start_from_derived is None
        and spec.target_keyframe_role is None
        and bool(spec.start_keyframe_role)
    )


def _class_default_qa(spec: MotionSpec) -> dict[str, Any]:
    structural_checks = ["vlm_anatomy"]
    identity_checks = ["identity_over_time", "vlm_same_pet"]
    motion_specific = ["vlm_motion", "temporal_stability", "vlm_composition"]

    if spec.motion_class == CLASS_TRANSITION:
        structural_checks = [
            "starts_at_start_pose",
            "reaches_target_pose",
            "vlm_anatomy",
            "structural_morphology_consistency",
        ]
        motion_specific = ["vlm_motion", "vlm_target_pose", "temporal_stability", "vlm_composition"]
    elif spec.motion_class == CLASS_LOCOMOTION:
        structural_checks = ["vlm_anatomy", "structural_morphology_consistency"]
        identity_checks = ["identity_over_time", "vlm_same_pet"]
        motion_specific = [
            "vlm_motion",
            "vlm_locomotion_form",
            "vlm_direction_travel",
            "temporal_stability",
            "vlm_composition",
        ]
    elif spec.motion_class == CLASS_INTERACTION:
        structural_checks = ["vlm_anatomy", "structural_morphology_consistency"]
        motion_specific = [
            "vlm_motion",
            "vlm_interaction",
            "vlm_human_hand_policy",
            "temporal_stability",
            "vlm_composition",
        ]
        if spec.motion_id == "PAW_HELLO":
            motion_specific.extend([
                "vlm_paw_lift", "vlm_paw_offer", "vlm_paw_hold",
                "vlm_paw_support", "vlm_paw_balance", "vlm_paw_limb_count",
                "vlm_paw_anatomy", "vlm_paw_return",
            ])

    if bool(spec.video_compat.get("returns_to_start_pose")):
        motion_specific.append("loop_return")
    if spec.motion_id == BREATHING_HOME_STATE:
        motion_specific.append("temporal_breathing")

    # 목표 키프레임이 필수인 비-TRANSITION 모션(RETURN_HOME: 파생 CLOSE_POSITION→
    # STAND_READY)도 전이와 똑같이 시작/목표 도달을 하드 구조 게이트한다 — TRANSITION
    # 분기가 쓰는 starts_at_start_pose / reaches_target_pose 판정기를 그대로 재사용한다.
    if (
        spec.motion_class != CLASS_TRANSITION
        and spec.requires_target_keyframe
        and spec.target_keyframe_role
    ):
        for _c in ("starts_at_start_pose", "reaches_target_pose"):
            if _c not in structural_checks:
                structural_checks.append(_c)
        if "vlm_target_pose" not in motion_specific:
            motion_specific.append("vlm_target_pose")

    return {
        "structural_anatomy": {
            "required_checks": structural_checks,
            "morphology_consistency": {
                "check": "structural_morphology_consistency",
                # body_size_class 제외 — 프레임 점유율은 실제 체급이 아니라서
                # 구조 QA 의 비교 축이 될 수 없다.
                "compare_fields": [
                    "body_length_class",
                    "leg_length_class",
                    "head_proportion_class",
                    "muzzle_proportion_class",
                    "body_build_class",
                    "ear_form",
                    "tail_form",
                ],
                "minimum_support_frames": 2,
                "strong_contradiction_ratio": 0.7,
                "use_sampled_frames": True,
                "missing_evidence_policy": "unknown_or_review_never_fail",
                "severe_corruption_signals": [
                    "limb_count_or_placement",
                    "joint_anatomy_plausibility",
                    "body_deformation",
                ],
                # 자세·카메라·기울기에 따라 자연히 함께 바뀌는 bbox/실루엣 신호들.
                # 어느 모션 클래스에서도 이것들의 변화만으로 해부학 붕괴를 주장할
                # 수 없다 — 강한 모순도 자문(REVIEW)으로만 남긴다.
                "pose_dependent_fields": [
                    "body_length_class",
                    "leg_length_class",
                    "body_build_class",
                ],
                "pose_dependent_policy": "review_never_fail",
                "pose_dependent_signals": ["body_deformation"],
                # severe_corruption_signals 는 전부 휴리스틱 마스크 기하에서
                # 나온다 — required_checks 에 올리지 않는 한 자문(REVIEW)이다.
                # 하드 FAIL 게이트는 required 인 vlm_anatomy /
                # structural_morphology_consistency 가 담당한다.
                "severe_corruption_signal_policy": "advisory_unless_required",
            },
            "notes": "Structure/anatomy checks are fail-closed in QA when explicit FAIL appears, except pose-dependent morphology signals and non-required advisory checks.",
        },
        "identity": {
            "required_checks": identity_checks,
            "notes": "Identity continuity over time remains mandatory.",
        },
        "motion_specific": {
            "required_checks": motion_specific,
            "notes": "Motion-class specific checks are declared in registry and executed in motion_video_qa.",
        },
        "business": _class_business_qa(spec),
    }


def _class_business_qa(spec: MotionSpec) -> dict[str, Any]:
    """Business authority/applicability for one motion, owned by the registry."""

    loop_return_required = bool(spec.video_compat.get("returns_to_start_pose"))
    domains: dict[str, dict[str, Any]] = {
        "identity_continuity": {
            "required_checks": ["identity_over_time", "vlm_same_pet"],
        },
    }
    authority = {
        # Deterministic identity similarity is supporting evidence; the VLM's
        # explicit all-frame individual contradiction is the hard gate.
        "identity_over_time": AUTHORITY_IDENTITY_SUPPORT,
        "vlm_same_pet": AUTHORITY_INTEGRITY_HARD,
        "vlm_anatomy": AUTHORITY_INTEGRITY_HARD,
        "vlm_motion": AUTHORITY_INTEGRITY_HARD,
        "vlm_composition": AUTHORITY_INTEGRITY_HARD,
        # Minor stability/loop/heuristic morphology differences are advisory.
        "temporal_stability": AUTHORITY_QUALITY_ADVISORY,
        "loop_return": AUTHORITY_QUALITY_ADVISORY,
        "structural_morphology_consistency": AUTHORITY_IDENTITY_SUPPORT,
        "anatomy_limb_count_placement": AUTHORITY_QUALITY_ADVISORY,
        "anatomy_joint_plausibility": AUTHORITY_QUALITY_ADVISORY,
        "anatomy_body_deformation": AUTHORITY_QUALITY_ADVISORY,
        # These semantic signals are emitted only for severe threshold-level
        # contradictions; their source measurements remain preserved above.
        "motion_temporal_integrity": AUTHORITY_INTEGRITY_HARD,
        "motion_structural_integrity": AUTHORITY_INTEGRITY_HARD,
    }

    if spec.motion_class == CLASS_MICRO:
        domains.update(
            {
                "temporal_stability": {"required_checks": ["temporal_stability"]},
                "motion_correctness": {"required_checks": ["vlm_motion"]},
                "severe_anatomy": {"required_checks": ["vlm_anatomy"]},
                "loop_usability": {
                    "required_checks": ["loop_return"] if loop_return_required else [],
                    "applicable": loop_return_required,
                },
            }
        )
        # 루프 same-start-end 아이들: 끝점 QA(첫≈시작, 끝≈같은 시작)를 **진단 전용**
        # 으로 둔다. 전역 레지스트리의 기본은 INTEGRITY_HARD(전이/RETURN_HOME 용)지만
        # 아이들에선 전달/과금 판정을 바꾸지 않는다 — 기하 기반 끝점 신호는 아이들
        # 루프에서 loop_return(자문 SSIM 게이트)이 이미 담당하고, 끝점은 영수증에
        # 측정값으로만 남긴다. breathing 이 vlm_motion/temporal_breathing 을 진단으로
        # 두는 설계(의미 신호만 제품 권위)와 일치한다. build 가 START_END 로 올린
        # 생성에서만 산출되고, required 가 아니라 단일 I2V 폴백의 누락도 무해하다.
        if is_loop_same_start_end(spec):
            domains["loop_endpoints"] = {"required_checks": [], "applicable": True}
            authority.update(
                {
                    "starts_at_start_pose": AUTHORITY_DIAGNOSTIC_ONLY,
                    "reaches_target_pose": AUTHORITY_DIAGNOSTIC_ONLY,
                }
            )
        # BREATHING receives its specialized authority migration in Phase 5.
        if spec.motion_id == BREATHING_HOME_STATE:
            # Phase 5: preserve legacy temporal_breathing/vlm_motion evidence,
            # but give product authority to analyzer-derived semantic signals.
            domains["motion_correctness"]["required_checks"] = [
                "breathing_motion_correctness",
            ]
            domains["global_motion_integrity"] = {
                "required_checks": ["breathing_global_motion_integrity"],
            }
            domains["composition_integrity"] = {
                "required_checks": ["breathing_composition_integrity"],
            }
            authority.update(
                {
                    "vlm_motion": AUTHORITY_DIAGNOSTIC_ONLY,
                    "vlm_composition": AUTHORITY_QUALITY_ADVISORY,
                    "temporal_breathing": AUTHORITY_DIAGNOSTIC_ONLY,
                    "breathing_motion_correctness": AUTHORITY_QUALITY_ADVISORY,
                    "breathing_periodicity": AUTHORITY_QUALITY_ADVISORY,
                    "breathing_modulation": AUTHORITY_QUALITY_ADVISORY,
                    "breathing_head_motion": AUTHORITY_QUALITY_ADVISORY,
                    "breathing_global_motion_integrity": AUTHORITY_INTEGRITY_HARD,
                    "breathing_composition_integrity": AUTHORITY_INTEGRITY_HARD,
                }
            )
            if BREATHING_AUTHORITY_VERSION != BREATHING_AUTHORITY_V1:
                # breathing-v2: sway/drift/settling/scale_trend findings are
                # advisory. Only the catastrophic whole-body scale pulse
                # (thresholds in motion_video_qa) can spend another candidate.
                domains["catastrophic_motion_integrity"] = {
                    "required_checks": ["breathing_catastrophic_motion_integrity"],
                }
                authority.update(
                    {
                        "breathing_global_motion_integrity": AUTHORITY_QUALITY_ADVISORY,
                        "breathing_catastrophic_motion_integrity": AUTHORITY_INTEGRITY_HARD,
                    }
                )
    elif spec.motion_class == CLASS_TRANSITION:
        domains.update(
            {
                "start_state": {"required_checks": ["starts_at_start_pose"]},
                "target_pose": {
                    "required_checks": ["reaches_target_pose", "vlm_target_pose"],
                },
                "motion_correctness": {"required_checks": ["vlm_motion"]},
                "anatomy": {"required_checks": ["vlm_anatomy"]},
                "structural_continuity": {
                    "required_checks": ["structural_morphology_consistency"],
                },
            }
        )
        authority.update(
            {
                "starts_at_start_pose": AUTHORITY_INTEGRITY_HARD,
                "reaches_target_pose": AUTHORITY_INTEGRITY_HARD,
                "vlm_target_pose": AUTHORITY_INTEGRITY_HARD,
            }
        )
    elif spec.motion_class == CLASS_LOCOMOTION:
        domains.update(
            {
                "motion_correctness": {
                    "required_checks": ["vlm_motion", "vlm_locomotion_form"],
                },
                "direction_travel": {
                    "required_checks": ["vlm_direction_travel"],
                },
                "limb_joint_integrity": {
                    "required_checks": [
                        "vlm_anatomy",
                        "anatomy_limb_count_placement",
                        "anatomy_joint_plausibility",
                    ],
                },
                "structural_continuity": {
                    "required_checks": ["structural_morphology_consistency"],
                },
            }
        )
        authority.update(
            {
                "vlm_locomotion_form": AUTHORITY_INTEGRITY_HARD,
                "vlm_direction_travel": AUTHORITY_INTEGRITY_HARD,
            }
        )
    elif spec.motion_class == CLASS_INTERACTION:
        domains.update(
            {
                "interaction_correctness": {
                    "required_checks": ["vlm_motion", "vlm_interaction"],
                },
                "anatomy": {"required_checks": ["vlm_anatomy"]},
                "structural_continuity": {
                    "required_checks": ["structural_morphology_consistency"],
                },
                "human_hand_policy": {
                    "required_checks": ["vlm_composition", "vlm_human_hand_policy"],
                    "allow_generated_hand": bool(spec.video_compat.get("allow_generated_hand")),
                    "requires_human_in_keyframe": bool(
                        spec.video_compat.get("requires_human_in_keyframe")
                    ),
                },
            }
        )
        authority.update(
            {
                "vlm_interaction": AUTHORITY_INTEGRITY_HARD,
                "vlm_human_hand_policy": AUTHORITY_INTEGRITY_HARD,
            }
        )
        if spec.motion_id == "PAW_HELLO":
            paw_checks = [
                "vlm_paw_lift", "vlm_paw_offer", "vlm_paw_hold",
                "vlm_paw_support", "vlm_paw_balance", "vlm_paw_limb_count",
                "vlm_paw_anatomy", "vlm_paw_return",
            ]
            domains["paw_hello"] = {"required_checks": paw_checks}
            authority.update(dict.fromkeys(paw_checks, AUTHORITY_INTEGRITY_HARD))

    # 목표 키프레임이 필수인 비-TRANSITION 모션(RETURN_HOME): 시작/목표 도달을 전이와
    # 동일한 하드 게이트(INTEGRITY_HARD)로 올린다 — TRANSITION 의 start_state/target_pose
    # 와 완전히 같은 계약이다. 위 클래스 분기가 더한 도메인은 그대로 두고 끝점만 보탠다.
    if (
        spec.motion_class != CLASS_TRANSITION
        and spec.requires_target_keyframe
        and spec.target_keyframe_role
    ):
        domains.setdefault("start_state", {"required_checks": ["starts_at_start_pose"]})
        domains.setdefault(
            "target_pose",
            {"required_checks": ["reaches_target_pose", "vlm_target_pose"]},
        )
        authority.update(
            {
                "starts_at_start_pose": AUTHORITY_INTEGRITY_HARD,
                "reaches_target_pose": AUTHORITY_INTEGRITY_HARD,
                "vlm_target_pose": AUTHORITY_INTEGRITY_HARD,
            }
        )

    acceptance = spec.qa_acceptance_spec
    if acceptance.get("version") == MOTION_ACCEPTANCE_VERSION:
        authority.update(acceptance["check_authority"])
        domains["acceptance"] = {"required_checks": acceptance["required_checks"]}

    # 목표 키프레임이 필수인 비-TRANSITION 모션(RETURN_HOME): 시작/목표 도달을 전이와
    # 동일한 하드 게이트(INTEGRITY_HARD)로 올린다 — TRANSITION 의 start_state/target_pose
    # 와 완전히 같은 계약이다. 위 클래스 분기가 더한 도메인은 그대로 두고 끝점만 보탠다.
    if (
        spec.motion_class != CLASS_TRANSITION
        and spec.requires_target_keyframe
        and spec.target_keyframe_role
    ):
        domains.setdefault("start_state", {"required_checks": ["starts_at_start_pose"]})
        domains.setdefault(
            "target_pose",
            {"required_checks": ["reaches_target_pose", "vlm_target_pose"]},
        )
        authority.update(
            {
                "starts_at_start_pose": AUTHORITY_INTEGRITY_HARD,
                "reaches_target_pose": AUTHORITY_INTEGRITY_HARD,
                "vlm_target_pose": AUTHORITY_INTEGRITY_HARD,
            }
        )

    acceptance = spec.qa_acceptance_spec
    if acceptance.get("version") == MOTION_ACCEPTANCE_VERSION:
        authority.update(acceptance["check_authority"])
        domains["acceptance"] = {"required_checks": acceptance["required_checks"]}

    return {
        "version": MOTION_QA_CONTRACT_VERSION,
        "acceptance": deepcopy(acceptance),
        "authority_profile": (
            BREATHING_AUTHORITY_VERSION
            if spec.motion_id == BREATHING_HOME_STATE
            else None
        ),
        "motion_class": spec.motion_class,
        "domains": domains,
        "check_authority": authority,
        "expected_direction": spec.preferred_travel_direction,
        "interaction_type": spec.video_compat.get("interaction"),
        "allow_generated_hand": bool(spec.video_compat.get("allow_generated_hand")),
        "loop_return_required": loop_return_required,
        "structural_required": spec.motion_class in (
            CLASS_TRANSITION,
            CLASS_LOCOMOTION,
            CLASS_INTERACTION,
        ),
    }


def _morphology_match_fields(spec: MotionSpec) -> list[str]:
    """
    모션별 구조 매칭 축 (종은 항상 하드 게이트라 별도).

    body_size_class 는 축이 아니다: 사진에서 얻을 수 있는 유일한 근거가
    프레임 점유율(카메라 거리의 함수)이라 실제 체급이 아니었다. 실측 소스가
    생기기 전까지 구조 매칭에서 제외한다.
    """
    base = ["leg_length_class", "body_length_class"]
    if spec.motion_class == CLASS_LOCOMOTION:
        return [*base, "body_build_class"]
    if spec.motion_id in ("TAIL_WAGGING", "LIE_IDLE", "SLEEP_BREATH"):
        return [*base, "tail_form"]
    if spec.motion_id == "PET_HEAD":
        return ["head_proportion_class", "muzzle_proportion_class", "ear_form"]
    return base


def _morphology_confidence_floor(spec: MotionSpec) -> str:
    sensitivity = _MORPHOLOGY_SENSITIVITY.get(spec.motion_id, "MEDIUM")
    return "medium" if sensitivity in ("HIGH", "MEDIUM") else "low"


def _requirements_for(spec: MotionSpec) -> dict[str, Any]:
    required_end_roles: list[str] = []
    optional_end_roles: list[str] = []
    if spec.target_keyframe_role:
        if spec.requires_target_keyframe:
            required_end_roles = [spec.target_keyframe_role]
        else:
            optional_end_roles = [spec.target_keyframe_role]

    provider_required_all: list[str] = []
    provider_preferred_any: list[str] = []
    degrade_allowed = True
    degrade_target = spec.fallback_video_strategy

    if spec.preferred_video_strategy == STRATEGY_START_END:
        provider_preferred_any.append("supports_end_frame")
        degrade_target = STRATEGY_I2V
    if spec.preferred_video_strategy == STRATEGY_I2V_MOTION_REF:
        provider_preferred_any.append("supports_motion_reference")
        degrade_target = STRATEGY_I2V

    match_fields = _morphology_match_fields(spec)

    reference_requirements: dict[str, Any] = {
        "policy": spec.motion_reference_policy,
        "reference_id": spec.motion_reference_id,
        "required": spec.motion_reference_policy == REF_REQUIRED,
        "preferred": spec.motion_reference_policy == REF_PREFERRED,
        "allows_fallback": bool(spec.fallback_video_strategy),
        "fallback_video_strategy": spec.fallback_video_strategy,
    }
    if spec.motion_reference_policy != REF_NONE:
        reference_requirements["morphology_match_fields"] = ["species", *match_fields]

    return {
        "registry_contract_version": MOTION_REGISTRY_CONTRACT_VERSION,
        "roles": {
            # start_from_derived 모션(RETURN_HOME)은 역할 시작 키프레임이 없다 —
            # 시작이 런별 파생 자산이므로 required_start_roles 는 비어 있다.
            "required_start_roles": [spec.start_keyframe_role] if spec.start_keyframe_role else [],
            "required_end_roles": required_end_roles,
            "optional_end_roles": optional_end_roles,
        },
        "duration": {
            "range_sec": [float(spec.duration_range_sec[0]), float(spec.duration_range_sec[1])],
        },
        "loopability": {
            "loopable": bool(spec.loopable),
        },
        "interruptibility": {
            "interruptible": bool(spec.interruptible),
        },
        "morphology": {
            "sensitivity": _MORPHOLOGY_SENSITIVITY.get(spec.motion_id, "MEDIUM"),
            "constraint_mode": "structural_only",
            "confidence_floor": _morphology_confidence_floor(spec),
            "match_fields": match_fields,
            "required_profile_fields": [
                "species",
                *match_fields,
            ]
            if spec.motion_reference_policy != REF_NONE or spec.motion_class == CLASS_LOCOMOTION
            else ["species"],
            "forbidden_logic": ["breed_specific_logic"],
        },
        "reference": reference_requirements,
        "provider_capabilities": {
            "required_all": provider_required_all,
            "preferred_any": provider_preferred_any,
            "degrade_allowed": degrade_allowed,
            "degrade_to_strategy": degrade_target,
        },
        "qa": _class_default_qa(spec),
    }


# Only these beta motions change behavior. Other entries get an explicit legacy
# acceptance snapshot, preserving their prompts, authority and delivery policy.
_BETA_ACCEPTANCE = {
    "BREATHING": {
        "description": "The pet remains at HOME_IDLE with subtle, believable living motion.",
        "expected_start_state": "HOME_IDLE: the approved home standing pose.",
        "expected_end_state": "HOME_IDLE: compatible with repeating this idle loop.",
        "blocking_criteria": [
            "Clearly broken idle behavior, significant positional drift, or an unusable loop discontinuity.",
            "Do not require exaggerated chest movement or a perfectly periodic breathing cycle.",
        ],
        "advisory_criteria": [
            "Small rhythm variations, minor fur movement, subtle posture adjustments, slight asymmetry and minor temporal imperfections.",
        ],
        "check_authority": {"vlm_motion": AUTHORITY_INTEGRITY_HARD},
    },
    "COME_CLOSER": {
        "description": (
            "The pet visibly approaches the viewer. Walking, trotting, running or short bounding approaches are acceptable. "
            "Turning before approaching and standing still after arrival are acceptable. "
            "requested_motion_occurs assesses meaningful approach, not a running gait."
        ),
        "expected_start_state": "HOME_IDLE: the pet starts at its home position.",
        "expected_end_state": (
            "CLOSE_POSITION: ideally visibly closer than at the beginning. Meaningful approach with uncertain final distance "
            "is advisory; it is not proof of failed motion or a critical state violation."
        ),
        "blocking_criteria": ["Clearly no meaningful approach or clearly incorrect travel direction."],
        "advisory_criteria": [
            "Imperfect gait, unidentifiable footfalls, short reorientation, minor speed changes, brief partial paw cropping, "
            "slightly irregular scale progression, uncertain final distance, and a stationary final pose.",
        ],
        "check_authority": {"vlm_locomotion_form": AUTHORITY_QUALITY_ADVISORY},
    },
    "RETURN_HOME": {
        "description": "The pet moves back toward its home location and settles into a pose compatible with BREATHING.",
        "endpoint_reference_required": True,
        "expected_start_state": "CLOSE_POSITION: the usable terminal frame of the successful COME_CLOSER clip.",
        "expected_end_state": (
            "HOME_IDLE: a calm home pose compatible with BREATHING. Exact pixel, framing or pose matching is not required. "
            "A demonstrably significant endpoint mismatch that disrupts idle continuity is a critical state violation; "
            "insufficient evidence of compatibility is unknown, not a demonstrated violation."
        ),
        "blocking_criteria": ["Clearly absent return movement or clearly incorrect return direction."],
        "advisory_criteria": ["Minor gait/speed variations, small framing or endpoint discrepancies, and minor settling movement."],
        "check_authority": {
            "vlm_locomotion_form": AUTHORITY_QUALITY_ADVISORY,
            # Pixel similarity is evidence, not proof of endpoint incompatibility.
            "starts_at_start_pose": AUTHORITY_QUALITY_ADVISORY,
            "reaches_target_pose": AUTHORITY_QUALITY_ADVISORY,
            "vlm_target_pose": AUTHORITY_QUALITY_ADVISORY,
        },
    },
}


def _acceptance_for(spec: MotionSpec) -> dict[str, Any]:
    beta = _BETA_ACCEPTANCE.get(spec.motion_id)
    if beta is None:
        return {"version": "motion-acceptance-legacy-v1", "motion_id": spec.motion_id,
                "description": spec.description}
    contract = deepcopy(beta)
    required = ["vlm_same_pet", "vlm_anatomy", "vlm_composition", "vlm_motion",
                "vlm_motion_integrity", "vlm_critical_state", "motion_temporal_integrity"]
    if spec.motion_class == CLASS_LOCOMOTION:
        required.append("vlm_direction_travel")
    contract.update({
        "version": MOTION_ACCEPTANCE_VERSION,
        "motion_id": spec.motion_id,
        "required_checks": required,
        "review_policy": MOTION_REVIEW_POLICY,
        "blocking_criteria": [
            "Significant identity inconsistency; severe anatomy deformation or demonstrably missing/broken body parts.",
            "Obvious teleportation, major temporal discontinuities, clearly sustained sliding/floating, or unusable output.",
            *contract["blocking_criteria"],
        ],
        "check_authority": {
            **dict.fromkeys(required, AUTHORITY_INTEGRITY_HARD),
            "vlm_final_position": AUTHORITY_QUALITY_ADVISORY,
            **contract["check_authority"],
        },
    })
    return contract


MOTIONS = {mid: replace(spec, qa_acceptance_spec=_acceptance_for(spec)) for mid, spec in MOTIONS.items()}

# authoritative registry 확장: 기존 모션 시스템을 유지하고 항목별 요구사항만 주입.
MOTIONS = {
    motion_id: replace(
        spec,
        motion_type=_MOTION_TYPES.get(motion_id, spec.motion_class),
        requirements=_requirements_for(spec),
    )
    for motion_id, spec in MOTIONS.items()
}

#: 결정론적 순서.
MOTION_ORDER: tuple[str, ...] = tuple(MOTIONS.keys())

#: 트리거 → 모션. 트리거는 몸의 움직임이 아니다 — 여기서 모션으로 해석된다.
#: 각 매핑은 레거시 클립의 실제 모션 내용(luma_prompts)과 일치한다:
#: TOUCH 클립=행복한 반응(쓰다듬기), VOICE 클립=고개 들어 귀 기울임, NFC 클립=반김.
TRIGGERS: dict[str, str] = {
    "TOUCH": "PET_HEAD",
    "VOICE": "LOOK_UP",
    "NFC": "HAPPY",
    "IDLE": BREATHING_HOME_STATE,  # 레거시 슬롯 — 홈 상태 모션
}

#: 기존 런타임 모션 id (레지스트리 무결성 검사용).
_EXISTING_RUNTIME_MOTIONS = set(IDLE_EVENTS) | set(PET_ACTIONS) | {BREATHING_HOME_STATE}


def get_motion(motion_id: str) -> Optional[MotionSpec]:
    return MOTIONS.get((motion_id or "").strip().upper())


def provider_order_for_class(motion_class: str) -> tuple[str, ...]:
    """클래스 기본 provider registry id 순서. 미등록 클래스는 fail-closed."""
    return tuple(MOTION_PROVIDER_ORDER_BY_CLASS.get((motion_class or "").strip().upper(), ()))


def provider_order_for_motion(motion_id: str) -> tuple[str, ...]:
    """Select one authoritative model; trailing legacy entries are never fallbacks."""
    normalized = (motion_id or "").strip().upper()
    spec = MOTIONS.get(normalized)
    if not spec:
        return ()
    override = MOTION_PROVIDER_ORDER_OVERRIDES.get(normalized)
    order = tuple(override) if override is not None else provider_order_for_class(spec.motion_class)
    model = configured_model_for_motion(normalized)
    return (model,) if model else order[:1]


def configured_model_for_motion(motion_id: str) -> Optional[str]:
    """env MOTION_MODEL_<MOTION_ID> > MOTION_MODEL_DEFAULTS. 미등록 모델은 과금 전에 실패."""
    import os

    from . import video_motion_providers

    normalized = (motion_id or "").strip().upper()
    env_name = f"{MOTION_MODEL_ENV_PREFIX}{normalized}"
    model = (os.getenv(env_name) or "").strip().lower() or MOTION_MODEL_DEFAULTS.get(normalized)
    if not model:
        return None
    if not video_motion_providers.is_registered_model(model):
        raise video_motion_providers.VideoProviderError(
            "UNKNOWN_LOGICAL_MODEL",
            f"{env_name}={model!r} 는 등록된 motion logical model 이 아닙니다 — "
            f"허용값: {', '.join(video_motion_providers.registered_logical_models())}",
        )
    return model


def motion_for_trigger(trigger_id: str) -> Optional[str]:
    return TRIGGERS.get((trigger_id or "").strip().upper())


def motions_for_keyframe_role(role: str) -> list[str]:
    """이 키프레임 역할을 (시작 또는 목표로) 재사용하는 모션들."""
    r = (role or "").strip().upper()
    return [
        m.motion_id
        for m in MOTIONS.values()
        if m.start_keyframe_role == r or m.target_keyframe_role == r
    ]


def motion_snapshot(spec: MotionSpec) -> dict[str, Any]:
    return {
        "motion_spec_version": MOTION_SPEC_VERSION,
        "registry_contract_version": MOTION_REGISTRY_CONTRACT_VERSION,
        "motion_id": spec.motion_id,
        "motion_class": spec.motion_class,
        "motion_type": spec.motion_type,
        "description": spec.description,
        "qa_acceptance_spec": deepcopy(spec.qa_acceptance_spec),
        "start_keyframe_role": spec.start_keyframe_role,
        "target_keyframe_role": spec.target_keyframe_role,
        "requires_target_keyframe": spec.requires_target_keyframe,
        "motion_reference_id": spec.motion_reference_id,
        "motion_reference_policy": spec.motion_reference_policy,
        "preferred_video_strategy": spec.preferred_video_strategy,
        "fallback_video_strategy": spec.fallback_video_strategy,
        "duration_range_sec": list(spec.duration_range_sec),
        "loopable": spec.loopable,
        "interruptible": spec.interruptible,
        "video_compat": dict(spec.video_compat),
        "provider_order": list(provider_order_for_motion(spec.motion_id)),
        "requirements": dict(spec.requirements),
    }


# ── 임포트 시 자기 검증 — 잘못된 스펙은 배포 전에 죽는다 ────────────────────
def _assert_registry_valid() -> None:
    assert set(MOTION_PROVIDER_ORDER_BY_CLASS) == set(MOTION_CLASSES), (
        "모든 모션 클래스에 provider 순서가 정확히 하나씩 필요하다"
    )
    for motion_class, order in MOTION_PROVIDER_ORDER_BY_CLASS.items():
        assert order, f"{motion_class}: provider 순서가 비어 있다"
        assert all(isinstance(provider_id, str) and provider_id.strip() for provider_id in order)
        assert len(order) == len(set(order)), f"{motion_class}: provider 순서 중복"
    for motion_id, order in MOTION_PROVIDER_ORDER_OVERRIDES.items():
        assert motion_id in MOTIONS, f"provider override 의 모션이 없다: {motion_id}"
        assert order, f"{motion_id}: provider override 가 비어 있다"
        assert len(order) == len(set(order)), f"{motion_id}: provider override 중복"

    for spec in MOTIONS.values():
        assert spec.motion_class in MOTION_CLASSES, spec.motion_id
        # start_from_derived 모션(RETURN_HOME)은 역할 시작 키프레임이 없다 — 시작이
        # 런별 파생 자산이므로 start_keyframe_role 은 None 이고 start_from_derived 가 설정된다.
        if spec.start_from_derived:
            assert spec.start_keyframe_role is None, (
                f"{spec.motion_id}: start_from_derived 와 start_keyframe_role 은 상호배타다"
            )
        else:
            assert spec.start_keyframe_role in KEYFRAME_ROLES, (
                f"{spec.motion_id}: 존재하지 않는 시작 키프레임 역할 {spec.start_keyframe_role}"
            )
        if spec.target_keyframe_role:
            assert spec.target_keyframe_role in KEYFRAME_ROLES, spec.motion_id
        if spec.requires_target_keyframe:
            assert spec.target_keyframe_role, spec.motion_id
        assert spec.motion_reference_policy in (REF_NONE, REF_PREFERRED, REF_REQUIRED)
        assert spec.motion_type, f"{spec.motion_id}: motion_type 누락"
        req = spec.requirements
        assert isinstance(req, dict), f"{spec.motion_id}: requirements 누락"
        for k in (
            "registry_contract_version",
            "roles",
            "duration",
            "loopability",
            "interruptibility",
            "morphology",
            "reference",
            "provider_capabilities",
            "qa",
        ):
            assert k in req, f"{spec.motion_id}: requirements.{k} 누락"
        roles = req.get("roles") or {}
        _expected_start_roles = [spec.start_keyframe_role] if spec.start_keyframe_role else []
        assert roles.get("required_start_roles") == _expected_start_roles, (
            f"{spec.motion_id}: requirements 시작 역할 불일치"
        )
        if spec.requires_target_keyframe:
            assert roles.get("required_end_roles") == [spec.target_keyframe_role], (
                f"{spec.motion_id}: requirements 목표 역할 불일치"
            )
        if spec.motion_class == CLASS_MICRO:
            # v6 정책: MICRO 는 클래스 전체가 고정 4.0s — 프로바이더 최소 미달
            # 요청(3s → Runway 계약 위반)이 스펙 단계에서 다시 생길 수 없게 한다.
            assert spec.duration_range_sec == (MICRO_DURATION_SEC, MICRO_DURATION_SEC), (
                f"{spec.motion_id}: MICRO 길이는 고정 {MICRO_DURATION_SEC}s 다"
            )
        business_qa = ((req.get("qa") or {}).get("business") or {})
        assert business_qa.get("version") == MOTION_QA_CONTRACT_VERSION, spec.motion_id
        assert business_qa.get("motion_class") == spec.motion_class, spec.motion_id
        assert business_qa.get("domains"), f"{spec.motion_id}: business QA domains 누락"
        authorities = business_qa.get("check_authority") or {}
        assert authorities, f"{spec.motion_id}: business QA authority 누락"
        assert set(authorities.values()) <= set(QA_AUTHORITY_CLASSES), (
            f"{spec.motion_id}: 알 수 없는 business QA authority"
        )
    # 트리거는 모션이 아니다 — id 충돌 금지. 트리거의 목적지는 유효한 모션이다.
    assert not (set(TRIGGERS) & set(MOTIONS)), "트리거 id 가 모션 id 와 겹친다"
    for target in TRIGGERS.values():
        assert target in MOTIONS, f"트리거가 없는 모션 {target} 을 가리킨다"
    # 기존 런타임 모션은 전부 등록돼 있다.
    missing = _EXISTING_RUNTIME_MOTIONS - set(MOTIONS)
    assert not missing, f"기존 런타임 모션 누락: {missing}"


_assert_registry_valid()


# ══════════════════════════════════════════════════════════════════════════
# Phase 6 계약 리졸버 — 읽기 전용, 결정론, 프로바이더 호출 없음
# ══════════════════════════════════════════════════════════════════════════


def _keyframe_payload(k: Any) -> dict[str, Any]:
    sel = next((c for c in k.candidates if c.selected), None)
    approved_evidence = None
    if sel:
        from . import action_keyframe_service, qa_evidence_reuse

        lineage = {
            "keyframe_id": k.id,
            "keyframe_version": k.version,
            "keyframe_candidate_id": sel.id,
            "canonical_version_id": k.canonical_version_id,
            "canonical_version": k.canonical_version,
        }
        approved_evidence = qa_evidence_reuse.approved_asset_evidence(
            source_stage="KEYFRAME",
            qa_result=sel.qa_result,
            lineage=lineage,
            expected_lineage=lineage,
            expected_qa_version=action_keyframe_service.KEYFRAME_QA_VERSION,
        )
    return {
        "role": k.keyframe_role,
        "keyframe_id": k.id,
        "version": k.version,
        "canonical_version_id": k.canonical_version_id,
        "candidate_id": (sel.id if sel else k.selected_candidate_id),
        "raw": (
            {"bucket": sel.raw_bucket if hasattr(sel, "raw_bucket") else None,
             "object_path": sel.raw_object_path}
            if sel
            else None
        ),
        "cutout": (
            {"bucket": getattr(sel, "cutout_bucket", None),
             "object_path": sel.cutout_object_path}
            if sel and sel.cutout_object_path
            else None
        ),
        # 하류(Phase 6)가 실제로 먹는 입력. raw 는 증거, plate 는 생성 입력이다.
        "plate": (
            {"bucket": getattr(sel, "plate_bucket", None),
             "object_path": getattr(sel, "plate_object_path", None)}
            if sel and getattr(sel, "plate_object_path", None)
            else None
        ),
        "approved_qa_evidence": approved_evidence,
    }


def _derived_start_payload(override: dict[str, Any], *, role_label: str) -> dict[str, Any]:
    """start_from_derived 모션(RETURN_HOME)의 시작 키프레임 페이로드를 **런별 파생
    자산**에서 구성한다 — 역할 키프레임이 아니다. _keyframe_payload 와 같은 형태지만
    raw 와 plate 가 모두 그 파생 object_path 를 가리킨다(하류 build_motion_video 가
    plate 를 생성 입력으로 먹는다). 키프레임 QA 증거는 없다(파생 프레임이므로)."""
    bucket = override.get("bucket")
    object_path = override.get("object_path")
    ref = {"bucket": bucket, "object_path": object_path}
    return {
        "role": role_label.upper(),
        "keyframe_id": override.get("reference_id") or f"derived:{role_label}",
        "version": override.get("version"),
        "canonical_version_id": override.get("canonical_version_id"),
        "candidate_id": override.get("reference_id"),
        "raw": ref,
        "cutout": None,
        "plate": dict(ref),
        "approved_qa_evidence": None,
        "derived": True,
    }


async def _approved_keyframe(user_id: str, pet_id: str, role: str):
    """complete 상태 + 선택 후보가 있는 키프레임만. REVIEW 는 조용히 쓰지 않는다."""
    from . import action_keyframe_service

    try:
        k = await action_keyframe_service.get_keyframe(
            user_id=user_id, pet_id=pet_id, keyframe_role=role
        )
    except action_keyframe_service.ActionKeyframeError as e:
        raise MotionSpecError(e.code, e.message, status=e.status) from e
    if not k or k.status != action_keyframe_service.STATUS_COMPLETE or not k.selected_candidate_id:
        return None
    return k


async def resolve_video_generation_spec(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    morphology_overrides: Optional[dict[str, str]] = None,
    desired_view: Optional[str] = None,
    direction: Optional[str] = None,
    speed: Optional[str] = None,
    start_image_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Phase 6 의 정본 입력. 실패는 명시적이다:
      * 모르는 모션            → 422 UNKNOWN_MOTION
      * 승인된 시작 키프레임 없음 → 409 KEYFRAME_REQUIRED (어느 역할인지 함께)
      * 필수 목표 키프레임 없음  → 409 TARGET_KEYFRAME_REQUIRED
      * 모션 레퍼런스 없음       → 같은 모델의 프롬프트 조건으로 대체 + 경고
    Phase 6.6: 레퍼런스는 종+형태 프로필로 라이브러리에서 해석된다. 종 교차 없음.
    """
    spec = get_motion(motion_id)
    if not spec:
        raise MotionSpecError(
            "UNKNOWN_MOTION", f"모르는 모션입니다: {motion_id}", status=422
        )

    warnings: list[str] = []

    # ── 펫 모션/형태 프로필 (Phase 6.6/Phase 5) — 신원이 아니라 구조 속성만 ─────
    from . import (
        canonical_pet_service,
        motion_reference_service,
        pet_identity_service,
        pet_morphology_service,
        pet_reference_set_service,
    )

    async def _resolve_keyframes():
        # 시작: 역할 키프레임 또는 런별 파생 자산(start_from_derived). 반환은
        # (start_payload, start_lineage, target_payload) — start_lineage 는 형태 프로필
        # 계보 조회(canonical_version)와 계약의 canonical_version_id 에만 쓴다.
        if spec.start_from_derived:
            if not start_image_override or not start_image_override.get("object_path"):
                raise MotionSpecError(
                    "DERIVED_START_REQUIRED",
                    f"{spec.motion_id} 은 런별 파생 시작 이미지({spec.start_from_derived})가 "
                    "필요합니다 — start_image_override 가 전달되지 않았습니다.",
                    status=409,
                )
            start_payload = _derived_start_payload(
                start_image_override, role_label=spec.start_from_derived
            )
            start_lineage = SimpleNamespace(
                canonical_version_id=start_image_override.get("canonical_version_id"),
                canonical_version=start_image_override.get("canonical_version"),
            )
        else:
            start = await _approved_keyframe(user_id, pet_id, spec.start_keyframe_role)
            if not start:
                raise MotionSpecError(
                    "KEYFRAME_REQUIRED",
                    f"승인된 {spec.start_keyframe_role} 키프레임이 필요합니다 — 먼저 빌드/승인하세요.",
                    status=409,
                )
            start_payload = _keyframe_payload(start)
            start_lineage = start
        target_payload = None
        if spec.target_keyframe_role:
            target = await _approved_keyframe(user_id, pet_id, spec.target_keyframe_role)
            if target:
                target_payload = _keyframe_payload(target)
            elif spec.requires_target_keyframe:
                raise MotionSpecError(
                    "TARGET_KEYFRAME_REQUIRED",
                    f"전이 모션 {spec.motion_id} 은 {spec.target_keyframe_role} 키프레임이 필요합니다.",
                    status=409,
                )
        return start_payload, start_lineage, target_payload

    async def _resolve_identity():
        try:
            return await pet_identity_service.get_profile(user_id=user_id, pet_id=pet_id)
        except pet_identity_service.PetIdentityError:
            return None  # 프로필 조회 실패 → 프로필 UNKNOWN → 레퍼런스 미해석 (LEVEL_4)

    # 키프레임 계보(순서/예외 의존)와 신원 프로필(완전 독립, 예외 없음)을
    # 동시에 가져온다 — _resolve_identity 는 절대 예외를 내지 않으므로
    # gather 가 내는 예외는 항상 _resolve_keyframes 쪽 그대로다 (에러 코드/
    # 순서 불변).
    (start_payload, start_lineage, target_payload), identity_profile = await asyncio.gather(
        _resolve_keyframes(), _resolve_identity()
    )

    # ── 형태 프로필 계보 ────────────────────────────────────────────────────
    #
    # 이 키프레임을 만든 레퍼런스 세트가 형태 프로필 버전을 **핀으로 박아** 두면,
    # 그 버전이 이 모션의 유일한 답이다. 예전에는 핀을 못 읽었을 때 조용히
    # `get_profile(version=None)` — 즉 **최신 프로필** — 로 떨어졌다. 같은
    # 키프레임을 같은 계약으로 다시 돌려도 그 사이 프로필이 한 번 다시 빌드되면
    # 다른 형태로 생성됐고, 기록에는 "핀됨"이라고 남았다. 재현되지 않는 계보다.
    #
    # 이제 핀이 **선언된** 경우, 그 버전을 못 읽으면 형태는 UNKNOWN 이다(None).
    # 최신으로 대신하지 않는다 — 다른 아이의 몸을 빌려 쓰는 것과 같다.
    # 최신을 보는 것은 핀이 애초에 없는 계보(핀 이전 자산)뿐이다.
    pinned_morphology = None
    morphology_pin_declared = False
    morphology_pin_version: Optional[int] = None
    #: 이 키프레임 계보의 정본이 고른 배경 (pet_background) — 모션 단계가 플레이트를
    #: 다시 만들어야 할 때 같은 색을 쓰도록 계약에 실어 보낸다. 다시 고르지 않는다.
    lineage_background: Optional[dict[str, Any]] = None
    try:
        canonical = None
        if getattr(start_lineage, "canonical_version", None):
            canonical = await canonical_pet_service.get_canonical(
                user_id=user_id,
                pet_id=pet_id,
                version=int(getattr(start_lineage, "canonical_version")),
            )
        if canonical is not None:
            from . import pet_background

            lineage_background = pet_background.from_output_spec(
                getattr(canonical, "output_spec", None)
            )
        if canonical and canonical.reference_set_version:
            refset = await pet_reference_set_service.get_set(
                user_id=user_id,
                pet_id=pet_id,
                version=int(canonical.reference_set_version),
            )
            if refset and refset.morphology_profile_version:
                morphology_pin_declared = True
                morphology_pin_version = int(refset.morphology_profile_version)
                pinned_morphology = await pet_morphology_service.get_profile(
                    user_id=user_id,
                    pet_id=pet_id,
                    version=morphology_pin_version,
                )
        if pinned_morphology is None and not morphology_pin_declared:
            pinned_morphology = await pet_morphology_service.get_profile(
                user_id=user_id,
                pet_id=pet_id,
            )
    except (
        canonical_pet_service.CanonicalPetError,
        pet_reference_set_service.PetReferenceSetError,
        pet_morphology_service.PetMorphologyError,
        ValueError,
        TypeError,
    ):
        # 계보를 확인하지 못했다 — 무엇이 핀돼 있었는지도 모른다. 최신으로
        # 메우지 않는다.
        pinned_morphology = None

    if morphology_pin_declared and pinned_morphology is None:
        warnings.append(
            f"pinned morphology profile v{morphology_pin_version} unavailable — "
            "morphology treated as UNKNOWN (latest profile NOT substituted)"
        )

    confidence_floor = (
        ((spec.requirements or {}).get("morphology") or {}).get("confidence_floor") or "medium"
    )
    pet_motion_profile = motion_reference_service.derive_motion_profile(
        identity_profile,
        overrides=morphology_overrides,
        morphology_profile=pinned_morphology,
        confidence_floor=str(confidence_floor),
    )

    strategy = spec.preferred_video_strategy
    motion_reference = None
    if spec.motion_reference_policy != REF_NONE:
        resolved = await motion_reference_service.resolve_motion_reference(
            profile=pet_motion_profile,
            motion_id=spec.motion_id,
            pet_id=pet_id,
            desired_view=(desired_view or spec.preferred_camera_view),
            direction=(direction or spec.preferred_travel_direction),
            speed=speed,
            motion_requirements=spec.requirements,
        )
        if resolved:
            motion_reference = {
                "id": resolved["reference_key"],
                "policy": spec.motion_reference_policy,
                **resolved,
            }
        else:
            # 미해석 — v1 형태 유지 (id = 스펙의 레거시 라벨, asset 없음).
            motion_reference = {
                "id": spec.motion_reference_id,
                "policy": spec.motion_reference_policy,
                "asset": None,
                "resolution": "unresolved",
            }
            warnings.append(
                f"motion_reference {spec.motion_reference_id} unavailable — "
                "using prompt conditioning with the assigned model"
            )
            strategy = STRATEGY_I2V

    return {
        "pet_motion_profile": pet_motion_profile,
        "contract_version": PHASE6_CONTRACT_VERSION,
        "registry_contract_version": MOTION_REGISTRY_CONTRACT_VERSION,
        "motion_spec_version": MOTION_SPEC_VERSION,
        "motion_id": spec.motion_id,
        "motion_class": spec.motion_class,
        "motion_type": spec.motion_type,
        "start_keyframe": start_payload,
        "target_keyframe": target_payload,
        "motion_reference": motion_reference,
        "video_strategy": strategy,
        "conditioning_fallbacks": (
            ["supports_motion_reference"]
            if spec.preferred_video_strategy == STRATEGY_I2V_MOTION_REF and strategy == STRATEGY_I2V
            else []
        ),
        # 루프 MICRO 아이들: 시작 키프레임을 양끝으로 쓸 자격이 있는지(provider 능력
        # 과 무관한 **스펙상** 자격). 실제 START_END 승격/폴백은 build 단계가 정한다.
        "loop_same_start_end": is_loop_same_start_end(spec),
        "loopable": spec.loopable,
        "interruptible": spec.interruptible,
        "duration_range_sec": list(spec.duration_range_sec),
        "video_compat": dict(spec.video_compat),
        "provider_order": list(provider_order_for_motion(spec.motion_id)),
        "requirements": dict(spec.requirements),
        "qa_context": {
            "acceptance": deepcopy(spec.qa_acceptance_spec),
            "expected_direction": direction or spec.preferred_travel_direction,
            "interaction_type": spec.video_compat.get("interaction"),
            "allow_generated_hand": bool(spec.video_compat.get("allow_generated_hand")),
        },
        "canonical_version_id": getattr(start_lineage, "canonical_version_id", None),
        "background": lineage_background,
        "warnings": warnings,
    }
