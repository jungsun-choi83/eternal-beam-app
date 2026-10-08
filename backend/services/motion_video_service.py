"""
모션 비디오 빌더 (Reference-locked Video Generation, Phase 6).

── 파이프라인 ──────────────────────────────────────────────────────────────
resolve_video_generation_spec (Phase 5.1 — 승인 키프레임 게이트 포함) →
  1. motion_spec 의 모션별 프로바이더 순서를 adapter registry 로 해석.
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

import asyncio
import functools
import io
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

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
            "aspect_ratio": os.getenv("PHASE6_ASPECT_RATIO", "16:9"),  # 프로파일 불변
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
    from . import (
        motion_spec,
        motion_video_prompts,
        motion_video_qa,
        qa_evidence_reuse,
        video_motion_providers,
        vlm_identity,
        vlm_escalation,
    )

    return {
        "motion_builder": MOTION_BUILDER_VERSION,
        "motion_spec": motion_spec.MOTION_SPEC_VERSION,
        "contract": motion_spec.PHASE6_CONTRACT_VERSION,
        "prompt": motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION,
        "qa": motion_video_qa.active_qa_version(),
        "sampling": motion_video_qa.FRAME_SAMPLING_VERSION,
        "vlm_motion_qa": vlm_identity.VLM_MOTION_QA_VERSION,
        "vlm_escalation": vlm_escalation.VLM_ESCALATION_VERSION,
        "qa_evidence_reuse": qa_evidence_reuse.QA_EVIDENCE_REUSE_VERSION,
        "motion_qa_contract": motion_spec.MOTION_QA_CONTRACT_VERSION,
        "providers": [f"{p.name}:{p.model_name()}" for p in providers],
        "provider_bindings": [
            video_motion_providers.provider_identity(provider) for provider in providers
        ],
    }


#: 판정만 바꾸는 버전들 — 저장된 후보를 **다시 평가**할 뿐, 다시 만들지 않는다.
#:
#: ── 무엇이 어긋나 있었나 ──────────────────────────────────────────────────
#: 멱등 판정이 analyzer_versions 스탬프 **전체**를 비교했다. QA 규칙을 한 줄
#: 고쳐 MOTION_VIDEO_QA_VERSION 을 올리면 스탬프가 달라지고, 그 순간 이미 만들어
#: 둔 영상이 "다른 것"으로 보여 프로바이더에 **다시 주문**이 나갔다. 화면도 결과도
#: 같은데 돈만 두 번 나가는 길이었다.
#:
#: 여기 적힌 키만 그 비교에서 빠진다. 나머지는 전부 생성에 영향을 준다고 본다 —
#: 새 키가 생겼을 때 기본값이 "다시 만든다" 여야, 조용히 낡은 자산을 재사용하는
#: 실수가 생기지 않는다.
QA_ONLY_VERSION_KEYS = (
    "qa",
    "sampling",
    "vlm_motion_qa",
    "vlm_escalation",
    "qa_evidence_reuse",
    "motion_qa_contract",
)


def generation_versions(stamp: Optional[dict[str, Any]]) -> dict[str, Any]:
    """스탬프에서 **생성 결과를 바꾸는** 부분만 남긴다."""
    return {k: v for k, v in (stamp or {}).items() if k not in QA_ONLY_VERSION_KEYS}


def _capability_enabled(provider: Any, capability: str) -> bool:
    value = getattr(provider, str(capability), None)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0.0
    return bool(value)


def _required_capabilities(requirements: Optional[dict[str, Any]]) -> list[str]:
    req = requirements or {}
    return [str(c) for c in (req.get("required_all") or []) if str(c).strip()]


def _degrade_permitted(requirements: Optional[dict[str, Any]]) -> bool:
    """
    레지스트리가 **명시적으로** 강등을 허용했는가.

    두 가지가 모두 있어야 한다: `degrade_allowed` 플래그와, 무엇으로 내려갈지
    말하는 `degrade_to_strategy`. 플래그만 있고 목적지가 없으면 그것은 허가가
    아니라 미기재다 — 무엇으로 내려갈지 모르는 채 내려가면 고객은 자기가
    주문한 것과 다른 것을 받는다.
    """
    req = requirements or {}
    if not bool(req.get("degrade_allowed")):
        return False
    return bool(str(req.get("degrade_to_strategy") or "").strip())


def _apply_provider_capability_requirements(
    providers: Sequence[Any],
    requirements: Optional[dict[str, Any]],
) -> list[Any]:
    """
    레지스트리 provider_capabilities 를 기존 라우팅 순서 위에 얹는다.

    ── 무엇이 어긋나 있었나 ────────────────────────────────────────────────
    `required_all` 을 아무도 만족하지 못하면 예전에는 **걸러지지 않은 원래
    목록을 그대로 되돌렸다**(`filtered if filtered else indexed`). 요구사항을
    통과한 프로바이더가 없다는 사실이 "요구사항이 없었다"와 같은 결과를 냈고,
    그 다음 줄부터는 아무도 그 차이를 알 수 없었다 — 능력 없는 프로바이더가
    유료 생성을 받아 갔다.

    이제 능력 미달은 **빈 목록**이다. 호출자가 그것을 보고 ROUTING_UNSUPPORTED
    로 거절한다. 되돌리는 경우는 레지스트리가 강등을 명시적으로 허용했을
    때뿐이고, 그때도 원래 순서를 그대로 유지한다.
    """
    req = requirements or {}
    required_all = _required_capabilities(req)
    preferred_any = [str(c) for c in (req.get("preferred_any") or []) if str(c).strip()]
    indexed = list(enumerate(list(providers)))

    filtered = [
        (idx, p)
        for idx, p in indexed
        if all(_capability_enabled(p, cap) for cap in required_all)
    ]
    if required_all and not filtered:
        if not _degrade_permitted(req):
            return []
        ranked = indexed
    else:
        ranked = filtered
    ranked = list(ranked)
    ranked.sort(
        key=lambda item: (
            -sum(1 for cap in preferred_any if _capability_enabled(item[1], cap)),
            item[0],
        )
    )
    return [p for _, p in ranked]


def _resolve_providers_for_contract(
    contract: dict[str, Any],
    providers: Optional[Sequence[Any]] = None,
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """모션 계약 순서 → 사용 가능한 adapter → capability 필터.

    반환값은 (필터 통과, 자격 증명 통과, capability 요구사항)이다. 두 목록을
    분리해 호출자가 "설정 없음"과 "능력 불일치"를 기존처럼 구분할 수 있게 한다.
    """
    from . import video_motion_providers

    try:
        resolved = (
            list(providers)
            if providers is not None
            else video_motion_providers.resolve_provider_order(
                list(contract.get("provider_order") or [])
            )
        )
    except video_motion_providers.VideoProviderError as exc:
        raise MotionVideoError(exc.code, exc.message, status=503) from exc
    available = [provider for provider in resolved if provider.available()]
    requirements = (
        ((contract.get("requirements") or {}).get("provider_capabilities") or {})
        if isinstance(contract, dict)
        else {}
    )
    return (
        _apply_provider_capability_requirements(available, requirements),
        available,
        requirements,
    )


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
# 무결성 게이트 (MOTION_QA_SEVERITY_GATE=integrity_only) — 전달 가능 후보
#
# QA 결정은 절대 고쳐 쓰지 않는다. 게이트가 켜지면 무결성 사유(motion_video_qa
# .classify_reason → INTEGRITY)가 하나도 없는 REVIEW/FAIL 후보를 "전달 가능"으로
# 본다. 발행/포장/재생/프리미엄 이행이 전부 candidate_is_publishable 하나를 본다.
# ══════════════════════════════════════════════════════════════════════════

SEVERITY_GATE_INTEGRITY_ONLY = "integrity_only"


def severity_gate_mode(override: Optional[str] = None) -> str:
    from . import motion_video_qa

    return motion_video_qa.severity_gate_mode(override)


def candidate_is_publishable(
    candidate: Any, *, mode: Optional[str] = None, motion_id: Optional[str] = None
) -> bool:
    """후보 행(dict) 또는 MotionCandidate → 전달 가능 여부. PASS 는 항상 참."""
    from . import business_qa, motion_video_qa

    if isinstance(candidate, dict):
        decision, qa = candidate.get("decision"), candidate.get("qa_result")
        motion = motion_id or candidate.get("motion_id")
    else:
        decision, qa = getattr(candidate, "decision", None), getattr(candidate, "qa_result", None)
        motion = motion_id or getattr(candidate, "motion_id", None)
    if business_qa.legacy_authority_retired(motion):
        # Receipt-only authority: the legacy decision and the severity gate
        # are not consulted, and a missing receipt is never deliverable.
        return business_qa.is_deliverable(qa or {}, legacy_fallback=False)
    if business_qa.receipt(qa or {}) is not None:
        return business_qa.is_deliverable(qa or {})
    return motion_video_qa.is_publishable(qa or {}, decision=decision, mode=mode)


def _breathing_evidence_key(qa: dict[str, Any]) -> tuple[float, float]:
    metrics = ((qa.get("temporal") or {}).get("metrics") or {}) if isinstance(qa.get("temporal"), dict) else {}
    snr = metrics.get("torso_snr")
    osc = metrics.get("scale_oscillation")
    return (
        float(snr) if isinstance(snr, (int, float)) else 0.0,
        float(osc) if isinstance(osc, (int, float)) else 0.0,
    )


def choose_publishable_candidate(
    candidates: Sequence[dict[str, Any]], *, mode: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """
    PASS 가 없을 때 게이트가 고르는 후보 — 문서화된 규칙 (위에서부터 비교):
      1. 결정: REVIEW 가 FAIL 보다 앞 (규칙 집합이 PASS 에 더 가깝다고 본 쪽)
      2. 외관(COSMETIC) 사유 개수가 적은 쪽
      3. 호흡 증거가 강한 쪽: torso_snr 내림차순, 그다음 scale_oscillation 내림차순
         (시간축 지표가 없는 모션은 0 으로 취급 — 동점이면 다음 기준)
      4. identity_similarity 내림차순
      5. attempt 오름차순 (먼저 만든 것)
    ERROR 후보와 무결성 사유가 있는 후보는 애초에 대상이 아니다.
    """
    from . import motion_video_qa

    eligible = [
        c for c in candidates
        if str(c.get("decision") or "").upper() in (motion_video_qa.REVIEW, motion_video_qa.FAIL)
        and candidate_is_publishable(c, mode=mode)
    ]
    if not eligible:
        return None
    decision_rank = {motion_video_qa.REVIEW: 0, motion_video_qa.FAIL: 1}

    def _key(c: dict[str, Any]):
        qa = c.get("qa_result") or {}
        summary = motion_video_qa.severity_summary(qa)
        snr, osc = _breathing_evidence_key(qa)
        return (
            decision_rank.get(str(c.get("decision") or "").upper(), 9),
            len(summary["cosmetic"]),
            -snr,
            -osc,
            -float(qa.get("identity_similarity") or -1.0),
            int(c.get("attempt") or 0),
        )

    return sorted(eligible, key=_key)[0]


def publication_severity_record(
    candidate: dict[str, Any], *, publication_id: str, gate: str
) -> dict[str, Any]:
    """발행 시점의 감사 기록 — 어떤 사유가 있었고 어떤 게이트로 통과했는가."""
    from . import motion_video_qa

    qa = candidate.get("qa_result") or {}
    summary = motion_video_qa.severity_summary(qa)
    return {
        "publication_id": publication_id,
        "gate": gate,
        "qa_decision": str(candidate.get("decision") or ""),
        "qa_version": qa.get("qa_version"),
        "severity_version": summary["version"],
        "integrity": summary["integrity"],
        "cosmetic": summary["cosmetic"],
        "published_at": _now_iso(),
    }


def _gate_selection_reason(candidate: dict[str, Any]) -> str:
    from . import motion_video_qa

    summary = motion_video_qa.severity_summary(candidate.get("qa_result") or {})
    return (
        f"integrity_only gate: no PASS — cosmetic-only {candidate.get('decision')} candidate: "
        f"{candidate.get('provider')} attempt {candidate.get('attempt')}; "
        f"cosmetic={summary['cosmetic']}"
    )



# ══════════════════════════════════════════════════════════════════════════
# 빌드
# ══════════════════════════════════════════════════════════════════════════


def _rank(rows: list[dict[str, Any]], *, legacy_order: bool = True) -> list[dict[str, Any]]:
    from . import motion_video_qa as qa

    if not legacy_order:
        # Legacy authority retired: creation order only. Business QA ranks.
        return sorted(
            [c for c in rows if c["decision"] != "ERROR"], key=lambda c: c["attempt"]
        )
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
    precomputed_contract: Optional[dict[str, Any]] = None,
) -> MotionVersion:
    from . import (
        business_qa,
        canonical_pet_service,
        motion_spec,
        motion_video_prompts,
        motion_video_qa,
        pet_background,
        pet_identity_service,
        pet_reference_service,
        supabase_assets,
        clean_plate_service,
        video_anchor,
        video_motion_providers,
        vlm_identity,
    )
    from .video_motion_providers import MotionVideoRequest, VideoProviderError

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    if not uid or not pid:
        raise MotionVideoError("MOTION_INVALID", "user_id 와 pet_id 가 필요합니다.")

    # ── Phase 5.1 계약 (business-v1 전달 가능 키프레임 게이트 포함) ─────────
    # 호출자(런 오케스트레이터)가 STAGE_MOTION_SPEC 에서 같은 계약을 이미
    # 해석/검증해 뒀다면 여기서 다시 해석하지 않는다 — 계약 해석 자체가
    # 키프레임/신원/canonical/레퍼런스세트/형태/모션레퍼런스를 도합 여러 번
    # 읽는 무거운 작업이라, 같은 순간의 같은 입력을 두 번 계산할 이유가 없다.
    if precomputed_contract is not None:
        contract = precomputed_contract
    else:
        try:
            contract = await motion_spec.resolve_video_generation_spec(
                user_id=uid, pet_id=pid, motion_id=motion_id
            )
        except motion_spec.MotionSpecError as e:
            raise MotionVideoError(e.code, e.message, status=e.status) from e

    spec = motion_spec.get_motion(motion_id)
    motion_class = contract["motion_class"]

    # ── 라우팅 + 능력 검증 ────────────────────────────────────────────────
    resolved, available, provider_requirements = _resolve_providers_for_contract(
        contract, providers
    )
    if not resolved:
        if available:
            # 프로바이더는 있는데 레지스트리가 요구한 능력이 없다 — 설정 누락이
            # 아니라 **라우팅 실패**다. 두 경우를 같은 코드로 말하면, 능력 없는
            # 생성을 조용히 허용하던 예전 동작으로 되돌아갈 자리가 생긴다.
            raise MotionVideoError(
                "ROUTING_UNSUPPORTED",
                "레지스트리가 요구한 프로바이더 능력("
                + ", ".join(_required_capabilities(provider_requirements))
                + ")을 가진 프로바이더가 없습니다 — 강등이 허용되지 않은 모션입니다.",
                status=503,
            )
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
        capable = _apply_provider_capability_requirements(capable, provider_requirements)
        if not capable and "supports_motion_reference" in _required_capabilities(
            provider_requirements
        ):
            # 레퍼런스가 **필수**인 모션인데 레퍼런스를 먹일 프로바이더가 없다.
            # 레지스트리가 강등을 명시적으로 허용하지 않았다면 여기서 멈춘다 —
            # 아래 IMAGE_TO_VIDEO 강등은 등가물이 아니다.
            if not _degrade_permitted(provider_requirements):
                raise MotionVideoError(
                    "ROUTING_UNSUPPORTED",
                    "모션 레퍼런스를 소비할 수 있는 프로바이더가 없습니다 — "
                    "레퍼런스 없는 생성은 등가물이 아니다.",
                    status=503,
                )
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
    target_obj = _obj(contract.get("target_keyframe"))
    versions_stamp = analyzer_versions(resolved)

    # ── 멱등 — 바이트를 한 장도 내려받기 전에 먼저 체크한다. 재개/재시도에서
    # 아무것도 안 바뀌었으면 raw 든 클린 플레이트든 전혀 다운로드하지 않는다.
    if skip_if_unchanged:
        rows = await _version_rows(pid, contract["motion_id"])
        if rows:
            latest = rows[-1]
            if (
                latest.get("status") in (STATUS_COMPLETE, STATUS_REVIEW)
                and str(latest.get("start_keyframe_id")) == str(contract["start_keyframe"]["keyframe_id"])
                and latest.get("prompt_version") == motion_video_prompts.MOTION_VIDEO_PROMPT_VERSION
                # QA 버전은 비교에서 빠진다 — 판정이 바뀌었다고 영상을 다시 사지 않는다.
                and generation_versions(latest.get("analyzer_versions"))
                == generation_versions(versions_stamp)
            ):
                return _to_version(latest, await _candidate_rows(str(latest["id"])), deduplicated=True)

    output_spec, profile_warnings = build_output_spec(
        duration_range=contract.get("duration_range_sec") or [],
        motion_class=motion_class,
    )
    prompt = motion_video_prompts.build_motion_video_prompt(contract, spec.description)
    warnings = list(contract.get("warnings") or []) + profile_warnings + extra_warnings

    cid = pid[4:] if pid.startswith("pet_") else pid

    # ── 생성 입력 = 키프레임 **클린 플레이트** (raw 아님) ────────────────
    # Phase 5 raw 에는 이미지 모델이 그린 접지/투영 그림자가 남아 있다. 그걸
    # I2V 에 먹이면 그림자가 프레임마다 흔들리며 살아나고, packed-alpha 매트가
    # 그것을 전경으로 집어삼킨다 (바닥 슬래브/발밑 얼룩). raw 는 증거로 보존되고
    # 생성에는 들어가지 않는다. 플레이트가 없으면 raw 로 **조용히 새지 않는다**.
    async def _plate_input(payload: Optional[dict[str, Any]], label: str):
        raw_payload = (payload or {}).get("raw") or {}
        plate_payload = (payload or {}).get("plate") or {}
        cut_payload = (payload or {}).get("cutout") or {}
        plate = None
        plate_error: Optional[str] = None
        try:
            plate = await clean_plate_service.ensure_plate(
                user_id=uid,
                content_id=cid,
                derived_kind=clean_plate_service.GENERATED_KIND_KEYFRAME_PLATE,
                fetch_bytes=fetch,
                raw_object_path=raw_payload.get("object_path"),
                cutout_bucket=cut_payload.get("bucket"),
                cutout_object_path=cut_payload.get("object_path"),
                plate_bucket=plate_payload.get("bucket"),
                plate_object_path_hint=plate_payload.get("object_path"),
                provenance={
                    "keyframe_id": (payload or {}).get("keyframe_id"),
                    "keyframe_role": (payload or {}).get("role"),
                    "keyframe_version": (payload or {}).get("version"),
                    "candidate_id": (payload or {}).get("candidate_id"),
                },
                # 플레이트를 다시 만들어야 하면 이 계보의 정본이 고른 배경으로 —
                # 모션 단계가 다른 회색을 고르지 않는다.
                background=pet_background.background_rgb(contract.get("background")),
            )
        except clean_plate_service.CleanPlateError as e:
            plate_error = f"{e.code}: {e.message}"
            logger.warning("키프레임 클린 플레이트 해석 실패 (%s): %s", label, plate_error)
        if plate is None:
            return None, None, {"kind": "raw", "reason": plate_error or "plate_unavailable"}
        return (
            plate.bytes,
            SimpleNamespace(
                bucket=plate.bucket or "",
                object_path=plate.object_path,
                mime_type="image/png",
            ),
            {
                "kind": "clean_plate",
                "object_path": plate.object_path,
                "created": bool(plate.created),
            },
        )

    # raw 키프레임 바이트는 더 이상 무조건 먼저 받지 않는다 — 클린 플레이트가
    # 이미 있으면(정상 경로) plate_object_path_hint 하나만 읽고 raw 는 아예
    # 건드리지 않는다. raw 는 플레이트가 없어 **진짜로 필요할 때만** 그 자리에서
    # 받는다 (아래 else 분기) — 최종적으로 프로바이더에 들어가는 바이트는
    # 이전과 동일하다 (플레이트 우선, raw 는 폴백).
    start_bytes: Optional[bytes] = None
    target_bytes: Optional[bytes] = None
    keyframe_inputs: dict[str, Any] = {}
    if clean_plate_service.plate_enabled():
        for label, payload, obj_ref in (
            ("start", contract["start_keyframe"], start_obj),
            (
                "target",
                contract.get("target_keyframe")
                if contract["video_strategy"] == "START_END_FRAME"
                else None,
                target_obj,
            ),
        ):
            if not payload:
                continue
            data, obj, lineage = await _plate_input(payload, label)
            keyframe_inputs[label] = lineage
            if data and obj:
                if label == "start":
                    start_bytes, start_obj = data, obj
                else:
                    target_bytes, target_obj = data, obj
            elif clean_plate_service.plate_required():
                raise MotionVideoError(
                    "KEYFRAME_PLATE_UNAVAILABLE",
                    f"{label} 키프레임의 클린 플레이트를 만들 수 없습니다 "
                    "(누끼 없음/손상) — raw 로 대체하지 않습니다. "
                    "CLEAN_PLATE_REQUIRED=0 으로만 명시적 허용.",
                    status=503,
                )
            else:
                raw_bytes = fetch(obj_ref) if obj_ref else None
                if label == "start":
                    start_bytes = raw_bytes
                else:
                    target_bytes = raw_bytes
                warnings.append(
                    f"{label} keyframe clean plate unavailable "
                    f"({(lineage or {}).get('reason')}) — fell back to raw keyframe"
                )
    else:
        start_bytes = fetch(start_obj) if start_obj else None
        target_bytes = fetch(target_obj) if target_obj else None

    if not start_bytes:
        raise MotionVideoError(
            "KEYFRAME_ASSET_UNAVAILABLE", "시작 키프레임 이미지를 불러오지 못했습니다.", status=503
        )
    if contract["video_strategy"] == "START_END_FRAME" and not target_bytes:
        raise MotionVideoError(
            "KEYFRAME_ASSET_UNAVAILABLE", "목표 키프레임 이미지를 불러오지 못했습니다.", status=503
        )

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
        # fetch_bytes/sign_url_fn 을 넘기면 결정론적 object_path 에 이미 같은
        # 앵커가 있는지 먼저 확인하고, 있으면 재빌드/재업로드 없이 그대로
        # 재사용한다 (source_path+aspect_ratio 가 같으면 같은 경로다).
        a = await video_anchor.ensure_video_anchor(
            user_id=uid, content_id=cid, keyframe=contract["start_keyframe"],
            image_bytes=start_bytes, aspect_ratio=output_spec["aspect_ratio"],
            source_object_path=(start_obj.object_path if start_obj else None),
            fetch_bytes=fetch, sign_url_fn=sign,
        )
        if a:
            start_bytes, start_image_url = a.bytes, a.url
            anchor_meta["start"] = {"object_path": a.object_path, **a.meta}
        if target_bytes and contract["video_strategy"] == "START_END_FRAME":
            t = await video_anchor.ensure_video_anchor(
                user_id=uid, content_id=cid, keyframe=contract["target_keyframe"],
                image_bytes=target_bytes, aspect_ratio=output_spec["aspect_ratio"],
                source_object_path=(target_obj.object_path if target_obj else None),
                fetch_bytes=fetch, sign_url_fn=sign,
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
        and generation_versions(resumable.get("analyzer_versions"))
        == generation_versions(versions_stamp)
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
        {
            "kind": "start_keyframe",
            "keyframe_id": contract["start_keyframe"]["keyframe_id"],
            # 실제로 프로바이더에 보낸 이미지가 raw 인지 클린 플레이트인지 박제.
            **({"sent_input": keyframe_inputs["start"]} if keyframe_inputs.get("start") else {}),
        },
        *(
            [{
                "kind": "target_keyframe",
                "keyframe_id": contract["target_keyframe"]["keyframe_id"],
                **(
                    {"sent_input": keyframe_inputs["target"]}
                    if keyframe_inputs.get("target")
                    else {}
                ),
            }]
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
    # BREATHING with legacy authority retired: only the receipt decides
    # delivery, retry and selection. Every other motion keeps legacy_fallback.
    legacy_fallback = not business_qa.legacy_authority_retired(contract["motion_id"])
    passes = sum(
        1 for candidate in candidates
        if business_qa.is_deliverable(
            candidate.get("qa_result") or {}, legacy_fallback=legacy_fallback
        )
    )
    contract_violation = any(
        bool((candidate.get("generation_metadata") or {}).get("contract_violation"))
        for candidate in candidates
    )
    #: 어댑터/스키마 계약 실패 — QA 실패도 프로바이더 장애도 아니다. 같은
    #: 잘못된 요청을 반복하거나 폴백 과금을 태우지 않는다 (이미지 빌더와 동일 정책).
    _CONTRACT_CODES = ("PROVIDER_CONTRACT", "PROVIDER_SCHEMA")

    async def run_provider(provider: Any, max_candidates: int, tier: str) -> None:
        nonlocal passes, contract_violation
        from . import durable_provider_jobs

        provider_identity = video_motion_providers.provider_identity(provider)
        logical_model = provider_identity["logical_model"]
        for attempt in range(1, max_candidates + 1):
            if passes >= policy["stop_after_passes"] or not business_qa.may_generate_next_candidate(
                candidates, legacy_fallback=legacy_fallback
            ):
                return
            existing = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("provider") == logical_model
                    and int(candidate.get("attempt") or 0) == attempt
                    and (candidate.get("generation_metadata") or {}).get("tier") == tier
                ),
                None,
            )
            resumable_candidate = bool(
                existing
                and existing.get("decision") == "ERROR"
                and existing.get("provider_job_id")
                and (
                    (not existing.get("error") and existing.get("raw_video_path"))
                    # raw 저장만 실패한 유료 결과 — 같은 후보를 재사용해 재시도한다
                    # (재과금 없음).
                    or (existing.get("error") == "RAW_STORE_FAILED" and not existing.get("raw_video_path"))
                )
            )
            if existing and not resumable_candidate:
                continue
            already_persisted = bool(existing)
            cand_id = str(existing.get("id")) if existing else str(uuid.uuid4())
            cand_row: dict[str, Any] = existing or {
                "id": cand_id,
                "motion_version_id": version_id,
                "pet_id": pid,
                "user_id": uid,
                "motion_id": contract["motion_id"],
                "provider": logical_model,
                "model": provider_identity["vendor_model"],
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
                    "provider_identity": provider_identity,
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
                    "[motion-receipt] pet=%s motion=%s v=%s model=%s vendor=%s adapter=%s attempt=%d",
                    pid,
                    contract["motion_id"],
                    version_row["version"],
                    logical_model,
                    provider_identity["vendor"],
                    provider_identity["adapter"],
                    attempt,
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
                f"{logical_model}_a{attempt}_raw.mp4"
            )
            if not cand_row.get("raw_video_path"):
                try:
                    await supabase_assets.upload_asset_to_storage(raw_path, result.video_bytes, "video/mp4")
                    cand_row["raw_bucket"] = supabase_assets.BUCKET
                    cand_row["raw_video_path"] = raw_path
                    cand_row["error"] = None
                except Exception as store_exc:
                    cand_row["error"] = "RAW_STORE_FAILED"
                    persist_fields = {
                        "error": cand_row["error"],
                        "model": cand_row["model"],
                        "provider_job_id": cand_row["provider_job_id"],
                        "generation_metadata": cand_row["generation_metadata"],
                    }
                    if already_persisted:
                        await canonical_pet_service._update(
                            _candidates_table(), _MOCK_CANDIDATES, cand_id, persist_fields
                        )
                    else:
                        await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                        candidates.append(cand_row)
                    logger.exception(
                        "모션 raw 저장 실패 (%s attempt=%d)", logical_model, attempt
                    )
                    # 이미 결제된 provider 결과다 — 새 유료 후보로 넘어가지 않는다.
                    # 재시도(재빌드 호출)는 같은 candidate/provider_job_id 를
                    # 재사용해 저장만 다시 시도한다 (재제출 없음).
                    raise durable_provider_jobs.ProviderRecoveryRequired(
                        cand_id,
                        "결제 완료된 모션 결과의 raw 저장에 실패했습니다 — 재시도 시 같은 후보를 재사용합니다.",
                    ) from store_exc

                # 완료된 후보는 QA **이전에** 저장된다.
                persist_fields = {
                    "error": None,
                    "raw_bucket": cand_row["raw_bucket"],
                    "raw_video_path": cand_row["raw_video_path"],
                    "model": cand_row["model"],
                    "provider_job_id": cand_row["provider_job_id"],
                    "generation_metadata": cand_row["generation_metadata"],
                }
                if already_persisted:
                    await canonical_pet_service._update(
                        _candidates_table(), _MOCK_CANDIDATES, cand_id, persist_fields
                    )
                else:
                    await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)

            qa = await _evaluate_candidate_qa(
                video_bytes=result.video_bytes,
                contract=contract,
                output_spec=output_spec,
                motion_description=spec.description,
                motion_class=motion_class,
                start_rgb=start_rgb,
                target_rgb=target_rgb,
                start_bytes=start_bytes,
                target_bytes=target_bytes,
                frame_sampler=sampler,
                vlm_qa_fn=vlm_identity.qa_motion_video,
                conformance_fn=conformance_fn or motion_video_qa.verify_output_conformance,
                inherited_evidence=[
                    evidence
                    for evidence in [
                        (contract.get("start_keyframe") or {}).get("approved_qa_evidence")
                    ]
                    if evidence
                ],
            )
            try:
                business_qa.attach_business_result(
                    qa,
                    attempt_number=business_qa.automatic_attempt_number(
                        candidates, current_candidate_id=cand_id
                    ),
                    request_kind=motion_class or "MOTION",
                    fallback_available=True,
                )
            except Exception as exc:
                if legacy_fallback:
                    raise
                # Business QA itself failed: safe default (block, no further
                # spend, fallback still). Never the legacy decision.
                logger.error("Business QA 영수증 생성 실패 — safe default", exc_info=True)
                qa["business_qa"] = business_qa.safe_default_receipt(
                    reason=business_qa.SAFE_DEFAULT_REASON_ERROR,
                    request_kind=motion_class or "MOTION",
                    attempt_number=business_qa.automatic_attempt_number(
                        candidates, current_candidate_id=cand_id
                    ),
                    detail=f"{type(exc).__name__}: {exc}",
                )

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
            if business_qa.is_deliverable(qa, legacy_fallback=legacy_fallback):
                passes += 1

    await run_provider(resolved[0], policy["max_primary"], "primary")
    # 계약 위반은 우리 어댑터 잘못이다 — 폴백 프로바이더 과금으로 가리지 않는다.
    if passes == 0 and len(resolved) > 1 and not contract_violation:
        await run_provider(resolved[1], policy["max_fallback"], "fallback")

    from . import motion_video_qa as qa_mod

    ranked = _rank(candidates, legacy_order=legacy_fallback)
    selected = business_qa.best_available_candidate(ranked, legacy_fallback=legacy_fallback)
    fallback_receipt = business_qa.fallback_receipt(ranked)
    # 무결성 게이트: PASS 가 없으면 무결성 사유 없는 REVIEW/FAIL 후보를 전달용으로
    # 고른다. 버전 status 는 그대로(review/failed) 두고 후보만 selected 로 표시한다 —
    # 결정도 status 도 고쳐 쓰지 않는다. (legacy authority 가 은퇴한 모션에는 없다.)
    gated = None
    if (
        selected is None
        and legacy_fallback
        and severity_gate_mode() == SEVERITY_GATE_INTEGRITY_ONLY
    ):
        gated = choose_publishable_candidate(candidates)
    chosen = selected or gated
    if chosen:
        selected = chosen
        if gated is None:
            status = STATUS_COMPLETE
            selection_reason = (
                f"best business-deliverable candidate: {selected['provider']} attempt {selected['attempt']}, "
                f"identity_similarity={selected['qa_result'].get('identity_similarity')}"
            )
        else:
            status = (
                STATUS_REVIEW
                if any(c["decision"] == qa_mod.REVIEW for c in candidates)
                else STATUS_FAILED
            )
            selection_reason = _gate_selection_reason(selected)
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
            "provider_identity": dict(
                (selected.get("generation_metadata") or {}).get("provider_identity") or {}
            ),
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
    elif fallback_receipt:
        status = STATUS_REVIEW
        selection_reason = "business-v1 automatic budget exhausted — fallback required"
    elif legacy_fallback and any(c["decision"] == qa_mod.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no business-deliverable candidate — human review required"
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
            "business_qa": {
                "version": business_qa.BUSINESS_QA_VERSION,
                "selection_policy": business_qa.BEST_AVAILABLE_POLICY_VERSION,
                "candidate_budget": business_qa.automatic_candidate_budget(motion_class or "MOTION"),
                "selected": (
                    dict((selected.get("qa_result") or {}).get("business_qa") or {})
                    if selected else (dict(fallback_receipt) if fallback_receipt else None)
                ),
                "selection_priority": (
                    business_qa.candidate_selection_priority(selected) if selected else None
                ),
            },
            **(
                {"delivery": {"gate": SEVERITY_GATE_INTEGRITY_ONLY, "candidate_id": gated["id"],
                              "qa_decision": gated["decision"]}}
                if gated else {}
            ),
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


async def _breathing_evidence(
    motion_id: str,
    video_bytes: Optional[bytes],
    start_rgb: Optional[np.ndarray],
    frames: Optional[list[Optional[np.ndarray]]],
) -> tuple[Optional[dict[str, Any]], list, tuple[float, ...]]:
    """
    BREATHING 전용 시간축 증거 + VLM 근접쌍 프레임 (motion-video-qa-v3).

    다른 모션은 (None, 기존 프레임, 기존 분율) 그대로다. 어떤 실패도 조용히
    기존 입력으로 떨어진다 — 증거 수집이 QA 를 죽이면 안 된다.

    시간축 분석(breathing_temporal_qa.analyze — 자체 조밀 디코딩)과 VLM 근접쌍
    추가 프레임 샘플링(motion_video_qa.sample_frames)은 같은 영상 바이트를
    **서로 독립적으로** 디코딩한다 — 판정에 쓰는 값은 그대로이고 동시에 돌려
    레이턴시만 줄인다.
    """
    from . import breathing_temporal_qa, motion_video_qa

    base_frames = list(frames or [])
    base_fractions = tuple(motion_video_qa.SAMPLE_FRACTIONS)
    if (motion_id or "").upper() != "BREATHING" or not video_bytes:
        return None, base_frames, base_fractions

    temporal, extra = await asyncio.gather(
        asyncio.to_thread(breathing_temporal_qa.analyze, video_bytes, start_rgb),
        asyncio.to_thread(
            motion_video_qa.sample_frames, video_bytes, breathing_temporal_qa.VLM_EVIDENCE_FRACTIONS
        ),
        return_exceptions=True,
    )
    if isinstance(temporal, BaseException):
        logger.warning("BREATHING 시간축 분석 실패", exc_info=temporal)
        temporal = None
    if isinstance(extra, BaseException):
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


async def _evaluate_candidate_qa(
    *,
    video_bytes: bytes,
    contract: dict[str, Any],
    output_spec: dict[str, Any],
    motion_description: str,
    motion_class: str,
    start_rgb: Optional[np.ndarray],
    target_rgb: Optional[np.ndarray],
    start_bytes: Optional[bytes],
    target_bytes: Optional[bytes],
    frame_sampler: Callable[[bytes], Optional[list[Optional[np.ndarray]]]],
    vlm_qa_fn: Callable[..., Optional[dict[str, Any]]],
    conformance_fn: Callable[[bytes, dict[str, Any]], dict[str, Any]],
    force_vlm: bool = False,
    inherited_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """
    후보 1건의 전체 QA — build_motion_video 와 reevaluate_motion_candidate 가
    공유한다 (동작은 이전과 동일, 실행 방식만 바뀐다).

    output_conformance(ffprobe — 프레임 샘플링/시간축/VLM 체인의 결과에 전혀
    의존하지 않는다)를 그 체인과 **동시에** 시작한다. 최종 판정(qa["decision"])
    은 여전히 둘 다 끝난 뒤에만 conformance 로 하향 조정된다 — 필수 검사를
    건너뛰는 게 아니라 대기 시간만 겹친다.
    """
    from . import motion_video_qa, qa_evidence_reuse, vlm_escalation, vlm_identity

    qa_started = time.perf_counter()
    conformance_task = asyncio.ensure_future(asyncio.to_thread(conformance_fn, video_bytes, output_spec))
    try:
        frames = await asyncio.to_thread(frame_sampler, video_bytes)
        temporal_qa, vlm_frames, vlm_fractions = await _breathing_evidence(
            contract["motion_id"], video_bytes, start_rgb, frames
        )
        deterministic_qa = motion_video_qa.evaluate_motion_video(
            frames=frames,
            spec_contract=contract,
            start_keyframe_rgb=start_rgb,
            target_keyframe_rgb=target_rgb,
            vlm_qa=None,
            temporal_qa=temporal_qa,
        )
        escalation = vlm_escalation.should_call_vlm(
            stage="MOTION",
            qa_result=deterministic_qa,
            request_kind=motion_class,
            motion_id=contract["motion_id"],
            force_call=force_vlm,
            inherited_evidence=inherited_evidence,
        )
        should_call = escalation["decision"] == vlm_escalation.CALL
        # Failure classes of VLM calls that return nothing are persisted on the
        # escalation receipt (to_thread copies this context to the worker).
        with vlm_identity.capture_call_failures() as vlm_failures:
            vlm_qa = (
                await asyncio.to_thread(
                    vlm_qa_fn,
                    _frames_to_jpeg(vlm_frames),
                    motion_description=motion_description,
                    motion_class=motion_class,
                    sample_fractions=vlm_fractions,
                    reference_image=(start_bytes, "image/png"),
                    target_image=((target_bytes, "image/png") if target_bytes else None),
                    expected_direction=(contract.get("qa_context") or {}).get("expected_direction"),
                    interaction_type=(contract.get("qa_context") or {}).get("interaction_type"),
                    allow_generated_hand=bool(
                        (contract.get("qa_context") or {}).get("allow_generated_hand")
                    ),
                    tasks=escalation.get("requested_tasks") or (),
                    unresolved_questions=escalation.get("unresolved_questions") or (),
                    evidence_context={
                        "stage_qa_version": motion_video_qa.active_qa_version(),
                        "motion_contract_version": contract.get("registry_contract_version"),
                        "inherited_fingerprints": [
                            str(item.get("fingerprint"))
                            for item in inherited_evidence
                            if item.get("valid") is True and item.get("fingerprint")
                        ]
                    },
                )
                if should_call
                else None
            )
        qa = (
            motion_video_qa.evaluate_motion_video(
                frames=frames,
                spec_contract=contract,
                start_keyframe_rgb=start_rgb,
                target_keyframe_rgb=target_rgb,
                vlm_qa=vlm_qa,
                temporal_qa=temporal_qa,
            )
            if should_call
            else deterministic_qa
        )
        qa["vlm_escalation"] = vlm_escalation.finalize_vlm_escalation(
            escalation, called=should_call, result=vlm_qa, failures=vlm_failures
        )
        qa_evidence_reuse.attach_receipt(
            qa,
            stage="MOTION",
            escalation=escalation,
            vlm_result=vlm_qa,
            inherited=inherited_evidence,
        )
        conformance = await conformance_task
    except BaseException:
        conformance_task.cancel()
        raise

    # ── 출력 규격 검증 (Phase 6.5) — 프로바이더가 요청 사양을 실제로 지켰는가.
    # 종횡비/오디오 위반은 FAIL 로 강등된다: 요청을 명시했는데 어긴 출력이
    # 조용히 통과할 수 없다. unknown 은 기록만 (PASS 승격 없음).
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
    # 무결성 게이트가 켜져 있을 때만 심각도 영수증을 싣는다 — 꺼져 있으면 저장
    # 형식도 이전과 같다. 결정(decision)은 어느 경우에도 바뀌지 않는다.
    gate = motion_video_qa.severity_gate_mode()
    if gate != motion_video_qa.SEVERITY_GATE_OFF:
        qa["severity"] = motion_video_qa.severity_receipt(qa, gate)
    qa["shadow_telemetry"] = {
        "version": "business-qa-shadow-v1",
        "qa_time_ms": round((time.perf_counter() - qa_started) * 1000.0, 3),
    }
    return qa


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
    vlm_cache_mode: Optional[str] = None,
) -> MotionVersion:
    """Re-run the current versioned QA against one already stored candidate.

    ``vlm_cache_mode`` overrides ``VLM_QA_CACHE`` for this re-evaluation only.
    ``"refresh"`` skips the same-version dedup and re-asks the VLM, overwriting
    the cached answer (operator path to force one candidate to be re-judged).

    This operator path never invokes a generation provider and never uploads or
    replaces an asset.  The resulting candidate decision is derived from the
    current QA implementation; the superseded QA result is retained in
    ``generation_metadata.qa_history`` for auditability.
    """
    from . import (
        asset_url_refresh,
        business_qa,
        canonical_pet_service,
        motion_spec,
        motion_video_qa,
        pet_identity_service,
        supabase_assets,
        vlm_identity,
        vlm_escalation,
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
    force_refresh = vlm_identity.qa_cache_mode(vlm_cache_mode) == vlm_identity.QA_CACHE_REFRESH
    if (
        previous_qa.get("qa_version") == motion_video_qa.active_qa_version()
        and previous_qa.get("sampling_version") == motion_video_qa.FRAME_SAMPLING_VERSION
        and (
            previous_vlm.get("source")
            in {vlm_identity.VLM_MOTION_QA_VERSION, vlm_identity.VLM_TARGETED_QA_VERSION}
            or (
                (previous_qa.get("vlm_escalation") or {}).get("version")
                == vlm_escalation.VLM_ESCALATION_VERSION
                and (previous_qa.get("vlm_escalation") or {}).get("decision") == "SKIP"
            )
        )
        and (previous_qa.get("motion_business_contract") or {}).get("version")
        == motion_spec.MOTION_QA_CONTRACT_VERSION
        and business_qa.receipt(previous_qa) is not None
        and not force_refresh
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

    spec = motion_spec.get_motion(mid)
    assert spec is not None  # resolve_video_generation_spec succeeded above.
    start_rgb = _rgb_from_bytes(start_bytes)
    qa = await _evaluate_candidate_qa(
        video_bytes=raw,
        contract=contract,
        output_spec=dict(version_row.get("output_spec") or {}),
        motion_description=spec.description,
        motion_class=contract["motion_class"],
        start_rgb=start_rgb,
        target_rgb=_rgb_from_bytes(target_bytes),
        start_bytes=start_bytes,
        target_bytes=target_bytes,
        frame_sampler=frame_sampler or motion_video_qa.sample_frames,
        vlm_qa_fn=(
            functools.partial(vlm_qa_fn or vlm_identity.qa_motion_video, cache_mode=vlm_cache_mode)
            if vlm_cache_mode
            else (vlm_qa_fn or vlm_identity.qa_motion_video)
        ),
        conformance_fn=conformance_fn or motion_video_qa.verify_output_conformance,
        force_vlm=force_refresh,
        inherited_evidence=[
            evidence
            for evidence in [
                (contract.get("start_keyframe") or {}).get("approved_qa_evidence")
            ]
            if evidence
        ],
    )
    business_qa.attach_business_result(
        qa,
        attempt_number=business_qa.automatic_attempt_number(
            candidates, current_candidate_id=cand_id
        ),
        request_kind=contract["motion_class"] or "MOTION",
        fallback_available=True,
    )

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

    legacy_fallback = not business_qa.legacy_authority_retired(motion_id)
    ranked = _rank(candidates, legacy_order=legacy_fallback)
    selected = business_qa.best_available_candidate(ranked, legacy_fallback=legacy_fallback)
    fallback_receipt = business_qa.fallback_receipt(ranked)
    gated = None
    if (
        selected is None
        and legacy_fallback
        and severity_gate_mode() == SEVERITY_GATE_INTEGRITY_ONLY
    ):
        gated = choose_publishable_candidate(candidates)
    chosen = selected or gated
    for row in candidates:
        should_select = bool(chosen and str(row.get("id")) == str(chosen.get("id")))
        if bool(row.get("selected")) != should_select:
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, str(row["id"]), {"selected": should_select}
            )
            row["selected"] = should_select

    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best business-deliverable candidate after {motion_video_qa.active_qa_version()}: "
            f"{selected['provider']} attempt {selected['attempt']}"
        )
    elif gated:
        status = (
            STATUS_REVIEW
            if any(row.get("decision") == motion_video_qa.REVIEW for row in candidates)
            else STATUS_FAILED
        )
        selection_reason = _gate_selection_reason(gated)
        selected = gated
    elif fallback_receipt:
        status = STATUS_REVIEW
        selection_reason = "business-v1 automatic budget exhausted — fallback required"
    elif legacy_fallback and any(
        row.get("decision") == motion_video_qa.REVIEW for row in candidates
    ):
        status = STATUS_REVIEW
        selection_reason = "no business-deliverable candidate — human review required"
    else:
        status = STATUS_FAILED
        selection_reason = "no usable candidate"

    versions = dict(version_row.get("analyzer_versions") or {})
    versions.update(
        {
            "qa": motion_video_qa.active_qa_version(),
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
            "business_qa": {
                "version": business_qa.BUSINESS_QA_VERSION,
                "selection_policy": business_qa.BEST_AVAILABLE_POLICY_VERSION,
                "candidate_budget": business_qa.automatic_candidate_budget(
                    contract["motion_class"] or "MOTION"
                ),
                "selected": (
                    dict((selected.get("qa_result") or {}).get("business_qa") or {})
                    if selected else (dict(fallback_receipt) if fallback_receipt else None)
                ),
                "selection_priority": (
                    business_qa.candidate_selection_priority(selected) if selected else None
                ),
            },
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
        "qa_version": motion_video_qa.active_qa_version(),
        "sample_count": len(pairs),
        "buckets": buckets,
        "matrix": matrix,
        "pairs": pairs,
        "note": (
            "임계값은 1~2개 사례로 바꾸지 않는다. 약 10~20마리 실펫 표본 후 "
            "새 QA 버전(motion-video-qa-v2)으로만 재캘리브레이션한다."
        ),
    }
