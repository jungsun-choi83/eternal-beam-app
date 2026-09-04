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
모션은 시작(및 전이면 목표) 키프레임 **역할**만 가리킨다. NEUTRAL_IDLE 하나가
호흡/깜빡임/귀/머리/꼬리 모션 전부를 감당한다 — 불필요한 스틸을 만들지 않는다.

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

from dataclasses import dataclass, field
from typing import Any, Optional

from ..scenarios.pet_scenarios import IDLE_EVENTS, PET_ACTIONS
from .action_keyframe_spec import BREATHING_HOME_STATE, KEYFRAME_ROLES

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
MOTION_SPEC_VERSION = "motion-spec-v5"
# v2 (Phase 6.6): pet_motion_profile 추가 + motion_reference 가 라이브러리에서
# 해석된 실제 자산/버전/호환성/출처를 담는다 (미해석 시 기존 v1 형태 + 경고 유지).
PHASE6_CONTRACT_VERSION = "phase6-contract-v2"

CLASS_MICRO = "MICRO"
CLASS_TRANSITION = "TRANSITION"
CLASS_LOCOMOTION = "LOCOMOTION"
CLASS_INTERACTION = "INTERACTION"
MOTION_CLASSES = (CLASS_MICRO, CLASS_TRANSITION, CLASS_LOCOMOTION, CLASS_INTERACTION)

STRATEGY_I2V = "IMAGE_TO_VIDEO"
STRATEGY_START_END = "START_END_FRAME"
STRATEGY_I2V_MOTION_REF = "IMAGE_TO_VIDEO_WITH_MOTION_REF"

REF_NONE = "none"
REF_PREFERRED = "preferred"
REF_REQUIRED = "required"


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
    #: 사람이 읽는 모션 서술 (Phase 6 프롬프트의 씨앗이 아니라 문서다).
    description: str
    start_keyframe_role: str
    target_keyframe_role: Optional[str] = None
    requires_target_keyframe: bool = False
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


def _micro(motion_id: str, desc: str, role: str, *, loopable: bool = False,
           duration: tuple[float, float] = (3.0, 6.0)) -> MotionSpec:
    return MotionSpec(
        motion_id=motion_id, motion_class=CLASS_MICRO, description=desc,
        start_keyframe_role=role, preferred_video_strategy=STRATEGY_I2V,
        duration_range_sec=duration, loopable=loopable,
        # MICRO 는 시작 포즈로 되돌아온다 — 기존 seam-aligned 반환 계약과 일치.
        video_compat={"returns_to_start_pose": True, "motion_scale": "micro"},
    )


