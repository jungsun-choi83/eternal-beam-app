"""
모션 비디오 빌더 (Reference-locked Video Generation, Phase 6).

── 파이프라인 ──────────────────────────────────────────────────────────────
resolve_video_generation_spec (Phase 5.1 — 승인 키프레임 게이트 포함) →
  1. 클래스별 프로바이더 라우팅 (video_motion_providers.routing_for_class).
     START_END_FRAME 인데 프로바이더가 end frame 을 못 받으면 **라우팅 실패**다 —
     start-only 로 조용히 강등하지 않는다.
  2. 라이브 안전 게이트 (mock / allowlist / all) — 과금 전, 행 기록 전.
  3. 명시적 출력 사양: 9:16 · 720p · 스펙 duration 범위 중앙값 · audio off ·
     camera fixed. 프로바이더 기본값에 기대지 않는다 (Wan 16:9 사고 재발 방지).
  4. Phase 4/5 후보 생명주기: 버전 행 먼저 → 후보 raw 저장 → QA → 판정.
  5. 프레임 샘플링 QA (0/12.5/.../87.5/true-last) + VLM 확언. FAIL 은 절대 승격 불가.
  6. 선택 → 대장 기록(role='generated', kind motion_raw) → 불변 버전.

프로덕션 Luma/Wan·테마·크레딧·디바이스는 이 모듈과 무관하다. Pet Action Library
승격은 이후 단계의 명시적 작업이다.
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

MOTION_BUILDER_VERSION = "motion-video-builder-v1"

STATUS_BUILDING = "building"
STATUS_COMPLETE = "complete"
STATUS_REVIEW = "review"
STATUS_FAILED = "failed"

GENERATED_KIND_MOTION = "motion_raw"


class MotionVideoError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _versions_table() -> str:
    return os.getenv("PET_MOTION_VERSIONS_TABLE", "pet_motion_versions")


def _candidates_table() -> str:
    return os.getenv("PET_MOTION_CANDIDATES_TABLE", "pet_motion_candidates")


_MOCK_VERSIONS: list[dict[str, Any]] = []
_MOCK_CANDIDATES: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_VERSIONS.clear()
    _MOCK_CANDIDATES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)))
        )
    except ValueError:
        return default


#: 모션 클래스별 기본 시도 상한 (점진적 조기 중단 — Phase 7 최적화).
#:
#: MICRO(눈 깜빡임류)는 짧고 저렴하며 실패 시 놓치는 가치도 작다 — 낮은 상한.
#: LOCOMOTION(이동)은 실패율이 높고 레퍼런스 조건화 폴백(I2V_MOTION_REF→I2V,
#: 프로바이더 폴백)이 실제로 쓰인다 — 높은 상한. 프롬프트/스펙은 "무엇을
#: 만들 것인가"만 정하고, 몇 번 더 과금할지는 여기(정책)가 정한다.
_CLASS_PRIMARY_DEFAULTS: dict[str, int] = {
    "MICRO": 2,
    "TRANSITION": 3,
    "INTERACTION": 3,
    "LOCOMOTION": 4,
}
_CLASS_FALLBACK_DEFAULTS: dict[str, int] = {
    "MICRO": 1,
    "TRANSITION": 2,
    "INTERACTION": 2,
    "LOCOMOTION": 2,
}


def _tier_limit(base_env: str, motion_class: str, class_defaults: dict[str, int], hard_default: int) -> int:
    """
    시도 상한 해석 — 우선순위:

        {base_env}_{CLASS}  (클래스별 명시 env, 예: PHASE6_MAX_PRIMARY_LOCOMOTION)
      > {base_env}          (전역 명시 env — 기존 변수, 의미 유지)
      > 1                   (PHASE6_GENERATION_PROFILE=test — 로컬 개발은 1회면 충분)
      > 클래스 기본값
      > {hard_default}
    """
    cls = (motion_class or "").strip().upper()
    class_env = os.getenv(f"{base_env}_{cls}") if cls else None
    if class_env is not None and class_env.strip():
        return _int_env(f"{base_env}_{cls}", hard_default)
    if (os.getenv(base_env) or "").strip():
        return _int_env(base_env, hard_default)
    if generation_profile()[0] == "test":
        return 1
    return class_defaults.get(cls, hard_default)


def candidate_policy(motion_class: Optional[str] = None) -> dict[str, int]:
    """
    비디오 후보 상한 — 클래스 인지 + env 조정 가능.

    조기 중단은 기본 1 (첫 PASS 에서 즉시 멈춘다). 루프는 후보 N 의 QA 가 끝난
    뒤에만 N+1 을 제출하므로 "3개를 만들어 놓고 평가"는 구조적으로 없다 — 이
    정책은 실패가 이어질 때 몇 번까지 더 과금할지만 정한다.
    """
    return {
        "max_primary": _tier_limit("PHASE6_MAX_PRIMARY", motion_class or "", _CLASS_PRIMARY_DEFAULTS, 3),
        "max_fallback": _tier_limit("PHASE6_MAX_FALLBACK", motion_class or "", _CLASS_FALLBACK_DEFAULTS, 2),
        "stop_after_passes": _int_env("PHASE6_STOP_AFTER_PASSES", 1),
    }


# ── 생성 프로파일 (Phase 6.5 비용 절약) — 해상도/길이의 **단일 정본** ─────────
# PHASE6_GENERATION_PROFILE = test | benchmark | production (기본 benchmark).
#   test        480p + 클래스별 짧은 길이 — 프롬프트/QA/라우팅 검증용 (저비용)
#   benchmark   720p + 스펙 범위 중앙값 — 기존 동작 그대로 (기본값)
#   production  최종 프로덕션 설정 자리 — 현재는 benchmark 와 동일 (아직 미조정)
# 길이는 절대 모션 스펙 최소 아래로 내려가지 않는다: 프로파일 목표가 스펙
# 최소보다 짧으면 최소를 유지하고 경고로 보고한다 (예: BREATHING 최소 4s).
# 종횡비(9:16)·오디오 off·카메라 고정은 프로파일과 무관하게 불변이다.
GENERATION_PROFILES: dict[str, dict[str, Any]] = {
    "test": {
        "resolution": "480p",
        # MICRO 3 → 4 (motion-spec-v6): Runway seedance2_5 최소가 4s 라 3s 목표는
        # 스펙 최소 클램프에 걸리거나(BREATHING) 계약 위반 제출이 됐다(TAIL_WAGGING
        # 라이브 실측). 스펙도 MICRO 고정 4s 라 이제 목표와 스펙이 일치한다.
        "duration_by_class": {"MICRO": 4, "INTERACTION": 4, "TRANSITION": 4, "LOCOMOTION": 4},
    },
    "benchmark": {"resolution": "720p", "duration_by_class": None},
    "production": {"resolution": "720p", "duration_by_class": None},
}


def generation_profile() -> tuple[str, dict[str, Any]]:
    name = os.getenv("PHASE6_GENERATION_PROFILE", "benchmark").strip().lower()
    if name not in GENERATION_PROFILES:
        return "benchmark", GENERATION_PROFILES["benchmark"]
    return name, GENERATION_PROFILES[name]


def build_output_spec(
    *, duration_range: Sequence[float], motion_class: Optional[str] = None
) -> tuple[dict[str, Any], list[str]]:
    """명시적 출력 사양 + 프로파일 경고. 프로바이더 기본값에 기대지 않는다."""
    profile_name, profile = generation_profile()
    lo, hi = (duration_range or [3.0, 6.0])[:2]
    lo, hi = float(lo), float(hi)

    warnings: list[str] = []
    if os.getenv("PHASE6_GENERATION_PROFILE", "benchmark").strip().lower() not in GENERATION_PROFILES:
        warnings.append(
            f"unknown PHASE6_GENERATION_PROFILE — falling back to '{profile_name}'"
        )

    by_class = profile.get("duration_by_class") or {}
    target = by_class.get((motion_class or "").upper())
    if target is None:
        duration = int(round((lo + hi) / 2))
    else:
        duration = int(round(min(max(float(target), lo), hi)))
        if float(target) < lo:
            # 모션이 자연스럽게 완료될 수 없는 길이로 줄이지 않는다 — 최소 유지 + 보고.
            warnings.append(
                f"profile '{profile_name}' duration {target}s below spec minimum "
                f"{lo}s for {motion_class} — spec minimum preserved"
            )

    return (
        {
            "aspect_ratio": os.getenv("PHASE6_ASPECT_RATIO", "9:16"),  # 프로파일 불변
            # 명시적 env 가 프로파일보다 우선한다 (운영자 오버라이드).
            "resolution": os.getenv("PHASE6_RESOLUTION") or profile["resolution"],
            "duration_sec": duration,
            "audio": False,       # 펫 모션 자산 — 생성 오디오 금지 (프로파일 불변)
            "camera_fixed": True,  # 프로파일 불변
            "profile": profile_name,
        },
        warnings,
    )


def default_output_spec(duration_range: Sequence[float]) -> dict[str, Any]:
    """하위 호환 별칭 — 클래스 무관 사양 (스펙 중앙값)."""
    return build_output_spec(duration_range=duration_range)[0]


def analyzer_versions(providers: Sequence[Any]) -> dict[str, Any]:
    from . import motion_spec, motion_video_prompts, motion_video_qa, vlm_identity

    return {
        "motion_builder": MOTION_BUILDER_VERSION,
        "motion_spec": motion_spec.MOTION_SPEC_VERSION,
        "contract": motion_spec.PHASE6_CONTRACT_VERSION,
        "prompt": motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION,
        "qa": motion_video_qa.MOTION_VIDEO_QA_VERSION,
        "sampling": motion_video_qa.FRAME_SAMPLING_VERSION,
        "vlm_motion_qa": vlm_identity.VLM_MOTION_QA_VERSION,
        "providers": [f"{p.name}:{p.model_name()}" for p in providers],
    }


# ══════════════════════════════════════════════════════════════════════════
# 데이터 모델 (Phase 4/5 와 같은 형태)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MotionCandidate:
    id: str
    motion_version_id: str
    provider: str
    attempt: int
    decision: str
    model: Optional[str] = None
    provider_job_id: Optional[str] = None
    start_keyframe_id: Optional[str] = None
    target_keyframe_id: Optional[str] = None
    motion_reference_id: Optional[str] = None
    raw_video_path: Optional[str] = None
    derived_video_path: Optional[str] = None
    prompt_version: Optional[str] = None
    input_references: list[dict[str, Any]] = field(default_factory=list)
    generation_metadata: dict[str, Any] = field(default_factory=dict)
    qa_result: dict[str, Any] = field(default_factory=dict)
    selected: bool = False
    error: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class MotionVersion:
    id: str
    pet_id: str
    user_id: str
    motion_id: str
    motion_class: str
    version: int
    status: str
    motion_spec_version: Optional[str] = None
    start_keyframe_id: Optional[str] = None
    start_keyframe_version: Optional[int] = None
    target_keyframe_id: Optional[str] = None
    target_keyframe_version: Optional[int] = None
    canonical_version_id: Optional[str] = None
    selected_candidate_id: Optional[str] = None
    selection_reason: Optional[str] = None
    video_strategy: Optional[str] = None
    output_spec: dict[str, Any] = field(default_factory=dict)
    prompt: Optional[str] = None
    prompt_version: Optional[str] = None
    qa_summary: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    candidates: list[MotionCandidate] = field(default_factory=list)
    deduplicated: bool = False


def _to_candidate(row: dict[str, Any]) -> MotionCandidate:
    return MotionCandidate(
        id=str(row.get("id")),
        motion_version_id=str(row.get("motion_version_id")),
        provider=str(row.get("provider") or ""),
        model=(row.get("model") or None),
        attempt=int(row.get("attempt") or 1),
        provider_job_id=(row.get("provider_job_id") or None),
        start_keyframe_id=(str(row["start_keyframe_id"]) if row.get("start_keyframe_id") else None),
        target_keyframe_id=(str(row["target_keyframe_id"]) if row.get("target_keyframe_id") else None),
        motion_reference_id=(row.get("motion_reference_id") or None),
        raw_video_path=(row.get("raw_video_path") or None),
        derived_video_path=(row.get("derived_video_path") or None),
        prompt_version=(row.get("prompt_version") or None),
        input_references=list(row.get("input_references") or []),
        generation_metadata=dict(row.get("generation_metadata") or {}),
        qa_result=dict(row.get("qa_result") or {}),
        decision=str(row.get("decision") or "ERROR"),
        selected=bool(row.get("selected")),
        error=(row.get("error") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


def _to_version(row: dict[str, Any], candidates: list[dict[str, Any]], *, deduplicated: bool = False) -> MotionVersion:
    return MotionVersion(
        id=str(row.get("id")),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        motion_id=str(row.get("motion_id") or ""),
        motion_class=str(row.get("motion_class") or ""),
        motion_spec_version=(row.get("motion_spec_version") or None),
        start_keyframe_id=(str(row["start_keyframe_id"]) if row.get("start_keyframe_id") else None),
        start_keyframe_version=row.get("start_keyframe_version"),
        target_keyframe_id=(str(row["target_keyframe_id"]) if row.get("target_keyframe_id") else None),
        target_keyframe_version=row.get("target_keyframe_version"),
        canonical_version_id=(str(row["canonical_version_id"]) if row.get("canonical_version_id") else None),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_BUILDING),
        selected_candidate_id=(str(row["selected_candidate_id"]) if row.get("selected_candidate_id") else None),
        selection_reason=(row.get("selection_reason") or None),
        video_strategy=(row.get("video_strategy") or None),
        output_spec=dict(row.get("output_spec") or {}),
        prompt=(row.get("prompt") or None),
        prompt_version=(row.get("prompt_version") or None),
        qa_summary=dict(row.get("qa_summary") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        warnings=list(row.get("warnings") or []),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        completed_at=(str(row["completed_at"]) if row.get("completed_at") else None),
        candidates=[
            _to_candidate(c)
            for c in sorted(candidates, key=lambda c: (str(c.get("created_at") or ""), int(c.get("attempt") or 0)))
        ],
        deduplicated=deduplicated,
    )


async def _version_rows(pet_id: str, motion_id: Optional[str] = None) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            q = _supabase().table(_versions_table()).select("*").eq("pet_id", pet_id)
            if motion_id:
                q = q.eq("motion_id", motion_id)
            r = q.order("version", desc=False).execute()
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("모션 버전 조회 실패 (pet=%s)", pet_id)
            raise MotionVideoError(
                "MOTIONS_UNAVAILABLE", "모션 버전을 확인하지 못했습니다.", status=503
            ) from e
    return [
        r
        for r in _MOCK_VERSIONS
        if r.get("pet_id") == pet_id and (motion_id is None or r.get("motion_id") == motion_id)
    ]


async def _candidate_rows(version_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_candidates_table())
                .select("*")
                .eq("motion_version_id", version_id)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception:
            logger.exception("모션 후보 조회 실패 (version=%s)", version_id)
            return []
    return [c for c in _MOCK_CANDIDATES if c.get("motion_version_id") == version_id]


# ══════════════════════════════════════════════════════════════════════════
# 빌드
# ══════════════════════════════════════════════════════════════════════════


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import motion_video_qa as qa

    order = {qa.PASS: 0, qa.REVIEW: 1, qa.FAIL: 2, "ERROR": 3}
    return sorted(
        [c for c in rows if c["decision"] != "ERROR"],
        key=lambda c: (
            order.get(c["decision"], 9),
            -float((c.get("qa_result") or {}).get("identity_similarity") or -1.0),
            c["attempt"],
        ),
    )


def _rgb_from_bytes(data: Optional[bytes]) -> Optional[np.ndarray]:
    if not data:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except Exception:
        return None


def _frames_to_jpeg(frames: Sequence[Optional[np.ndarray]]) -> list[tuple[bytes, str]]:
    from PIL import Image

    out: list[tuple[bytes, str]] = []
    for f in frames or []:
        if f is None:
            continue
        buf = io.BytesIO()
        Image.fromarray(f, mode="RGB").save(buf, format="JPEG", quality=90)
        out.append((buf.getvalue(), "image/jpeg"))
    return out


async def build_motion_video(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    providers: Optional[Sequence[Any]] = None,
    frame_sampler: Optional[Callable[[bytes], Optional[list[Optional[np.ndarray]]]]] = None,
    conformance_fn: Optional[Callable[[bytes, dict[str, Any]], dict[str, Any]]] = None,
    sign_url_fn: Optional[Callable[[Any], Optional[str]]] = None,
    skip_if_unchanged: bool = True,
) -> MotionVersion:
    from . import (
        canonical_pet_service,
        motion_spec,
        motion_video_prompts,
        motion_video_qa,
        pet_identity_service,
        pet_reference_service,
        supabase_assets,
        video_anchor,
        video_motion_providers,
        vlm_identity,
    )
    from .video_motion_providers import MotionVideoRequest, VideoProviderError

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise MotionVideoError("MOTION_INVALID", "user_id 와 pet_id 가 필요합니다.")

    # ── Phase 5.1 계약 (승인 키프레임 게이트 포함 — REVIEW/FAIL 은 여기서 거절) ─
    try:
        contract = await motion_spec.resolve_video_generation_spec(
            user_id=uid, pet_id=pid, motion_id=motion_id
        )
    except motion_spec.MotionSpecError as e:
        raise MotionVideoError(e.code, e.message, status=e.status) from e

    spec = motion_spec.get_motion(motion_id)
    motion_class = contract["motion_class"]

    # ── 라우팅 + 능력 검증 ────────────────────────────────────────────────
    resolved = list(providers) if providers is not None else video_motion_providers.routing_for_class(motion_class)
    resolved = [p for p in resolved if p.available()]
    if not resolved:
        raise MotionVideoError(
            "PROVIDER_NOT_CONFIGURED", "비디오 프로바이더가 설정되지 않았습니다.", status=503
        )
    if contract["video_strategy"] == "START_END_FRAME":
        capable = [p for p in resolved if p.supports_end_frame]
        if not capable:
            # start-only 강등은 없다 — 명시적 라우팅 실패 (요구 3).
            raise MotionVideoError(
                "ROUTING_UNSUPPORTED",
                "START_END_FRAME 전략을 지원하는 프로바이더가 없습니다 — "
                "목표 프레임을 버린 생성은 등가물이 아니다.",
                status=503,
            )
        resolved = capable

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    sign = sign_url_fn or canonical_pet_service._default_sign_url

    # ── 모션 레퍼런스 실소비 게이트 (Phase 6.7) ──────────────────────────
    # I2V_MOTION_REF 는 레퍼런스 비디오가 **실제 프로바이더 입력**으로 들어갈
    # 때만 유지된다. 소비 불가(레퍼런스 지원 프로바이더 없음/자산 없음/서명
    # 불가/계약 길이 초과)면 기존 I2V 로 강등하고 경고를 남긴다 — 메타데이터만
    # 박제한 채 레퍼런스 조건부라고 표기하지 않는다.
    strategy = contract["video_strategy"]
    extra_warnings: list[str] = []
    motion_reference_url: Optional[str] = None
    _mr = contract.get("motion_reference") or {}
    if strategy == motion_spec.STRATEGY_I2V_MOTION_REF:
        capable = [p for p in resolved if getattr(p, "supports_motion_reference", False)]
        if not capable and providers is None:
            capable = [
                p for p in video_motion_providers.reference_capable_providers() if p.available()
            ]
        asset = _mr.get("asset") or {}
        ref_obj = (
            SimpleNamespace(
                bucket=asset.get("bucket") or "",
                object_path=asset["object_path"],
                mime_type="video/mp4",
            )
            if asset.get("object_path")
            else None
        )
        ref_dur = _mr.get("duration_sec")
        too_long = (
            ref_dur is not None
            and float(ref_dur) > video_motion_providers.RUNWAY_WAN_MAX_REFERENCE_SEC
        )
        if capable and ref_obj and not too_long:
            motion_reference_url = sign(ref_obj)
        if motion_reference_url:
            # 레퍼런스 조건부 경로 — 단일 프로바이더, 교차 전략 폴백 없음.
            resolved = capable[:1]
        else:
            degrade_reason = (
                "no reference-capable provider" if not capable
                else "reference asset missing" if not ref_obj
                else "reference exceeds provider limit "
                     f"({ref_dur}s > {video_motion_providers.RUNWAY_WAN_MAX_REFERENCE_SEC}s)" if too_long
                else "reference asset not signable"
            )
            extra_warnings.append(
                f"motion reference resolved but NOT consumed ({degrade_reason}) — "
                "degraded to IMAGE_TO_VIDEO"
            )
            strategy = motion_spec.STRATEGY_I2V

    # ── 라이브 안전 게이트 — 과금 전, 행 기록 전 ─────────────────────────
    allowed, reason = video_motion_providers.live_generation_allowed(pid, resolved)
    if not allowed:
        raise MotionVideoError(
            "LIVE_GENERATION_BLOCKED",
            f"라이브 비디오 생성이 차단됐습니다 ({reason}) — PHASE6_LIVE_MODE 를 확인하세요.",
            status=403,
        )

    # ── 입력 조립 ─────────────────────────────────────────────────────────
    def _obj(payload: Optional[dict[str, Any]]):
        if not payload or not payload.get("raw"):
            return None
        raw = payload["raw"]
        return SimpleNamespace(
            bucket=raw.get("bucket") or "", object_path=raw.get("object_path") or "",
            mime_type="image/png",
        )

    start_obj = _obj(contract["start_keyframe"])
    start_bytes = fetch(start_obj) if start_obj else None
    if not start_bytes:
        raise MotionVideoError(
            "KEYFRAME_ASSET_UNAVAILABLE", "시작 키프레임 이미지를 불러오지 못했습니다.", status=503
        )
    target_obj = _obj(contract.get("target_keyframe"))
    target_bytes = fetch(target_obj) if target_obj else None
    if contract["video_strategy"] == "START_END_FRAME" and not target_bytes:
        raise MotionVideoError(
            "KEYFRAME_ASSET_UNAVAILABLE", "목표 키프레임 이미지를 불러오지 못했습니다.", status=503
        )

    output_spec, profile_warnings = build_output_spec(
        duration_range=contract.get("duration_range_sec") or [],
        motion_class=motion_class,
    )
    prompt = motion_video_prompts.build_motion_video_prompt(contract, spec.description)
    versions_stamp = analyzer_versions(resolved)
    warnings = list(contract.get("warnings") or []) + profile_warnings + extra_warnings

    # ── 멱등 ─────────────────────────────────────────────────────────────
    if skip_if_unchanged:
        rows = await _version_rows(pid, contract["motion_id"])
        if rows:
            latest = rows[-1]
            if (
                latest.get("status") in (STATUS_COMPLETE, STATUS_REVIEW)
                and str(latest.get("start_keyframe_id")) == str(contract["start_keyframe"]["keyframe_id"])
                and latest.get("prompt_version") == motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION
                and (latest.get("analyzer_versions") or {}) == versions_stamp
            ):
                return _to_version(latest, await _candidate_rows(str(latest["id"])), deduplicated=True)

    cid = pid[4:] if pid.startswith("pet_") else pid

    # ── 9:16 비디오 앵커 (라이브 검증 계약) ──────────────────────────────
    # Seedance 2.5/Kling V3 I2V 에는 aspect_ratio 파라미터가 없다 — 출력 기하는
    # **시작 이미지**가 결정한다. 키프레임이 요청 종횡비가 아니면 결정론적
    # DERIVED 앵커(펫 픽셀 보존·중앙 배치·중립 패딩)를 만들어 시작/끝 이미지로
    # 쓴다. 펫을 재생성하지 않는다 — 캔버스 기하만 바꾼다.
    start_image_url = sign(start_obj) if start_obj else None
    target_image_url = (
        sign(target_obj)
        if target_obj and contract["video_strategy"] == "START_END_FRAME"
        else None
    )
    anchor_meta: dict[str, Any] = {}
    if video_anchor.anchor_enabled():
        a = await video_anchor.ensure_video_anchor(
            user_id=uid, content_id=cid, keyframe=contract["start_keyframe"],
            image_bytes=start_bytes, aspect_ratio=output_spec["aspect_ratio"],
        )
        if a:
            start_bytes, start_image_url = a.bytes, a.url
            anchor_meta["start"] = {"object_path": a.object_path, **a.meta}
        if target_bytes and contract["video_strategy"] == "START_END_FRAME":
            t = await video_anchor.ensure_video_anchor(
                user_id=uid, content_id=cid, keyframe=contract["target_keyframe"],
                image_bytes=target_bytes, aspect_ratio=output_spec["aspect_ratio"],
            )
            if t:
                target_bytes, target_image_url = t.bytes, t.url
                anchor_meta["target"] = {"object_path": t.object_path, **t.meta}

    policy = candidate_policy(motion_class)
    rows = await _version_rows(pid, contract["motion_id"])
    durable_execution = any(getattr(provider, "durable_execution", False) for provider in resolved)
    resumable = rows[-1] if rows else None
    if not (
        durable_execution
        and resumable
        and resumable.get("status") == STATUS_BUILDING
        and str(resumable.get("start_keyframe_id") or "")
        == str(contract["start_keyframe"]["keyframe_id"])
        and resumable.get("start_keyframe_version") == contract["start_keyframe"]["version"]
        and str(resumable.get("canonical_version_id") or "")
        == str(contract.get("canonical_version_id") or "")
        and resumable.get("motion_spec_version") == contract["motion_spec_version"]
        and resumable.get("prompt_version") == motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION
        and (resumable.get("analyzer_versions") or {}) == versions_stamp
    ):
        resumable = None

    if resumable:
        version_row = resumable
    else:
        version_row = {
            "id": str(uuid.uuid4()),
            "pet_id": pid,
            "user_id": uid,
            "motion_id": contract["motion_id"],
            "motion_class": motion_class,
            "motion_spec_version": contract["motion_spec_version"],
            "start_keyframe_id": contract["start_keyframe"]["keyframe_id"],
            "start_keyframe_version": contract["start_keyframe"]["version"],
            "target_keyframe_id": (contract.get("target_keyframe") or {}).get("keyframe_id"),
            "target_keyframe_version": (contract.get("target_keyframe") or {}).get("version"),
            "canonical_version_id": contract.get("canonical_version_id"),
            "version": (max((int(r.get("version") or 0) for r in rows), default=0)) + 1,
            "status": STATUS_BUILDING,
            "selected_candidate_id": None,
            "selection_reason": None,
            # 실제 사용된 전략 — 레퍼런스 미소비 시 I2V 로 강등된 값이 기록된다.
            "video_strategy": strategy,
            "output_spec": output_spec,
            "prompt": prompt,
            "prompt_version": motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION,
            "qa_summary": {},
            "analyzer_versions": versions_stamp,
            "warnings": warnings,
            "created_at": _now_iso(),
            "completed_at": None,
        }
        if not await canonical_pet_service._insert(_versions_table(), _MOCK_VERSIONS, version_row):
            raise MotionVideoError("MOTIONS_UNAVAILABLE", "모션 버전을 기록하지 못했습니다.", status=503)
    version_id = str(version_row["id"])

    sampler = frame_sampler or motion_video_qa.sample_frames
    start_rgb = _rgb_from_bytes(start_bytes)
    target_rgb = _rgb_from_bytes(target_bytes)

    # 해석된 모션 레퍼런스의 id+버전을 박제한다 (Phase 6.6) — 라이브러리가 V2 로
    # 개선돼도 이 생성물은 영원히 "V1 을 썼다"고 기록된다.
    # Phase 6.7: consumed 가 True 인 것만이 "실제로 프로바이더에 보냈다"는 뜻이다.
    motion_reference_snapshot = (
        {
            k: _mr.get(k)
            for k in ("id", "version", "selection_level", "quality", "resolution")
            if _mr.get(k) is not None
        }
        or None
    )
    if motion_reference_snapshot is not None:
        motion_reference_snapshot["consumed"] = bool(motion_reference_url)
        if motion_reference_url:
            motion_reference_snapshot["sent_object_path"] = (_mr.get("asset") or {}).get(
                "object_path"
            )
            motion_reference_snapshot["sent_version"] = _mr.get("version")

    input_references = [
        {"kind": "start_keyframe", "keyframe_id": contract["start_keyframe"]["keyframe_id"]},
        *(
            [{"kind": "target_keyframe", "keyframe_id": contract["target_keyframe"]["keyframe_id"]}]
            if contract.get("target_keyframe")
            else []
        ),
        *(
            {"kind": f"video_anchor_{k}", "object_path": v["object_path"]}
            for k, v in anchor_meta.items()
        ),
        *(
            [{
                "kind": "motion_reference_video",
                "reference_key": _mr.get("id"),
                "version": _mr.get("version"),
                "object_path": (_mr.get("asset") or {}).get("object_path"),
            }]
            if motion_reference_url
            else []
        ),
    ]

    candidates = await _candidate_rows(version_id) if resumable else []
    passes = sum(1 for candidate in candidates if candidate.get("decision") == "PASS")
    contract_violation = any(
        bool((candidate.get("generation_metadata") or {}).get("contract_violation"))
        for candidate in candidates
    )
    #: 어댑터/스키마 계약 실패 — QA 실패도 프로바이더 장애도 아니다. 같은
    #: 잘못된 요청을 반복하거나 폴백 과금을 태우지 않는다 (이미지 빌더와 동일 정책).
    _CONTRACT_CODES = ("PROVIDER_CONTRACT", "PROVIDER_SCHEMA")

    async def run_provider(provider: Any, max_candidates: int, tier: str) -> None:
        nonlocal passes, contract_violation
        for attempt in range(1, max_candidates + 1):
            if passes >= policy["stop_after_passes"]:
                return
            existing = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("provider") == provider.name
                    and int(candidate.get("attempt") or 0) == attempt
                    and (candidate.get("generation_metadata") or {}).get("tier") == tier
                ),
                None,
            )
            resumable_candidate = bool(
                existing
                and existing.get("decision") == "ERROR"
                and not existing.get("error")
                and existing.get("raw_video_path")
            )
            if existing and not resumable_candidate:
                continue
            cand_id = str(existing.get("id")) if existing else str(uuid.uuid4())
            cand_row: dict[str, Any] = existing or {
                "id": cand_id,
                "motion_version_id": version_id,
                "pet_id": pid,
                "user_id": uid,
                "motion_id": contract["motion_id"],
                "provider": provider.name,
                "model": provider.model_name(),
                "attempt": attempt,
                "provider_job_id": None,
                "start_keyframe_id": contract["start_keyframe"]["keyframe_id"],
                "target_keyframe_id": (contract.get("target_keyframe") or {}).get("keyframe_id"),
                "motion_reference_id": (contract.get("motion_reference") or {}).get("id"),
                "raw_bucket": None,
                "raw_video_path": None,
                "derived_video_path": None,
                "prompt_version": motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION,
                "input_references": input_references,
                "generation_metadata": {
                    "tier": tier,
                    "output_spec": output_spec,
                    "motion_reference": motion_reference_snapshot,
                    **({"video_anchor": anchor_meta} if anchor_meta else {}),
                },
                "qa_result": {},
                "decision": "ERROR",
                "selected": False,
                "error": None,
                "created_at": _now_iso(),
            }
            try:
                logger.info(
                    "[motion-receipt] pet=%s motion=%s v=%s provider=%s attempt=%d",
                    pid, contract["motion_id"], version_row["version"], provider.name, attempt,
                )
                result = provider.generate(
                    MotionVideoRequest(
                        prompt=prompt,
                        start_image_url=start_image_url,
                        start_image_bytes=start_bytes,
                        end_image_url=target_image_url,
                        end_image_bytes=(target_bytes if contract["video_strategy"] == "START_END_FRAME" else None),
                        output_spec=output_spec,
                        motion_reference_url=motion_reference_url,
                        metadata={
                            "pet_id": pid,
                            "motion_id": contract["motion_id"],
                            "motion_version_id": version_id,
                            "start_keyframe_id": contract["start_keyframe"]["keyframe_id"],
                            "target_keyframe_id": (contract.get("target_keyframe") or {}).get(
                                "keyframe_id"
                            ),
                            "motion_reference_id": (contract.get("motion_reference") or {}).get("id"),
                            "attempt": attempt,
                        },
                    )
                )
            except VideoProviderError as e:
                cand_row["error"] = f"{e.code}: {e.message}"[:500]
                if e.code in _CONTRACT_CODES:
                    # 우리 요청/파싱이 계약을 어긴 것이다 — 같은 요청을 반복하지
                    # 않고(시도 소모 금지) 감사 기록 1건만 남긴 뒤 멈춘다.
                    cand_row["generation_metadata"] = {
                        **cand_row["generation_metadata"],
                        "contract_violation": True,
                    }
                    contract_violation = True
                    if existing:
                        await canonical_pet_service._update(
                            _candidates_table(), _MOCK_CANDIDATES, cand_id,
                            {
                                "error": cand_row["error"],
                                "generation_metadata": cand_row["generation_metadata"],
                            },
                        )
                    else:
                        await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                        candidates.append(cand_row)
                    return
                if existing:
                    await canonical_pet_service._update(
                        _candidates_table(), _MOCK_CANDIDATES, cand_id,
                        {"error": cand_row["error"]},
                    )
                else:
                    await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)
                continue

            cand_row["model"] = result.model
            cand_row["provider_job_id"] = result.external_job_id
            cand_row["generation_metadata"] = {
                **cand_row["generation_metadata"],
                "usage": result.usage,
            }

            raw_path = cand_row.get("raw_video_path") or (
                f"{uid}/{cid}/motions/{contract['motion_id'].lower()}/v{version_row['version']}/"
                f"{provider.name}_a{attempt}_raw.mp4"
            )
            if not cand_row.get("raw_video_path"):
                try:
                    await supabase_assets.upload_asset_to_storage(raw_path, result.video_bytes, "video/mp4")
                    cand_row["raw_bucket"] = supabase_assets.BUCKET
                    cand_row["raw_video_path"] = raw_path
                except Exception:
                    cand_row["error"] = "RAW_STORE_FAILED"
                    await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)
                    continue

                # 완료된 후보는 QA **이전에** 저장된다.
                await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                candidates.append(cand_row)

            frames = sampler(result.video_bytes)
            temporal_qa, vlm_frames, vlm_fractions = _breathing_evidence(
                contract["motion_id"], result.video_bytes, start_rgb, frames
            )
            vlm_qa = vlm_identity.qa_motion_video(
                _frames_to_jpeg(vlm_frames),
                motion_description=spec.description,
                motion_class=motion_class,
                sample_fractions=vlm_fractions,
                reference_image=(start_bytes, "image/png"),
                target_image=((target_bytes, "image/png") if target_bytes else None),
            )
            qa = motion_video_qa.evaluate_motion_video(
                frames=frames,
                spec_contract=contract,
                start_keyframe_rgb=start_rgb,
                target_keyframe_rgb=target_rgb,
                vlm_qa=vlm_qa,
                temporal_qa=temporal_qa,
            )

            # ── 출력 규격 검증 (Phase 6.5) — 프로바이더가 요청 사양을 지켰는가.
            # 종횡비/오디오 위반은 FAIL 로 강등된다: 요청을 명시했는데 어긴
            # 출력이 조용히 통과할 수 없다. unknown 은 기록만 (PASS 승격 없음).
            conformance = (conformance_fn or motion_video_qa.verify_output_conformance)(
                result.video_bytes, output_spec
            )
            qa["output_conformance"] = conformance
            if conformance["status"] == motion_video_qa.FAIL:
                qa["decision"] = motion_video_qa.FAIL
                qa["reasons"] = list(qa.get("reasons") or []) + [
                    f"output_conformance:{r}" for r in conformance["reasons"]
                ]
            elif conformance["status"] == motion_video_qa.REVIEW and qa["decision"] == motion_video_qa.PASS:
                qa["decision"] = motion_video_qa.REVIEW
                qa["reasons"] = list(qa.get("reasons") or []) + [
                    f"output_conformance:{r}" for r in conformance["reasons"]
                ]

            cand_row["qa_result"] = qa
            cand_row["decision"] = qa["decision"]
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, cand_id,
                {
                    "model": cand_row["model"],
                    "provider_job_id": cand_row["provider_job_id"],
                    "generation_metadata": cand_row["generation_metadata"],
                    "qa_result": qa,
                    "decision": qa["decision"],
                },
            )
            if qa["decision"] == motion_video_qa.PASS:
                passes += 1

    await run_provider(resolved[0], policy["max_primary"], "primary")
    # 계약 위반은 우리 어댑터 잘못이다 — 폴백 프로바이더 과금으로 가리지 않는다.
    if passes == 0 and len(resolved) > 1 and not contract_violation:
        await run_provider(resolved[1], policy["max_fallback"], "fallback")

    from . import motion_video_qa as qa_mod

    ranked = _rank(candidates)
    selected = ranked[0] if ranked and ranked[0]["decision"] == qa_mod.PASS else None
    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best PASS candidate: {selected['provider']} attempt {selected['attempt']}, "
            f"identity_similarity={selected['qa_result'].get('identity_similarity')}"
        )
        await canonical_pet_service._update(_candidates_table(), _MOCK_CANDIDATES, selected["id"], {"selected": True})
        selected["selected"] = True
        provenance = {
            "motion_version_id": version_id,
            "motion_id": contract["motion_id"],
            "motion_spec_version": contract["motion_spec_version"],
            "candidate_id": selected["id"],
            "start_keyframe_id": contract["start_keyframe"]["keyframe_id"],
            "target_keyframe_id": (contract.get("target_keyframe") or {}).get("keyframe_id"),
            "canonical_version_id": contract.get("canonical_version_id"),
            "provider": selected["provider"],
            "model": selected["model"],
        }
        if selected.get("raw_video_path"):
            try:
                await pet_reference_service.record_generated(
                    user_id=uid, content_id=cid, object_path=selected["raw_video_path"],
                    generated_kind=GENERATED_KIND_MOTION, mime_type="video/mp4",
                    provenance=provenance,
                )
            except Exception:
                logger.warning("모션 대장 기록 실패", exc_info=True)
    elif any(c["decision"] == qa_mod.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
    elif contract_violation:
        status = STATUS_FAILED
        selection_reason = (
            "provider contract/schema violation — 어댑터/요청 스키마 수정 필요 (QA 실패 아님)"
        )
    else:
        status = STATUS_FAILED
        selection_reason = "no usable candidate"

    final_fields = {
        "status": status,
        "selected_candidate_id": (selected["id"] if selected else None),
        "selection_reason": selection_reason,
        "qa_summary": {
            "candidate_count": len(candidates),
            "decisions": {
                d: sum(1 for c in candidates if c["decision"] == d)
                for d in ("PASS", "REVIEW", "FAIL", "ERROR")
            },
            "policy": policy,
        },
        "completed_at": _now_iso(),
    }
    await canonical_pet_service._update(_versions_table(), _MOCK_VERSIONS, version_id, final_fields)
    version_row.update(final_fields)
    return _to_version(version_row, candidates)


# ══════════════════════════════════════════════════════════════════════════
# 조회 / 평가
# ══════════════════════════════════════════════════════════════════════════


async def _assert_owned(user_id: str, pet_id: str) -> None:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise MotionVideoError(e.code, e.message, status=e.status) from e


async def get_motion_version(
    *, user_id: str, pet_id: str, motion_id: str, version: Optional[int] = None
) -> Optional[MotionVersion]:
    await _assert_owned(user_id, pet_id)
    rows = await _version_rows(pet_id, (motion_id or "").strip().upper())
    if not rows:
        return None
    row = None
    if version is not None:
        for r in rows:
            if int(r.get("version") or 0) == version:
                row = r
                break
    else:
        row = max(rows, key=lambda r: int(r.get("version") or 0))
    if not row:
        return None
    return _to_version(row, await _candidate_rows(str(row["id"])))


async def list_motion_versions(*, user_id: str, pet_id: str) -> list[MotionVersion]:
    await _assert_owned(user_id, pet_id)
    rows = await _version_rows(pet_id)
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        mid = str(r.get("motion_id") or "")
        if mid not in latest or int(r.get("version") or 0) > int(latest[mid].get("version") or 0):
            latest[mid] = r
    return [_to_version(r, []) for r in latest.values()]


def _breathing_evidence(
    motion_id: str,
    video_bytes: Optional[bytes],
    start_rgb: Optional[np.ndarray],
    frames: Optional[list[Optional[np.ndarray]]],
) -> tuple[Optional[dict[str, Any]], list, tuple[float, ...]]:
    """
    BREATHING 전용 시간축 증거 + VLM 근접쌍 프레임 (motion-video-qa-v3).

    다른 모션은 (None, 기존 프레임, 기존 분율) 그대로다. 어떤 실패도 조용히
    기존 입력으로 떨어진다 — 증거 수집이 QA 를 죽이면 안 된다.
    """
    from . import breathing_temporal_qa, motion_video_qa

    base_frames = list(frames or [])
    base_fractions = tuple(motion_video_qa.SAMPLE_FRACTIONS)
    if (motion_id or "").upper() != "BREATHING" or not video_bytes:
        return None, base_frames, base_fractions

    temporal = breathing_temporal_qa.analyze(video_bytes, start_rgb)
    try:
        extra = motion_video_qa.sample_frames(
            video_bytes, fractions=breathing_temporal_qa.VLM_EVIDENCE_FRACTIONS
        )
    except Exception:
        extra = None
    if not extra or len(base_frames) != len(base_fractions):
        return temporal, base_frames, base_fractions
    # 근접쌍을 시간순으로 병합한다 — VLM 계약("chronological order at fractions")
    # 을 지키면서 (0.25,0.30) (0.5,0.55) (0.75,0.80) 세 쌍을 만든다. 총 12장 캡.
    combined = sorted(
        [(f, fr) for f, fr in zip(base_fractions, base_frames)]
        + [
            (f, fr)
            for f, fr in zip(breathing_temporal_qa.VLM_EVIDENCE_FRACTIONS, extra)
            if fr is not None
        ],
        key=lambda pair: pair[0],
    )
    return temporal, [fr for _, fr in combined], tuple(f for f, _ in combined)


async def reevaluate_motion_candidate(
    *,
    user_id: str,
    pet_id: str,
    motion_id: str,
    motion_version_id: str,
    candidate_id: str,
    video_bytes: Optional[bytes] = None,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    frame_sampler: Optional[Callable[[bytes], Optional[list[Optional[np.ndarray]]]]] = None,
    vlm_qa_fn: Optional[Callable[..., Optional[dict[str, Any]]]] = None,
    conformance_fn: Optional[Callable[[bytes, dict[str, Any]], dict[str, Any]]] = None,
) -> MotionVersion:
    """Re-run the current versioned QA against one already stored candidate.

    This operator path never invokes a generation provider and never uploads or
    replaces an asset.  The resulting candidate decision is derived from the
    current QA implementation; the superseded QA result is retained in
    ``generation_metadata.qa_history`` for auditability.
    """
    from . import (
        asset_url_refresh,
        canonical_pet_service,
        motion_spec,
        motion_video_qa,
        pet_identity_service,
        supabase_assets,
        vlm_identity,
    )

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    mid = (motion_id or "").strip().upper()
    version_id = (motion_version_id or "").strip()
    cand_id = (candidate_id or "").strip()
    if not all((uid, pid, mid, version_id, cand_id)):
        raise MotionVideoError("QA_RERUN_INVALID", "QA 재실행 식별자가 모두 필요합니다.", status=422)

    await _assert_owned(uid, pid)
    version_row = next(
        (
            row
            for row in await _version_rows(pid, mid)
            if str(row.get("id") or "") == version_id
        ),
        None,
    )
    if not version_row or str(version_row.get("user_id") or "") != uid:
        raise MotionVideoError("MOTION_VERSION_NOT_FOUND", "소유한 모션 버전이 없습니다.", status=404)
    candidates = await _candidate_rows(version_id)
    candidate = next((row for row in candidates if str(row.get("id") or "") == cand_id), None)
    if not candidate or any(
        (
            str(candidate.get("user_id") or "") != uid,
            str(candidate.get("pet_id") or "") != pid,
            str(candidate.get("motion_id") or "").upper() != mid,
        )
    ):
        raise MotionVideoError("MOTION_CANDIDATE_NOT_FOUND", "소유한 모션 후보가 없습니다.", status=404)
    if not candidate.get("raw_video_path"):
        raise MotionVideoError("CANDIDATE_ASSET_MISSING", "재평가할 원본 비디오가 없습니다.", status=409)

    previous_qa = dict(candidate.get("qa_result") or {})
    previous_vlm = dict(previous_qa.get("vlm") or {})
    if (
        previous_qa.get("qa_version") == motion_video_qa.MOTION_VIDEO_QA_VERSION
        and previous_qa.get("sampling_version") == motion_video_qa.FRAME_SAMPLING_VERSION
        and previous_vlm.get("source") == vlm_identity.VLM_MOTION_QA_VERSION
    ):
        return _to_version(version_row, candidates, deduplicated=True)

    try:
        contract = await motion_spec.resolve_video_generation_spec(
            user_id=uid, pet_id=pid, motion_id=mid
        )
    except motion_spec.MotionSpecError as exc:
        raise MotionVideoError(exc.code, exc.message, status=exc.status) from exc
    if (
        str(version_row.get("motion_spec_version") or "")
        != str(contract.get("motion_spec_version") or "")
        or str(version_row.get("start_keyframe_id") or "")
        != str((contract.get("start_keyframe") or {}).get("keyframe_id") or "")
        or str(version_row.get("canonical_version_id") or "")
        != str(contract.get("canonical_version_id") or "")
    ):
        raise MotionVideoError(
            "QA_RERUN_LINEAGE_CHANGED",
            "현재 모션 계약이 저장된 후보 lineage 와 다릅니다.",
            status=409,
        )

    def _obj(payload: Optional[dict[str, Any]]):
        raw = (payload or {}).get("raw") or {}
        if not raw.get("object_path"):
            return None
        return SimpleNamespace(
            bucket=raw.get("bucket") or asset_url_refresh.default_bucket(),
            object_path=raw["object_path"],
            mime_type="image/png",
        )

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    start_obj = _obj(contract.get("start_keyframe"))
    target_obj = _obj(contract.get("target_keyframe"))
    start_bytes = fetch(start_obj) if start_obj else None
    target_bytes = fetch(target_obj) if target_obj else None
    if not start_bytes:
        raise MotionVideoError("KEYFRAME_ASSET_UNAVAILABLE", "시작 키프레임을 불러오지 못했습니다.", status=503)

    raw = video_bytes
    if raw is None:
        client = supabase_assets.get_client()
        if client:
            try:
                raw = client.storage.from_(
                    candidate.get("raw_bucket") or asset_url_refresh.default_bucket()
                ).download(candidate["raw_video_path"])
            except Exception:
                raw = None
    if not raw:
        raise MotionVideoError("CANDIDATE_ASSET_UNAVAILABLE", "저장된 후보 영상을 불러오지 못했습니다.", status=503)

    frames = (frame_sampler or motion_video_qa.sample_frames)(raw)
    spec = motion_spec.get_motion(mid)
    assert spec is not None  # resolve_video_generation_spec succeeded above.
    start_rgb = _rgb_from_bytes(start_bytes)
    temporal_qa, vlm_frames, vlm_fractions = _breathing_evidence(mid, raw, start_rgb, frames)
    vlm_result = (vlm_qa_fn or vlm_identity.qa_motion_video)(
        _frames_to_jpeg(vlm_frames),
        motion_description=spec.description,
        motion_class=contract["motion_class"],
        sample_fractions=vlm_fractions,
        reference_image=(start_bytes, "image/png"),
        target_image=((target_bytes, "image/png") if target_bytes else None),
    )
    qa = motion_video_qa.evaluate_motion_video(
        frames=frames,
        spec_contract=contract,
        start_keyframe_rgb=start_rgb,
        target_keyframe_rgb=_rgb_from_bytes(target_bytes),
        vlm_qa=vlm_result,
        temporal_qa=temporal_qa,
    )
    conformance = (conformance_fn or motion_video_qa.verify_output_conformance)(
        raw, dict(version_row.get("output_spec") or {})
    )
    qa["output_conformance"] = conformance
    if conformance["status"] == motion_video_qa.FAIL:
        qa["decision"] = motion_video_qa.FAIL
        qa["reasons"] = list(qa.get("reasons") or []) + [
            f"output_conformance:{reason}" for reason in conformance.get("reasons") or []
        ]
    elif conformance["status"] == motion_video_qa.REVIEW and qa["decision"] == motion_video_qa.PASS:
        qa["decision"] = motion_video_qa.REVIEW
        qa["reasons"] = list(qa.get("reasons") or []) + [
            f"output_conformance:{reason}" for reason in conformance.get("reasons") or []
        ]

    metadata = dict(candidate.get("generation_metadata") or {})
    history = list(metadata.get("qa_history") or [])
    if previous_qa:
        history.append(
            {
                "decision": candidate.get("decision"),
                "qa_result": previous_qa,
                "superseded_at": _now_iso(),
            }
        )
    metadata["qa_history"] = history
    await canonical_pet_service._update(
        _candidates_table(),
        _MOCK_CANDIDATES,
        cand_id,
        {"qa_result": qa, "decision": qa["decision"], "generation_metadata": metadata},
    )
    candidate.update({"qa_result": qa, "decision": qa["decision"], "generation_metadata": metadata})

    ranked = _rank(candidates)
    selected = ranked[0] if ranked and ranked[0].get("decision") == motion_video_qa.PASS else None
    for row in candidates:
        should_select = bool(selected and str(row.get("id")) == str(selected.get("id")))
        if bool(row.get("selected")) != should_select:
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, str(row["id"]), {"selected": should_select}
            )
            row["selected"] = should_select

    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best PASS candidate after {motion_video_qa.MOTION_VIDEO_QA_VERSION}: "
            f"{selected['provider']} attempt {selected['attempt']}"
        )
    elif any(row.get("decision") == motion_video_qa.REVIEW for row in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
    else:
        status = STATUS_FAILED
        selection_reason = "no usable candidate"

    versions = dict(version_row.get("analyzer_versions") or {})
    versions.update(
        {
            "qa": motion_video_qa.MOTION_VIDEO_QA_VERSION,
            "sampling": motion_video_qa.FRAME_SAMPLING_VERSION,
            "vlm_motion_qa": vlm_identity.VLM_MOTION_QA_VERSION,
        }
    )
    fields = {
        "status": status,
        "selected_candidate_id": (str(selected["id"]) if selected else None),
        "selection_reason": selection_reason,
        "qa_summary": {
            "candidate_count": len(candidates),
            "decisions": {
                decision: sum(1 for row in candidates if row.get("decision") == decision)
                for decision in ("PASS", "REVIEW", "FAIL", "ERROR")
            },
            "policy": dict((version_row.get("qa_summary") or {}).get("policy") or {}),
        },
        "analyzer_versions": versions,
        "completed_at": _now_iso(),
    }
    await canonical_pet_service._update(_versions_table(), _MOCK_VERSIONS, version_id, fields)
    version_row.update(fields)
    return _to_version(version_row, candidates)


async def record_motion_evaluation(
    *,
    user_id: str,
    pet_id: str,
    motion_version_id: str,
    candidate_id: Optional[str],
    scores: dict[str, Any],
    verdict: str,
    overall_usable: Optional[bool] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Phase 4/5 하네스 재사용 — provider/model/클래스/시도/길이 메타 포함 (요구 18)."""
    from . import canonical_pet_service

    provider = model = motion_id = motion_class = None
    attempt = duration = None
    for c in await _candidate_rows(motion_version_id):
        if candidate_id and str(c.get("id")) == candidate_id:
            provider, model = c.get("provider"), c.get("model")
            motion_id = c.get("motion_id")
            attempt = c.get("attempt")
            duration = ((c.get("generation_metadata") or {}).get("output_spec") or {}).get("duration_sec")
            break
    for r in await _version_rows(pet_id):
        if str(r.get("id")) == motion_version_id:
            motion_class = r.get("motion_class")
            motion_id = motion_id or r.get("motion_id")
            break

    try:
        return await canonical_pet_service.record_evaluation(
            user_id=user_id,
            pet_id=pet_id,
            canonical_version_id=motion_version_id,
            candidate_id=candidate_id,
            scores=scores,
            verdict=verdict,
            notes=notes,
            provider=provider,
            kind="motion",
            extra={
                "model": model,
                "motion_id": motion_id,
                "motion_class": motion_class,
                "attempt": attempt,
                "duration_sec": duration,
                **({"overall_usable": bool(overall_usable)} if overall_usable is not None else {}),
            },
        )
    except canonical_pet_service.CanonicalPetError as e:
        raise MotionVideoError(e.code, e.message, status=e.status) from e