MOTIONS: dict[str, MotionSpec] = {
    # ── MICRO — 기존 런타임 모션 id 그대로 ──────────────────────────────
    BREATHING_HOME_STATE: _micro(
        BREATHING_HOME_STATE,
        # 홈 상태 루프 — 서술은 프롬프트와 VLM QA(requested_motion_occurs) 양쪽이
        # 소비한다. 두 극단을 모두 피한다: "잔잔히"만으로는 정지화면(라이브 v5),
        # "clearly visible + 상체 오르내림"은 전신 줌 펄스(라이브 v1/v2 실측).
        # 가시성 하한("gently but perceptibly", 2~3초당 1회)은 유지하되 운동을
        # 흉곽으로 국소화하고 전신 스케일/프레이밍 변화를 명시적으로 금지한다.
        "calm natural resting breathing. The chest wall and ribcage gently but "
        "perceptibly expand during each inhale and relax during each exhale, in a "
        "slow natural rhythm of about one full breath every 2 to 3 seconds. The "
        "breathing motion is visible only at the chest, ribcage and flank — only "
        "that local body contour changes. The pet's overall size, outline, "
        "position and framing stay exactly constant: no whole-body pulsing, no "
        "zooming or scaling, no vertical bobbing of the head or torso, no swaying, "
        "no translation. Head, ears, legs, paws and tail stay still",
        "NEUTRAL_IDLE", loopable=True, duration=(4.0, 6.0),
    ),
    "BLINKING": _micro("BLINKING", "자연스러운 눈 깜빡임 1~2회", "NEUTRAL_IDLE"),
    "EAR_TWITCHING": _micro("EAR_TWITCHING", "귀 움찔거림", "NEUTRAL_IDLE"),
    "HEAD_TILTING": _micro("HEAD_TILTING", "호기심 어린 고개 갸웃", "NEUTRAL_IDLE"),
    "TAIL_WAGGING": _micro("TAIL_WAGGING", "부드러운 꼬리 흔들기", "NEUTRAL_IDLE"),
    # ── MICRO — 새 모션 (기존 어디에도 없던 것만 새 id) ─────────────────
    "LOOK_UP": _micro("LOOK_UP", "위를 올려다보고 되돌아오기", "LOOK_UP"),
    "HAPPY": _micro("HAPPY", "반가운 알림 반응 — 귀 쫑긋, 밝은 표정, 가벼운 몸짓", "HAPPY"),
    "LIE_IDLE": _micro("LIE_IDLE", "엎드린 채 잔잔히 쉬기", "LIE", loopable=True),
    "SLEEP_BREATH": _micro("SLEEP_BREATH", "잠든 채 고른 숨쉬기", "SLEEP", loopable=True, duration=(4.0, 6.0)),
    # ── TRANSITION — 시작+목표 쌍 명시 ──────────────────────────────────
    "LIE_DOWN": MotionSpec(
        motion_id="LIE_DOWN", motion_class=CLASS_TRANSITION,
        description="선/앉은 자세에서 자연스럽게 엎드리기",
        start_keyframe_role="NEUTRAL_IDLE", target_keyframe_role="LIE",
        requires_target_keyframe=True, preferred_video_strategy=STRATEGY_START_END,
        duration_range_sec=(2.5, 5.0), loopable=False,
        video_compat={"returns_to_start_pose": False, "motion_scale": "body"},
    ),
    "STAND_UP": MotionSpec(
        motion_id="STAND_UP", motion_class=CLASS_TRANSITION,
        description="엎드린 자세에서 일어나기",
        start_keyframe_role="LIE", target_keyframe_role="NEUTRAL_IDLE",
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
        description="카메라 쪽으로 다가오기 (기존 프리미엄 액션)",
        start_keyframe_role="NEUTRAL_IDLE",
        motion_reference_id="DOG_APPROACH", motion_reference_policy=REF_PREFERRED,
        preferred_video_strategy=STRATEGY_I2V_MOTION_REF,
        fallback_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
        preferred_camera_view="FRONT",
        preferred_travel_direction="TOWARD_CAMERA",
    ),
    "RUN": MotionSpec(
        motion_id="RUN", motion_class=CLASS_LOCOMOTION,
        description="신나게 달리기 (미래 모션)",
        start_keyframe_role="NEUTRAL_IDLE",
        motion_reference_id="DOG_RUN", motion_reference_policy=REF_PREFERRED,
        preferred_video_strategy=STRATEGY_I2V_MOTION_REF,
        fallback_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 6.0),
        video_compat={"returns_to_start_pose": False, "motion_scale": "locomotion"},
    ),
    "WALK": MotionSpec(
        motion_id="WALK", motion_class=CLASS_LOCOMOTION,
        description="자연스러운 걸음걸이 (미래 모션)",
        start_keyframe_role="NEUTRAL_IDLE",
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
        description="머리를 쓰다듬을 때의 반응 — interaction-ready 포즈에서 시작; "
        "손의 등장 여부는 Phase 6 이 결정한다",
        start_keyframe_role="NEUTRAL_IDLE",
        preferred_video_strategy=STRATEGY_I2V,
        duration_range_sec=(3.0, 5.0),
        video_compat={"returns_to_start_pose": True, "motion_scale": "micro",
                      "interaction": "head_touch", "requires_human_in_keyframe": False,
                      # Phase 6: 영상 단계에서 부드러운 손이 화면 밖에서 들어와도 된다.
                      "allow_generated_hand": True},
    ),
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
        "motion_id": spec.motion_id,
        "motion_class": spec.motion_class,
        "description": spec.description,
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
    }