async def _all_candidate_rows_for_user(user_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_candidates_table())
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception:
            logger.exception("모션 후보 전체 조회 실패 (user=%s)", user_id)
            return []
    return [c for c in _MOCK_CANDIDATES if c.get("user_id") == user_id]


async def qa_calibration_report(*, user_id: str) -> dict[str, Any]:
    """
    자동 QA 판정 vs 사람 판정 (Phase 6.5) — 임계값 재캘리브레이션의 근거.

    kind='motion' 평가를 후보의 자동 decision 과 짝지어:
      true_pass / false_pass / true_fail / false_fail / review_cases + 전체 3×3.
    현재 QA 버전은 불변이다 — 임계값 변경은 새 QA 버전으로만 (10~20마리 표본 후).
    """
    from . import canonical_pet_service, motion_video_qa

    try:
        evals = await canonical_pet_service.list_evaluation_rows(user_id=user_id)
    except canonical_pet_service.CanonicalPetError as e:
        raise MotionVideoError(e.code, e.message, status=e.status) from e
    candidates = {str(c.get("id")): c for c in await _all_candidate_rows_for_user(user_id)}

    matrix: dict[str, dict[str, int]] = {}
    buckets = {"true_pass": 0, "false_pass": 0, "true_fail": 0, "false_fail": 0, "review_cases": 0}
    pairs: list[dict[str, Any]] = []

    for row in evals:
        scores = row.get("scores") or {}
        if scores.get("kind") != "motion" or not row.get("candidate_id"):
            continue
        cand = candidates.get(str(row["candidate_id"]))
        if not cand:
            continue
        auto = str(cand.get("decision") or "REVIEW")
        human = str(row.get("verdict") or "REVIEW")
        matrix.setdefault(auto, {}).setdefault(human, 0)
        matrix[auto][human] += 1
        pairs.append(
            {
                "candidate_id": str(row["candidate_id"]),
                "motion_id": cand.get("motion_id"),
                "provider": cand.get("provider"),
                "auto_decision": auto,
                "human_verdict": human,
                "qa_version": (cand.get("qa_result") or {}).get("qa_version"),
            }
        )
        if auto == "REVIEW":
            buckets["review_cases"] += 1
        elif auto == "PASS" and human == "PASS":
            buckets["true_pass"] += 1
        elif auto == "PASS" and human == "FAIL":
            buckets["false_pass"] += 1
        elif auto == "FAIL" and human == "FAIL":
            buckets["true_fail"] += 1
        elif auto == "FAIL" and human == "PASS":
            buckets["false_fail"] += 1

    return {
        "qa_version": motion_video_qa.MOTION_VIDEO_QA_VERSION,
        "sample_count": len(pairs),
        "buckets": buckets,
        "matrix": matrix,
        "pairs": pairs,
        "note": (
            "임계값은 1~2개 사례로 바꾸지 않는다. 약 10~20마리 실펫 표본 후 "
            "새 QA 버전(motion-video-qa-v2)으로만 재캘리브레이션한다."
        ),
    }