# ── 임포트 시 자기 검증 — 잘못된 스펙은 배포 전에 죽는다 ────────────────────
def _assert_registry_valid() -> None:
    for spec in MOTIONS.values():
        assert spec.motion_class in MOTION_CLASSES, spec.motion_id
        assert spec.start_keyframe_role in KEYFRAME_ROLES, (
            f"{spec.motion_id}: 존재하지 않는 시작 키프레임 역할 {spec.start_keyframe_role}"
        )
        if spec.target_keyframe_role:
            assert spec.target_keyframe_role in KEYFRAME_ROLES, spec.motion_id
        if spec.requires_target_keyframe:
            assert spec.target_keyframe_role, spec.motion_id
        assert spec.motion_reference_policy in (REF_NONE, REF_PREFERRED, REF_REQUIRED)
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
) -> dict[str, Any]:
    """
    Phase 6 의 정본 입력. 실패는 명시적이다:
      * 모르는 모션            → 422 UNKNOWN_MOTION
      * 승인된 시작 키프레임 없음 → 409 KEYFRAME_REQUIRED (어느 역할인지 함께)
      * 필수 목표 키프레임 없음  → 409 TARGET_KEYFRAME_REQUIRED
      * 선호 모션 레퍼런스 없음  → 실패가 아니라 경고 + 폴백 전략
      * 필수 모션 레퍼런스 없음  → 409 MOTION_REFERENCE_REQUIRED
    Phase 6.6: 레퍼런스는 종+형태 프로필로 라이브러리에서 해석된다. 종 교차 없음.
    """
    spec = get_motion(motion_id)
    if not spec:
        raise MotionSpecError(
            "UNKNOWN_MOTION", f"모르는 모션입니다: {motion_id}", status=422
        )

    warnings: list[str] = []

    start = await _approved_keyframe(user_id, pet_id, spec.start_keyframe_role)
    if not start:
        raise MotionSpecError(
            "KEYFRAME_REQUIRED",
            f"승인된 {spec.start_keyframe_role} 키프레임이 필요합니다 — 먼저 빌드/승인하세요.",
            status=409,
        )

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

    # ── 펫 모션/형태 프로필 (Phase 6.6) — 신원이 아니라 구조 속성만 ─────────
    from . import motion_reference_service, pet_identity_service

    identity_profile = None
    try:
        identity_profile = await pet_identity_service.get_profile(
            user_id=user_id, pet_id=pet_id
        )
    except pet_identity_service.PetIdentityError:
        pass  # 프로필 조회 실패 → 프로필 UNKNOWN → 레퍼런스 미해석 (LEVEL_4)
    pet_motion_profile = motion_reference_service.derive_motion_profile(
        identity_profile, overrides=morphology_overrides
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
            if spec.motion_reference_policy == REF_REQUIRED:
                raise MotionSpecError(
                    "MOTION_REFERENCE_REQUIRED",
                    f"{spec.motion_id} 은 모션 레퍼런스가 필수지만 호환 레퍼런스가 없습니다.",
                    status=409,
                )
            if spec.fallback_video_strategy:
                warnings.append(
                    f"motion_reference {spec.motion_reference_id} unavailable — "
                    f"falling back to {spec.fallback_video_strategy}"
                )
                strategy = spec.fallback_video_strategy

    return {
        "pet_motion_profile": pet_motion_profile,
        "contract_version": PHASE6_CONTRACT_VERSION,
        "motion_spec_version": MOTION_SPEC_VERSION,
        "motion_id": spec.motion_id,
        "motion_class": spec.motion_class,
        "start_keyframe": _keyframe_payload(start),
        "target_keyframe": target_payload,
        "motion_reference": motion_reference,
        "video_strategy": strategy,
        "loopable": spec.loopable,
        "interruptible": spec.interruptible,
        "duration_range_sec": list(spec.duration_range_sec),
        "video_compat": dict(spec.video_compat),
        "canonical_version_id": start.canonical_version_id,
        "warnings": warnings,
    }
