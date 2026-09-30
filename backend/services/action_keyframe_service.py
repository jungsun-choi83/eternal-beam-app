"""
액션 키프레임 빌더 (Phase 5).

── 파이프라인 ──────────────────────────────────────────────────────────────
승인된 정본 펫(Phase 4) + 역할 스펙(action_keyframe_spec) →
  1. 정본 앵커 확정 — 신원의 출발점은 항상 정본 **클린 플레이트**(누끼 +
     고정 중립 배경)다. raw 는 증거/QA 용으로만 읽는다. 매 액션마다 고객
     원본에서 신원을 다시 만들지 않는다. REVIEW 정본은 기본 거절
     (KEYFRAME_ALLOW_REVIEW_CANONICAL=1 로만 명시적으로 허용).
  2. 보조 신원 제약 — 신뢰 세트의 PRIMARY_FACE/FULL_BODY 원본 최대 2장.
  3. Phase 4 프로바이더/후보/한도 정책 **그대로 재사용** — 프롬프트만
     "같은 펫, 통제된 다른 포즈"다.
  4. QA = 정본 QA(신원/코트/누끼) + 포즈 QA(VLM) + 해부학. 포즈가 바뀌는
     역할(LIE/SLEEP)은 프로필 비율 비교를 끄고 VLM 해부학 확인이 구조 검증을
     대신한다. VLM 확언 없으면 최대 REVIEW.
  5. 선택 → 대장 기록(role='generated', kind keyframe_raw/cutout/plate) →
     불변 버전.

테마/배경/환경 오브젝트 없음. 프로덕션 Luma/Wan 경로는 이 모듈과 무관하다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

KEYFRAME_BUILDER_VERSION = "keyframe-builder-v1"
KEYFRAME_QA_VERSION = "keyframe-qa-v1"

STATUS_BUILDING = "building"
STATUS_COMPLETE = "complete"
STATUS_REVIEW = "review"
STATUS_FAILED = "failed"

GENERATED_KIND_RAW = "keyframe_raw"
GENERATED_KIND_CUTOUT = "keyframe_cutout"
#: 하류 생성(모션 영상)이 실제로 먹는 입력 — raw 가 아니라 클린 플레이트다.
GENERATED_KIND_PLATE = "keyframe_plate"

#: 프로필 bbox 비율 비교가 무의미해지는(포즈가 실루엣을 바꾸는) 역할.
_POSE_CHANGING_ROLES = ("LIE", "SLEEP")

#: 새 이미지 생성 대신 승인된 Canonical 을 그대로 별칭하는 후보의 provider 값
#: (BREATHING → NEUTRAL_IDLE 재사용 전용, generated=false).
CANONICAL_REUSE_PROVIDER = "canonical_reuse"

#: Canonical → 키프레임 재사용을 허용하는 역할. BREATHING 전용 요구사항이라
#: NEUTRAL_IDLE 하나뿐이다 — 다른 역할(포즈가 바뀌는 LIE/SLEEP 포함)은 Canonical
#: 자체가 그 포즈를 절대 보여줄 수 없으므로 대상이 아니다.
_CANONICAL_REUSABLE_ROLES = ("NEUTRAL_IDLE",)


class ActionKeyframeError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _keyframes_table() -> str:
    return os.getenv("PET_ACTION_KEYFRAMES_TABLE", "pet_action_keyframes")


def _candidates_table() -> str:
    return os.getenv("PET_ACTION_KEYFRAME_CANDIDATES_TABLE", "pet_action_keyframe_candidates")


def _use_db() -> bool:
    return os.getenv("HYBRID_USE_SUPABASE", "1").strip().lower() not in ("0", "false", "no")


def _supabase():
    from ..models.content import _supabase_client

    return _supabase_client()


_MOCK_KEYFRAMES: list[dict[str, Any]] = []
_MOCK_CANDIDATES: list[dict[str, Any]] = []


def __reset_for_tests() -> None:
    _MOCK_KEYFRAMES.clear()
    _MOCK_CANDIDATES.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allow_review_canonical() -> bool:
    """REVIEW 정본을 앵커로 쓸지 — 기본 거절, 명시적 정책으로만 허용."""
    return os.getenv("KEYFRAME_ALLOW_REVIEW_CANONICAL", "0").strip().lower() in ("1", "true", "yes")


def analyzer_versions() -> dict[str, Any]:
    from . import action_keyframe_spec, canonical_pet_service

    return {
        **canonical_pet_service.analyzer_versions(),
        "keyframe_builder": KEYFRAME_BUILDER_VERSION,
        "keyframe_spec": action_keyframe_spec.KEYFRAME_SPEC_VERSION,
        "keyframe_prompt": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
        "keyframe_qa": KEYFRAME_QA_VERSION,
    }


#: 판정만 바꾸는 버전들 — 저장된 후보를 **다시 평가**할 뿐, 다시 만들지 않는다.
#:
#: canonical_pet_service.QA_ONLY_VERSION_KEYS / motion_video_service.QA_ONLY_VERSION_KEYS
#: 와 같은 계약이다. 키프레임 스탬프는 정본 스탬프를 통째로 포함하므로 정본의 QA
#: 키(canonical_qa)도 함께 뺀다. 예전에는 멱등/재개 판정이 스탬프 **전체**를
#: 비교해서, QA 규칙 한 줄을 고치면 완료된 키프레임이 "달라진 것"으로 보여 새
#: 버전(유료)이 만들어지고 building 중이던 키프레임은 재개되지 못했다.
#:
#: 여기 적힌 키만 비교에서 빠진다. 나머지(빌더/스펙/프롬프트/프로바이더/정본·세트·
#: 신원 분석기)는 전부 생성에 영향을 준다고 본다 — 모르는 키의 기본값은 "다시
#: 만든다"여야 조용히 낡은 자산을 재사용하는 실수가 생기지 않는다.
QA_ONLY_VERSION_KEYS = ("keyframe_qa", "canonical_qa")


def generation_versions(stamp: Optional[dict[str, Any]]) -> dict[str, Any]:
    """스탬프에서 **생성 결과를 바꾸는** 부분만 남긴다 (읽기 시점 투영 — 저장 형식 불변)."""
    return {k: v for k, v in (stamp or {}).items() if k not in QA_ONLY_VERSION_KEYS}


# ══════════════════════════════════════════════════════════════════════════
# 키프레임 QA — 정본 QA + 포즈
# ══════════════════════════════════════════════════════════════════════════


def evaluate_keyframe_candidate(
    *,
    cutout_rgba: Optional[np.ndarray],
    profile: Any,
    canonical_signature: Optional[dict[str, Any]],
    reference_signatures: list[dict[str, Any]],
    spec: Any,
    vlm_qa: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    신원(정본 시그니처 우선) + 포즈 + 해부학 + 사용성.

    포즈 판정은 VLM 전용이다 — 실루엣 휴리스틱으로 "엎드림"을 판정하는 것은
    Phase 2 부터 일관되게 거부해 온 종류의 추측이다. VLM 없으면 pose=unknown →
    최대 REVIEW.
    """
    from . import canonical_qa

    pose_changing = spec.role in _POSE_CHANGING_ROLES
    sigs = ([canonical_signature] if canonical_signature else []) + list(reference_signatures)
    base = canonical_qa.evaluate_candidate(
        cutout_rgba=cutout_rgba,
        profile=profile,
        reference_signatures=sigs,
        vlm_qa=vlm_qa,
        compare_structure=not pose_changing,
    )
    checks = dict(base["checks"])
    reasons = list(base["reasons"])

    def v(key: str) -> str:
        return str((vlm_qa or {}).get(key) or "unknown")

    if vlm_qa:
        if v("pose_matches") == "no" or v("body_orientation_ok") == "no":
            checks["pose"] = canonical_qa.FAIL
            reasons.append("pose_not_achieved")
        elif v("pose_matches") == "yes":
            if v("required_regions_visible") == "no":
                checks["pose"] = canonical_qa.REVIEW
                reasons.append("required_regions_not_visible")
            else:
                checks["pose"] = canonical_qa.PASS
        else:
            checks["pose"] = "unknown"
            reasons.append("pose_uncertain")
    else:
        checks["pose"] = "unknown"
        reasons.append("pose_qa_unavailable")

    # 포즈가 바뀌는 역할: 프로필 비율 비교 대신 VLM 해부학 확인이 구조를 담당한다.
    if pose_changing and checks.get("structure") == "unknown" and checks.get("vlm_anatomy") == canonical_qa.PASS:
        checks["structure"] = canonical_qa.PASS
        reasons.append("structure_via_vlm_anatomy")

    # ── 판정: 신원 도메인 판정 × 포즈 ────────────────────────────────────
    # 신원 쪽은 정본 QA 가 내린 결론을 그대로 쓴다. 여기서 서브체크를 다시
    # "전부 PASS" 로 재집계하면 coat_pattern 같은 **자문** 검사의 REVIEW 가
    # 정본 QA 에서는 통과했는데 키프레임에서만 다시 발목을 잡는다.
    identity_checks = {k: val for k, val in checks.items() if k != "pose"}
    identity_decision = (
        base["decision"]
        if identity_checks == base["checks"]
        # structure_via_vlm_anatomy 보정이 들어간 경우에만 정본 QA 의 판정
        # 규칙(자문 검사 인지)을 보정된 검사표에 다시 적용한다.
        else canonical_qa.decide(identity_checks)
    )
    pose_check = checks.get("pose")

    if identity_decision == canonical_qa.FAIL or pose_check == canonical_qa.FAIL:
        decision = canonical_qa.FAIL
    elif identity_decision == canonical_qa.PASS and pose_check == canonical_qa.PASS:
        decision = canonical_qa.PASS
    else:
        decision = canonical_qa.REVIEW

    return {
        "qa_version": KEYFRAME_QA_VERSION,
        "base_qa_version": base["qa_version"],
        "identity_similarity": base["identity_similarity"],
        "checks": checks,
        "reasons": reasons,
        "identity_decision": identity_decision,
        "decision": decision,
        "pose": {
            "required": spec.required_pose,
            "matches": v("pose_matches"),
            "confidence": v("pose_confidence"),
        },
        "vlm": base.get("vlm"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 데이터 모델
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class KeyframeCandidate:
    id: str
    keyframe_id: str
    provider: str
    attempt: int
    decision: str
    model: Optional[str] = None
    external_job_id: Optional[str] = None
    raw_bucket: Optional[str] = None
    raw_object_path: Optional[str] = None
    cutout_bucket: Optional[str] = None
    cutout_object_path: Optional[str] = None
    plate_bucket: Optional[str] = None
    plate_object_path: Optional[str] = None
    input_canonical_candidate_id: Optional[str] = None
    input_reference_ids: list[str] = field(default_factory=list)
    generation_metadata: dict[str, Any] = field(default_factory=dict)
    qa_result: dict[str, Any] = field(default_factory=dict)
    selected: bool = False
    error: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class ActionKeyframe:
    id: str
    pet_id: str
    user_id: str
    keyframe_role: str
    version: int
    status: str
    canonical_version_id: Optional[str] = None
    canonical_version: Optional[int] = None
    selected_candidate_id: Optional[str] = None
    selection_reason: Optional[str] = None
    prompt: Optional[str] = None
    prompt_version: Optional[str] = None
    spec: dict[str, Any] = field(default_factory=dict)
    qa_summary: dict[str, Any] = field(default_factory=dict)
    analyzer_versions: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    candidates: list[KeyframeCandidate] = field(default_factory=list)
    deduplicated: bool = False


def _to_candidate(row: dict[str, Any]) -> KeyframeCandidate:
    return KeyframeCandidate(
        id=str(row.get("id")),
        keyframe_id=str(row.get("keyframe_id")),
        provider=str(row.get("provider") or ""),
        model=(row.get("model") or None),
        attempt=int(row["attempt"]) if row.get("attempt") is not None else 1,
        external_job_id=(row.get("external_job_id") or None),
        raw_bucket=(row.get("raw_bucket") or None),
        raw_object_path=(row.get("raw_object_path") or None),
        cutout_bucket=(row.get("cutout_bucket") or None),
        cutout_object_path=(row.get("cutout_object_path") or None),
        plate_bucket=(row.get("plate_bucket") or None),
        plate_object_path=(row.get("plate_object_path") or None),
        input_canonical_candidate_id=(
            str(row["input_canonical_candidate_id"]) if row.get("input_canonical_candidate_id") else None
        ),
        input_reference_ids=list(row.get("input_reference_ids") or []),
        generation_metadata=dict(row.get("generation_metadata") or {}),
        qa_result=dict(row.get("qa_result") or {}),
        decision=str(row.get("decision") or "ERROR"),
        selected=bool(row.get("selected")),
        error=(row.get("error") or None),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
    )


def _to_keyframe(
    row: dict[str, Any], candidates: list[dict[str, Any]], *, deduplicated: bool = False
) -> ActionKeyframe:
    return ActionKeyframe(
        id=str(row.get("id")),
        pet_id=str(row.get("pet_id") or ""),
        user_id=str(row.get("user_id") or ""),
        keyframe_role=str(row.get("keyframe_role") or ""),
        version=int(row.get("version") or 1),
        status=str(row.get("status") or STATUS_BUILDING),
        canonical_version_id=(str(row["canonical_version_id"]) if row.get("canonical_version_id") else None),
        canonical_version=row.get("canonical_version"),
        selected_candidate_id=(
            str(row["selected_candidate_id"]) if row.get("selected_candidate_id") else None
        ),
        selection_reason=(row.get("selection_reason") or None),
        prompt=(row.get("prompt") or None),
        prompt_version=(row.get("prompt_version") or None),
        spec=dict(row.get("spec") or {}),
        qa_summary=dict(row.get("qa_summary") or {}),
        analyzer_versions=dict(row.get("analyzer_versions") or {}),
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        completed_at=(str(row["completed_at"]) if row.get("completed_at") else None),
        candidates=[
            _to_candidate(c)
            for c in sorted(candidates, key=lambda c: (str(c.get("created_at") or ""), int(c.get("attempt") or 0)))
        ],
        deduplicated=deduplicated,
    )


async def _keyframe_rows(pet_id: str, role: Optional[str] = None) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            q = _supabase().table(_keyframes_table()).select("*").eq("pet_id", pet_id)
            if role:
                q = q.eq("keyframe_role", role)
            r = q.order("version", desc=False).execute()
            return getattr(r, "data", None) or []
        except Exception as e:
            logger.exception("키프레임 조회 실패 (pet=%s)", pet_id)
            raise ActionKeyframeError(
                "KEYFRAMES_UNAVAILABLE", "키프레임을 확인하지 못했습니다.", status=503
            ) from e
    return [
        r
        for r in _MOCK_KEYFRAMES
        if r.get("pet_id") == pet_id and (role is None or r.get("keyframe_role") == role)
    ]


async def _candidate_rows(keyframe_id: str) -> list[dict[str, Any]]:
    if _use_db() and _supabase():
        try:
            r = (
                _supabase()
                .table(_candidates_table())
                .select("*")
                .eq("keyframe_id", keyframe_id)
                .execute()
            )
            return getattr(r, "data", None) or []
        except Exception:
            logger.exception("키프레임 후보 조회 실패 (kf=%s)", keyframe_id)
            return []
    return [c for c in _MOCK_CANDIDATES if c.get("keyframe_id") == keyframe_id]


# ══════════════════════════════════════════════════════════════════════════
# 빌드
# ══════════════════════════════════════════════════════════════════════════


def _rank_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import canonical_qa

    decision_rank = {canonical_qa.PASS: 0, canonical_qa.REVIEW: 1, canonical_qa.FAIL: 2, "ERROR": 3}
    return sorted(
        [c for c in rows if c["decision"] != "ERROR"],
        key=lambda c: (
            decision_rank.get(c["decision"], 9),
            -float((c.get("qa_result") or {}).get("identity_similarity") or -1.0),
            c["attempt"],
        ),
    )


async def _canonical_neutral_idle_reuse_candidate(
    *,
    spec: Any,
    canonical: Any,
    anchor: Any,
    anchor_bytes: bytes,
    anchor_cutout_bytes: Optional[bytes],
    anchor_cutout_rgba: Optional[np.ndarray],
    alias_plate_bucket: Optional[str],
    alias_plate_object_path: Optional[str],
    profile: Any,
    reference_signatures: list[dict[str, Any]],
    secondary_vlm_images: list[tuple[bytes, str]],
    keyframe_id: str,
    pid: str,
    uid: str,
    input_ids: list[str],
) -> Optional[dict[str, Any]]:
    """
    BREATHING → NEUTRAL_IDLE 전용 재사용 판정.

    승인된 Canonical(PASS)이 **독립된** 신뢰 레퍼런스 사진들과 대조해 현재
    NEUTRAL_IDLE 계약(신원/포즈/방향/가시성)을 만족하면, 새 이미지 생성 없이
    Canonical 의 raw/cutout/plate 를 그대로 별칭하는 후보 행을 돌려준다.
    부적합하면 None — 호출부는 평소 생성 경로로 그대로 폴백한다.

    Canonical PASS 만으로는 부족하다: 여기서 돌리는 QA 는 현재 키프레임 QA
    (evaluate_keyframe_candidate) 그 자체이며, pose_matches/body_orientation_ok/
    required_regions_visible 이 전부 확언돼야 PASS 다 (canonical 자신을 자신의
    신원 레퍼런스로 쓰는 순환논리를 피하려고 canonical_signature 는 넘기지
    않는다 — 독립된 레퍼런스 사진의 signature 만 쓴다).
    """
    from . import canonical_pet_service, canonical_qa, clean_plate_service, vlm_identity
    from . import action_keyframe_spec

    if spec.role not in _CANONICAL_REUSABLE_ROLES:
        return None
    if canonical.status != canonical_pet_service.STATUS_COMPLETE:
        return None
    if not anchor_cutout_bytes or anchor_cutout_rgba is None:
        return None
    # 독립된 레퍼런스 사진/시그니처가 없으면 신원 교차검증이 순환논리가 된다 —
    # Canonical 을 Canonical 자신과 비교하는 건 검증이 아니다.
    if not secondary_vlm_images or not reference_signatures:
        return None
    if clean_plate_service.plate_required() and not alias_plate_object_path:
        return None

    vlm_qa = vlm_identity.qa_action_keyframe(
        anchor_bytes,
        secondary_vlm_images,
        required_pose=spec.required_pose,
        required_visibility=spec.required_visibility,
    )
    qa = evaluate_keyframe_candidate(
        cutout_rgba=anchor_cutout_rgba,
        profile=profile,
        canonical_signature=None,
        reference_signatures=reference_signatures,
        spec=spec,
        vlm_qa=vlm_qa,
    )
    if qa["decision"] != canonical_qa.PASS:
        return None

    return {
        "id": str(uuid.uuid4()),
        "keyframe_id": keyframe_id,
        "pet_id": pid,
        "user_id": uid,
        "keyframe_role": spec.role,
        "provider": CANONICAL_REUSE_PROVIDER,
        "model": None,
        "model_version": None,
        "attempt": 1,
        "external_job_id": None,
        "raw_bucket": anchor.raw_bucket,
        "raw_object_path": anchor.raw_object_path,
        "cutout_bucket": anchor.cutout_bucket,
        "cutout_object_path": anchor.cutout_object_path,
        "plate_bucket": alias_plate_bucket,
        "plate_object_path": alias_plate_object_path,
        "prompt_version": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
        "input_canonical_candidate_id": anchor.id,
        "input_reference_ids": input_ids,
        "generation_metadata": {
            # 재사용 명시 메타 — 하류(런/오퍼레이터)가 "실제 생성 없음"을
            # 조용히 추측하지 않도록 명시적으로 박제한다.
            "reused_from_canonical": True,
            "generated": False,
            "canonical_version_id": canonical.id,
            "canonical_candidate_id": anchor.id,
        },
        "qa_result": qa,
        "decision": qa["decision"],
        "selected": False,
        "error": None,
        "created_at": _now_iso(),
    }


async def build_keyframe(
    *,
    user_id: str,
    pet_id: str,
    keyframe_role: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    providers: Optional[Sequence[Any]] = None,
    cutout_fn: Optional[Callable[[bytes], Optional[bytes]]] = None,
    sign_url_fn: Optional[Callable[[Any], Optional[str]]] = None,
    skip_if_unchanged: bool = True,
    allow_canonical_reuse: bool = False,
    pinned_canonical_version_id: Optional[str] = None,
    pinned_canonical_version: Optional[int] = None,
) -> ActionKeyframe:
    """
    pinned_canonical_version_id / pinned_canonical_version: 생성 실행이 고정한
    정본. 주어지면 **그 버전만** 읽는다 — "최신 정본"을 해석하지 않는다. 틱
    사이에 다른 정본 버전이 생기거나 env 가 바뀌어도 키프레임 계보는 그대로다.
    """
    from . import (
        action_keyframe_spec,
        canonical_image_providers,
        canonical_pet_service,
        canonical_qa,
        clean_plate_service,
        pet_identity_service,
        pet_reference_service,
        pet_reference_set_service,
        supabase_assets,
        vlm_identity,
    )
    from .canonical_image_providers import CanonicalProviderError, CanonicalReference

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    spec = action_keyframe_spec.get_role(keyframe_role)
    if not uid or not pid:
        raise ActionKeyframeError("KEYFRAME_INVALID", "user_id 와 pet_id 가 필요합니다.")
    if not spec:
        raise ActionKeyframeError(
            "UNKNOWN_KEYFRAME_ROLE",
            f"지원하지 않는 키프레임 역할입니다: {keyframe_role}",
            status=422,
        )

    # 키프레임은 정본과 **다른** env 순서를 쓸 수 있다
    # (KEYFRAME_IMAGE_PROVIDER / KEYFRAME_IMAGE_FALLBACK_PROVIDER).
    resolved_providers = (
        list(providers)
        if providers is not None
        else canonical_image_providers.resolve_keyframe_providers()
    )
    resolved_providers = [p for p in resolved_providers if p.available()]
    if not resolved_providers:
        raise ActionKeyframeError(
            "PROVIDER_NOT_CONFIGURED", "이미지 프로바이더가 설정되지 않았습니다.", status=503
        )

    # ── 정본 앵커 (소유권 검사 포함) ──────────────────────────────────────
    pinned = pinned_canonical_version_id is not None or pinned_canonical_version is not None
    if pinned and not (pinned_canonical_version_id and pinned_canonical_version):
        raise ActionKeyframeError(
            "PINNED_CANONICAL_INVALID",
            "고정 정본은 id 와 version 이 모두 필요합니다.",
            status=409,
        )
    try:
        # 고정된 실행은 그 버전만 읽는다 — 최신을 고르지 않는다.
        canonical = await canonical_pet_service.get_canonical(
            user_id=uid,
            pet_id=pid,
            **({"version": int(pinned_canonical_version)} if pinned else {}),
        )
    except canonical_pet_service.CanonicalPetError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e
    if pinned and (not canonical or str(canonical.id or "") != str(pinned_canonical_version_id)):
        raise ActionKeyframeError(
            "PINNED_CANONICAL_NOT_FOUND",
            "실행에 고정된 정본 버전을 불러오지 못했습니다.",
            status=409,
        )
    if not canonical:
        raise ActionKeyframeError(
            "CANONICAL_REQUIRED", "승인된 정본 펫이 없습니다 — 먼저 정본을 빌드하세요.", status=409
        )

    anchor = next((c for c in canonical.candidates if c.selected), None)
    if canonical.status == canonical_pet_service.STATUS_REVIEW and anchor is None:
        if not _allow_review_canonical():
            raise ActionKeyframeError(
                "CANONICAL_NOT_APPROVED",
                "정본이 REVIEW 상태입니다 — 승인 전에는 키프레임을 만들지 않습니다 "
                "(KEYFRAME_ALLOW_REVIEW_CANONICAL=1 로만 명시적 허용).",
                status=409,
            )
        ranked = _rank_candidates(
            [
                {
                    "decision": c.decision,
                    "qa_result": c.qa_result,
                    "attempt": c.attempt,
                    "_obj": c,
                }
                for c in canonical.candidates
            ]
        )
        anchor = ranked[0]["_obj"] if ranked else None
    if canonical.status == canonical_pet_service.STATUS_FAILED or anchor is None:
        raise ActionKeyframeError(
            "CANONICAL_NOT_APPROVED", "쓸 수 있는 정본 후보가 없습니다.", status=409
        )

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    sign = sign_url_fn or canonical_pet_service._default_sign_url
    cutout = cutout_fn or canonical_pet_service._default_cutout_fn
    cid = pid[4:] if pid.startswith("pet_") else pid

    def _obj(bucket: Optional[str], path: Optional[str]):
        return SimpleNamespace(bucket=bucket or "", object_path=path or "", mime_type="image/png")

    anchor_ref = _obj(anchor.raw_bucket, anchor.raw_object_path)
    cutout_ref = _obj(anchor.cutout_bucket, anchor.cutout_object_path) if anchor.cutout_object_path else None

    async def _fetch(ref: Optional[SimpleNamespace]) -> Optional[bytes]:
        if ref is None:
            return None
        return await asyncio.to_thread(fetch, ref)

    # ── 독립적인 자산 다운로드/조회를 동시에 시작 ──────────────────────────
    # raw·누끼 다운로드는 서로 독립이고, 신뢰 세트·레퍼런스 대장·신원 프로필
    # 조회도 서로 독립이며 이 둘 중 어느 쪽에도 의존하지 않는다 — 순차로
    # 기다릴 이유가 없다(레이턴시만 줄인다, 프로바이더/유료 호출은 없다).
    anchor_bytes, anchor_cutout_bytes, refset, refs, profile = await asyncio.gather(
        _fetch(anchor_ref),
        _fetch(cutout_ref),
        pet_reference_set_service.get_set(
            user_id=uid, pet_id=pid, version=canonical.reference_set_version
        ),
        pet_reference_service.list_references(user_id=uid, pet_id=pid),
        pet_identity_service.get_profile(
            user_id=uid, pet_id=pid, version=canonical.identity_profile_version
        ),
    )
    if not anchor_bytes:
        raise ActionKeyframeError(
            "CANONICAL_ASSET_UNAVAILABLE", "정본 raw 이미지를 불러오지 못했습니다.", status=503
        )
    refs_by_id = {str(r.id): r for r in refs}

    anchor_cutout_rgba: Optional[np.ndarray] = (
        pet_identity_service.load_rgba(anchor_cutout_bytes) if anchor_cutout_bytes else None
    )
    # canonical_signature 는 재사용 QA 에서 절대 쓰지 않는다(자기 자신과
    # 비교하는 순환논리를 피하려고 None 을 넘긴다 — _canonical_neutral_idle_
    # reuse_candidate 참고) — 폴백/생성 경로에서만 필요하므로, 재사용이
    # 성공하면 이 값은 끝까지 계산하지 않는다 (재사용 판정 뒤로 지연).
    canonical_signature: Optional[dict[str, Any]] = None

    # ── 생성 입력 = 정본 **클린 플레이트** (raw 아님) ─────────────────────
    # raw 에는 모델이 그린 접지/투영 그림자와 배경 그라디언트가 들어 있고, 그걸
    # 그대로 먹이면 키프레임이 같은 얼룩을 물려받는다. raw 는 여기서도 파괴되지
    # 않는다 — VLM 신원 QA 의 **증거**로는 계속 raw 를 쓴다 (판정 불변).
    canonical_input = {"kind": "raw", "object_path": anchor.raw_object_path}
    gen_ref, gen_bytes = anchor_ref, anchor_bytes
    if clean_plate_service.plate_enabled():
        plate = None
        plate_error: Optional[str] = None
        try:
            plate = await clean_plate_service.ensure_plate(
                user_id=uid,
                content_id=cid,
                derived_kind=clean_plate_service.GENERATED_KIND_CANONICAL_PLATE,
                fetch_bytes=fetch,
                raw_object_path=anchor.raw_object_path,
                cutout_bucket=anchor.cutout_bucket,
                cutout_object_path=anchor.cutout_object_path,
                cutout_bytes=anchor_cutout_bytes,
                plate_bucket=anchor.plate_bucket,
                plate_object_path_hint=anchor.plate_object_path,
                provenance={
                    "canonical_version_id": canonical.id,
                    "canonical_candidate_id": anchor.id,
                },
            )
        except clean_plate_service.CleanPlateError as e:
            plate_error = f"{e.code}: {e.message}"
            logger.warning("정본 클린 플레이트 해석 실패 (%s)", plate_error)
        if plate is not None:
            gen_ref = _obj(plate.bucket, plate.object_path)
            gen_bytes = plate.bytes
            canonical_input = {
                "kind": "clean_plate",
                "object_path": plate.object_path,
                "created": bool(plate.created),
                **({"plate_version": plate.meta.get("plate_version")} if plate.meta else {}),
            }
        elif clean_plate_service.plate_required():
            # raw 로 조용히 새지 않는다 — 그림자 잔재의 원인이 정확히 그것이었다.
            raise ActionKeyframeError(
                "CANONICAL_PLATE_UNAVAILABLE",
                "정본 클린 플레이트를 만들 수 없습니다 (누끼 없음/손상) — "
                "raw 로 대체하지 않습니다. CLEAN_PLATE_REQUIRED=0 으로만 명시적 허용.",
                status=503,
            )
        else:
            canonical_input["fallback_reason"] = plate_error or "plate_unavailable"

    # Canonical 재사용 후보가 별칭할 플레이트 — 실제로 clean_plate 가 해석된
    # 경우에만 채운다(레거시 raw-fallback 을 플레이트로 둔갑시키지 않는다).
    alias_plate_bucket: Optional[str] = None
    alias_plate_object_path: Optional[str] = None
    if canonical_input.get("kind") == "clean_plate":
        alias_plate_bucket = gen_ref.bucket
        alias_plate_object_path = gen_ref.object_path

    # ── 보조 신원 제약: 신뢰 세트의 얼굴/전신 원본 최대 2장 ───────────────
    # 여기서는 (재사용 QA 에도 필요한) 바이트/시그니처만 모은다. signed URL
    # 조립(provider_refs)은 **프로바이더 전용 준비물**이라 재사용 판정 뒤로
    # 미룬다 — 재사용이 성공하면 프로바이더를 절대 부르지 않으므로 sign()
    # 호출 자체가 낭비다.
    secondary_candidates: list[tuple[str, str, Any]] = []
    if refset:
        by_role = {i["role"]: i for i in refset.items}
        seen_ids: set[str] = set()
        for role in ("PRIMARY_FACE", "PRIMARY_FULL_BODY"):
            item = by_role.get(role)
            if not item:
                continue
            rid = str(item["reference_id"])
            ref = refs_by_id.get(rid)
            if not ref or rid in seen_ids:
                continue
            seen_ids.add(rid)
            secondary_candidates.append((role, rid, ref))

    secondary_bytes = await asyncio.gather(*(_fetch(ref) for _, _, ref in secondary_candidates))

    reference_signatures: list[dict[str, Any]] = []
    secondary_ids: list[str] = []
    secondary_refs: list[tuple[str, str, Any, bytes]] = []
    vlm_ref_images: list[tuple[bytes, str]] = [(anchor_bytes, "image/png")]
    for (role, rid, ref), data in zip(secondary_candidates, secondary_bytes):
        if not data:
            continue
        secondary_ids.append(rid)
        secondary_refs.append((role, rid, ref, data))
        if len(vlm_ref_images) < vlm_identity.MAX_IMAGES:
            vlm_ref_images.append((data, ref.mime_type or "image/jpeg"))
        sig = ((refset.reference_analysis.get(rid) or {}).get("eligibility") or {}).get("signature")
        if sig:
            reference_signatures.append(sig)

    prompt = action_keyframe_spec.build_keyframe_prompt(
        spec, (profile.visual_identity if profile else {})
    )

    versions_stamp = {
        **analyzer_versions(),
        "canonical_providers": [f"{p.name}:{p.model_name()}" for p in resolved_providers],
    }
    if pinned:
        # 상류 분석기(신원/세트 — VLM 플래그가 들어가는 부분) 스탬프는 **고정된
        # 정본이 실제로 만들어진 값**이다, 현재 프로세스 env 가 아니다. 그러지
        # 않으면 VLM 플래그만 달라진 워커가 같은 정본의 building 키프레임을
        # 재개하지 못하고 새 버전을 (유료로) 만든다. 빌더/프롬프트/QA 코드
        # 버전과 프로바이더 구성은 그대로 현재 값이다.
        pinned_stamp = dict(getattr(canonical, "analyzer_versions", None) or {})
        versions_stamp.update(
            {
                key: pinned_stamp[key]
                for key in pet_reference_set_service.analyzer_versions()
                if key in pinned_stamp
            }
        )

    # ── 멱등: 같은 정본/프롬프트/구성의 비-실패 최신 버전 재사용 ──────────
    # rows 는 정확히 한 번만 조회한다 — 바로 아래 resumable 판정에도 같은
    # 결과를 그대로 쓴다(예전에는 skip_if_unchanged=True 일 때 같은 쿼리를
    # 두 번 날렸다).
    rows = await _keyframe_rows(pid, spec.role)
    if skip_if_unchanged and rows:
        latest = rows[-1]
        if (
            latest.get("status") in (STATUS_COMPLETE, STATUS_REVIEW)
            and str(latest.get("canonical_version_id")) == str(canonical.id)
            and latest.get("prompt_version") == action_keyframe_spec.KEYFRAME_PROMPT_VERSION
            # QA 버전은 비교에서 빠진다 — 판정이 바뀌었다고 이미지를 다시 사지 않는다.
            and generation_versions(latest.get("analyzer_versions"))
            == generation_versions(versions_stamp)
        ):
            return _to_keyframe(latest, await _candidate_rows(str(latest["id"])), deduplicated=True)

    policy = canonical_pet_service.candidate_policy()

    durable_execution = any(getattr(provider, "durable_execution", False) for provider in resolved_providers)
    resumable = rows[-1] if rows else None
    if not (
        durable_execution
        and resumable
        and resumable.get("status") == STATUS_BUILDING
        and str(resumable.get("canonical_version_id") or "") == str(canonical.id or "")
        and resumable.get("canonical_version") == canonical.version
        and resumable.get("prompt_version") == action_keyframe_spec.KEYFRAME_PROMPT_VERSION
        and generation_versions(resumable.get("analyzer_versions"))
        == generation_versions(versions_stamp)
    ):
        resumable = None

    if resumable:
        kf_row = resumable
    else:
        kf_row = {
            "id": str(uuid.uuid4()),
            "pet_id": pid,
            "user_id": uid,
            "canonical_version_id": canonical.id,
            "canonical_version": canonical.version,
            "keyframe_role": spec.role,
            "version": (max((int(r.get("version") or 0) for r in rows), default=0)) + 1,
            "status": STATUS_BUILDING,
            "selected_candidate_id": None,
            "selection_reason": None,
            "prompt": prompt,
            "prompt_version": action_keyframe_spec.KEYFRAME_PROMPT_VERSION,
            "spec": action_keyframe_spec.role_spec_snapshot(spec),
            "qa_summary": {},
            "analyzer_versions": versions_stamp,
            "created_at": _now_iso(),
            "completed_at": None,
        }
        if not await canonical_pet_service._insert(_keyframes_table(), _MOCK_KEYFRAMES, kf_row):
            raise ActionKeyframeError(
                "KEYFRAMES_UNAVAILABLE", "키프레임 버전을 기록하지 못했습니다.", status=503
            )
    keyframe_id = str(kf_row["id"])
    input_ids = [f"canonical:{anchor.id}"] + secondary_ids

    # ── Canonical → NEUTRAL_IDLE 재사용 (BREATHING 전용, generation-run 이 명시적으로
    # allow_canonical_reuse=True 를 넘길 때만) — durable 재개 대상(resumable)이 아닌
    # 완전히 새 빌드에서만 시도한다. 재사용은 프로바이더 폴링 없이 그 자리에서
    # 끝나므로 "이어서 재개할" 중간 상태가 없다.
    if allow_canonical_reuse and not resumable:
        reuse_row = await _canonical_neutral_idle_reuse_candidate(
            spec=spec,
            canonical=canonical,
            anchor=anchor,
            anchor_bytes=anchor_bytes,
            anchor_cutout_bytes=anchor_cutout_bytes,
            anchor_cutout_rgba=anchor_cutout_rgba,
            alias_plate_bucket=alias_plate_bucket,
            alias_plate_object_path=alias_plate_object_path,
            profile=profile,
            reference_signatures=reference_signatures,
            secondary_vlm_images=vlm_ref_images[1:],
            keyframe_id=keyframe_id,
            pid=pid,
            uid=uid,
            input_ids=input_ids,
        )
        if reuse_row is not None:
            if not await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, reuse_row):
                raise ActionKeyframeError(
                    "KEYFRAMES_UNAVAILABLE", "키프레임 재사용 후보를 기록하지 못했습니다.", status=503
                )
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, reuse_row["id"], {"selected": True}
            )
            reuse_row["selected"] = True
            candidates = [reuse_row]
            # 대장(pet_reference_service)에는 새 행을 남기지 않는다 — 별칭된
            # raw/cutout/plate 는 이미 canonical 빌드가 그 정확한 object_path 로
            # canonical_raw/cutout/plate 행을 기록해 뒀다. record_generated 는
            # (role=generated, object_path) 로 멱등하므로 여기서 다시 부르면
            # 그 기존 행을 그대로 돌려줄 뿐 keyframe_* 종류로 바뀌지 않는다 —
            # 물리적으로 새 자산이 없으니 새 대장 행도 없다. 진짜 키프레임
            # 계보(누가 이걸 NEUTRAL_IDLE 로 재사용했는가)는 pet_action_keyframes/
            # pet_action_keyframe_candidates 행 자체가 담당한다
            # (input_canonical_candidate_id + generation_metadata.reused_from_canonical).
            final_fields = {
                "status": STATUS_COMPLETE,
                "selected_candidate_id": reuse_row["id"],
                "selection_reason": (
                    f"reused canonical candidate {anchor.id} for NEUTRAL_IDLE — "
                    "eligibility QA PASS, no keyframe provider call"
                ),
                "qa_summary": {
                    "candidate_count": 1,
                    "decisions": {"PASS": 1, "REVIEW": 0, "FAIL": 0, "ERROR": 0},
                    "policy": policy,
                    "reused_from_canonical": True,
                },
                "completed_at": _now_iso(),
            }
            await canonical_pet_service._update(_keyframes_table(), _MOCK_KEYFRAMES, keyframe_id, final_fields)
            kf_row.update(final_fields)
            return _to_keyframe(kf_row, candidates)

    # ── 재사용을 시도하지 않았거나 실패했다 — 여기서부터는 실제 프로바이더
    # 생성 경로다. 프로바이더 전용 준비물(신원 시그니처, signed URL)을 이제
    # 만든다 — 재사용이 성공한 위 경로는 이 지점에 절대 도달하지 않는다.
    if canonical_signature is None and anchor_cutout_rgba is not None:
        canonical_signature = pet_identity_service.compute_reference_signature(anchor_cutout_rgba)
    provider_refs: list[CanonicalReference] = [
        CanonicalReference(
            reference_id=f"canonical:{anchor.id}",
            role="CANONICAL",
            url=sign(gen_ref),
            data=gen_bytes,
            mime_type="image/png",
        )
    ] + [
        CanonicalReference(
            reference_id=rid, role=role, url=sign(ref), data=data,
            mime_type=ref.mime_type or "image/jpeg",
        )
        for role, rid, ref, data in secondary_refs
    ]

    candidates = await _candidate_rows(keyframe_id) if resumable else []
    passes = sum(1 for candidate in candidates if candidate.get("decision") == "PASS")
    contract_violation = any(
        bool((candidate.get("generation_metadata") or {}).get("contract_violation"))
        for candidate in candidates
    )

    def _prompt_for(provider: Any) -> tuple[Optional[str], str]:
        limit = getattr(provider, "max_prompt_chars", None)
        if not limit or len(prompt) <= limit:
            return prompt, "full"
        compact = action_keyframe_spec.build_compact_keyframe_prompt(
            spec, (profile.visual_identity if profile else {}), max_chars=limit
        )
        if len(compact) <= limit:
            return compact, "compact"
        return None, f"compact prompt {len(compact)} chars still exceeds {provider.name} limit {limit}"

    async def run_provider(provider: Any, max_candidates: int, tier: str) -> None:
        nonlocal passes, contract_violation
        from . import durable_provider_jobs

        provider_prompt, prompt_variant = _prompt_for(provider)
        if provider_prompt is None:
            existing_contract = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("provider") == provider.name
                    and int(candidate.get("attempt") or 0) == 0
                ),
                None,
            )
            if existing_contract:
                contract_violation = True
                return
            contract_violation = True
            row = {
                "id": str(uuid.uuid4()), "keyframe_id": keyframe_id, "pet_id": pid,
                "user_id": uid, "keyframe_role": spec.role, "provider": provider.name,
                "model": provider.model_name(), "model_version": None, "attempt": 0,
                "external_job_id": None, "raw_bucket": None, "raw_object_path": None,
                "cutout_bucket": None, "cutout_object_path": None,
                "plate_bucket": None, "plate_object_path": None,
                "prompt_version": action_keyframe_spec.KEYFRAME_COMPACT_PROMPT_VERSION,
                "input_canonical_candidate_id": anchor.id,
                "input_reference_ids": input_ids,
                "generation_metadata": {"tier": tier, "contract_violation": True},
                "qa_result": {}, "decision": "ERROR", "selected": False,
                "error": f"PROVIDER_CONTRACT: {prompt_variant}"[:500],
                "created_at": _now_iso(),
            }
            await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, row)
            candidates.append(row)
            logger.error("키프레임 프로바이더 계약 위반 (%s): %s", provider.name, prompt_variant)
            return

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
                and existing.get("external_job_id")
                and (
                    (not existing.get("error") and existing.get("raw_object_path"))
                    # raw 저장만 실패한 유료 결과 — 같은 후보를 재사용해 재시도한다
                    # (재과금 없음).
                    or (existing.get("error") == "RAW_STORE_FAILED" and not existing.get("raw_object_path"))
                )
            )
            if existing and not resumable_candidate:
                continue
            already_persisted = bool(existing)
            cand_id = str(existing.get("id")) if existing else str(uuid.uuid4())
            cand_row: dict[str, Any] = existing or {
                "id": cand_id,
                "keyframe_id": keyframe_id,
                "pet_id": pid,
                "user_id": uid,
                "keyframe_role": spec.role,
                "provider": provider.name,
                "model": provider.model_name(),
                "model_version": None,
                "attempt": attempt,
                "external_job_id": None,
                "raw_bucket": None,
                "raw_object_path": None,
                "cutout_bucket": None,
                "cutout_object_path": None,
                "plate_bucket": None,
                "plate_object_path": None,
                "prompt_version": (
                    action_keyframe_spec.KEYFRAME_COMPACT_PROMPT_VERSION
                    if prompt_variant == "compact"
                    else action_keyframe_spec.KEYFRAME_PROMPT_VERSION
                ),
                "input_canonical_candidate_id": anchor.id,
                "input_reference_ids": input_ids,
                "generation_metadata": {
                    "tier": tier,
                    "prompt_variant": prompt_variant,
                    "prompt_chars": len(provider_prompt),
                    "canonical_input": canonical_input,
                },
                "qa_result": {},
                "decision": "ERROR",
                "selected": False,
                "error": None,
                "created_at": _now_iso(),
            }
            try:
                logger.info(
                    "[keyframe-receipt] pet=%s role=%s v=%s provider=%s attempt=%d",
                    pid, spec.role, kf_row["version"], provider.name, attempt,
                )
                result = provider.generate(
                    provider_refs, provider_prompt,
                    {**action_keyframe_spec.role_spec_snapshot(spec), "ratio": "1024:1024", "size": "1024x1024"},
                    {"pet_id": pid, "keyframe_id": keyframe_id, "attempt": attempt},
                )
            except CanonicalProviderError as e:
                cand_row["error"] = f"{e.code}: {e.message}"[:500]
                if existing:
                    await canonical_pet_service._update(
                        _candidates_table(), _MOCK_CANDIDATES, cand_id,
                        {"error": cand_row["error"]},
                    )
                else:
                    await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)
                if e.code == "PROVIDER_CONTRACT":
                    contract_violation = True
                    logger.error("키프레임 프로바이더 계약 위반 (%s): %s", provider.name, e.message)
                    return
                continue

            cand_row["model"] = result.model
            cand_row["external_job_id"] = result.external_job_id
            cand_row["generation_metadata"] = {
                **cand_row["generation_metadata"],  # tier/prompt_variant/prompt_chars 보존
                "usage": result.usage,
            }

            raw_path = cand_row.get("raw_object_path") or (
                f"{uid}/{cid}/keyframes/{spec.role.lower()}/v{kf_row['version']}/"
                f"{provider.name}_a{attempt}_raw.png"
            )
            if not cand_row.get("raw_object_path"):
                try:
                    await supabase_assets.upload_asset_to_storage(raw_path, result.image_bytes, "image/png")
                    cand_row["raw_bucket"] = supabase_assets.BUCKET
                    cand_row["raw_object_path"] = raw_path
                    cand_row["error"] = None
                except Exception as store_exc:
                    cand_row["error"] = "RAW_STORE_FAILED"
                    persist_fields = {
                        "error": cand_row["error"],
                        "model": cand_row["model"],
                        "model_version": cand_row["model_version"],
                        "external_job_id": cand_row["external_job_id"],
                        "generation_metadata": cand_row["generation_metadata"],
                    }
                    if already_persisted:
                        await canonical_pet_service._update(
                            _candidates_table(), _MOCK_CANDIDATES, cand_id, persist_fields
                        )
                    else:
                        await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                        candidates.append(cand_row)
                    logger.exception("키프레임 raw 저장 실패 (%s attempt=%d)", provider.name, attempt)
                    # 이미 결제된 provider 결과다 — 새 유료 후보로 넘어가지 않는다.
                    # 재시도(재빌드 호출)는 같은 candidate/external_job_id 를 재사용해
                    # 저장만 다시 시도한다 (재제출 없음).
                    raise durable_provider_jobs.ProviderRecoveryRequired(
                        cand_id,
                        "결제 완료된 키프레임 결과의 raw 저장에 실패했습니다 — 재시도 시 같은 후보를 재사용합니다.",
                    ) from store_exc

                persist_fields = {
                    "error": None,
                    "raw_bucket": cand_row["raw_bucket"],
                    "raw_object_path": cand_row["raw_object_path"],
                    "model": cand_row["model"],
                    "model_version": cand_row["model_version"],
                    "external_job_id": cand_row["external_job_id"],
                    "generation_metadata": cand_row["generation_metadata"],
                }
                if already_persisted:
                    await canonical_pet_service._update(
                        _candidates_table(), _MOCK_CANDIDATES, cand_id, persist_fields
                    )
                else:
                    await canonical_pet_service._insert(_candidates_table(), _MOCK_CANDIDATES, cand_row)
                    candidates.append(cand_row)

            cutout_rgba = None
            cut_bytes = cutout(result.image_bytes)
            if cut_bytes:
                cut_path = raw_path.replace("_raw.png", "_cutout.png")
                try:
                    await supabase_assets.upload_asset_to_storage(cut_path, cut_bytes, "image/png")
                    cand_row["cutout_bucket"] = supabase_assets.BUCKET
                    cand_row["cutout_object_path"] = cut_path
                except Exception:
                    logger.exception("키프레임 누끼 저장 실패")
                cutout_rgba = pet_identity_service.load_rgba(cut_bytes)

                # 클린 플레이트 — **모션 영상이 실제로 먹는 입력**. raw 는 증거로
                # 남고, Phase 6 에는 그림자/배경이 빠진 이 파생물만 간다.
                if clean_plate_service.plate_enabled():
                    try:
                        plate_bytes, plate_meta = clean_plate_service.build_clean_plate(cut_bytes)
                        plate_path = clean_plate_service.plate_object_path(raw_path)
                        await supabase_assets.upload_asset_to_storage(
                            plate_path, plate_bytes, "image/png"
                        )
                        cand_row["plate_bucket"] = supabase_assets.BUCKET
                        cand_row["plate_object_path"] = plate_path
                        cand_row["generation_metadata"] = {
                            **cand_row["generation_metadata"],
                            "clean_plate": plate_meta,
                        }
                    except Exception:
                        logger.exception("키프레임 클린 플레이트 생성 실패")

            vlm_qa = vlm_identity.qa_action_keyframe(
                result.image_bytes,
                vlm_ref_images,
                required_pose=spec.required_pose,
                required_visibility=spec.required_visibility,
            )
            qa = evaluate_keyframe_candidate(
                cutout_rgba=cutout_rgba,
                profile=profile,
                canonical_signature=canonical_signature,
                reference_signatures=reference_signatures,
                spec=spec,
                vlm_qa=vlm_qa,
            )
            cand_row["qa_result"] = qa
            cand_row["decision"] = qa["decision"]
            await canonical_pet_service._update(
                _candidates_table(), _MOCK_CANDIDATES, cand_id,
                {
                    "model": cand_row["model"],
                    "external_job_id": cand_row["external_job_id"],
                    "generation_metadata": cand_row["generation_metadata"],
                    "cutout_bucket": cand_row["cutout_bucket"],
                    "cutout_object_path": cand_row["cutout_object_path"],
                    "plate_bucket": cand_row["plate_bucket"],
                    "plate_object_path": cand_row["plate_object_path"],
                    "qa_result": qa,
                    "decision": qa["decision"],
                },
            )
            if qa["decision"] == canonical_qa.PASS:
                passes += 1

    await run_provider(resolved_providers[0], policy["max_primary"], "primary")
    # 계약 위반은 QA 실패가 아니다 — 그것만으로 폴백을 태우지 않는다.
    if passes == 0 and len(resolved_providers) > 1 and not contract_violation:
        await run_provider(resolved_providers[1], policy["max_fallback"], "fallback")

    ranked = _rank_candidates(candidates)
    selected = ranked[0] if ranked and ranked[0]["decision"] == canonical_qa.PASS else None
    if selected:
        status = STATUS_COMPLETE
        selection_reason = (
            f"best PASS candidate: {selected['provider']} attempt {selected['attempt']}, "
            f"identity_similarity={selected['qa_result'].get('identity_similarity')}"
        )
        await canonical_pet_service._update(
            _candidates_table(), _MOCK_CANDIDATES, selected["id"], {"selected": True}
        )
        selected["selected"] = True
        provenance = {
            "keyframe_id": keyframe_id,
            "keyframe_role": spec.role,
            "canonical_version_id": canonical.id,
            "candidate_id": selected["id"],
            "input_reference_ids": input_ids,
            "provider": selected["provider"],
            "model": selected["model"],
        }
        for path, kind in (
            (selected.get("raw_object_path"), GENERATED_KIND_RAW),
            (selected.get("cutout_object_path"), GENERATED_KIND_CUTOUT),
            (selected.get("plate_object_path"), GENERATED_KIND_PLATE),
        ):
            if path:
                try:
                    await pet_reference_service.record_generated(
                        user_id=uid, content_id=cid, object_path=path,
                        generated_kind=kind, mime_type="image/png", provenance=provenance,
                    )
                except Exception:
                    logger.warning("키프레임 대장 기록 실패 (path=%s)", path, exc_info=True)
    elif any(c["decision"] == canonical_qa.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
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
    await canonical_pet_service._update(_keyframes_table(), _MOCK_KEYFRAMES, keyframe_id, final_fields)
    kf_row.update(final_fields)
    return _to_keyframe(kf_row, candidates)


async def reevaluate_keyframe_candidate(
    *,
    user_id: str,
    pet_id: str,
    keyframe_id: str,
    candidate_id: str,
    fetch_bytes: Optional[Callable[[Any], Optional[bytes]]] = None,
    cutout_fn: Optional[Callable[[bytes], Optional[bytes]]] = None,
    vlm_cache_mode: Optional[str] = None,
) -> ActionKeyframe:
    """
    저장된 키프레임 후보 1건에 **현재** QA 를 다시 돌린다 — 프로바이더(이미지
    생성) 호출은 절대 없다. canonical_pet_service.reevaluate_canonical_candidate
    와 같은 패턴 (motion_video_service.reevaluate_motion_candidate 참고).
    """
    from . import (
        action_keyframe_spec,
        canonical_pet_service,
        canonical_qa,
        pet_identity_service,
        pet_reference_service,
        pet_reference_set_service,
        vlm_identity,
    )

    uid = (user_id or "").strip()
    pid = (pet_id or "").strip()
    kf_id = (keyframe_id or "").strip()
    cand_id = (candidate_id or "").strip()
    if not all((uid, pid, kf_id, cand_id)):
        raise ActionKeyframeError("QA_RERUN_INVALID", "QA 재실행 식별자가 모두 필요합니다.", status=422)

    await _assert_owned(uid, pid)
    rows = await _keyframe_rows(pid)
    kf_row = next((r for r in rows if str(r.get("id") or "") == kf_id), None)
    if not kf_row or str(kf_row.get("user_id") or "") != uid:
        raise ActionKeyframeError("KEYFRAME_VERSION_NOT_FOUND", "소유한 키프레임 버전이 없습니다.", status=404)
    candidates = await _candidate_rows(kf_id)
    candidate = next((c for c in candidates if str(c.get("id") or "") == cand_id), None)
    if (
        not candidate
        or str(candidate.get("user_id") or "") != uid
        or str(candidate.get("pet_id") or "") != pid
    ):
        raise ActionKeyframeError("KEYFRAME_CANDIDATE_NOT_FOUND", "소유한 키프레임 후보가 없습니다.", status=404)
    if not candidate.get("raw_object_path"):
        raise ActionKeyframeError("CANDIDATE_ASSET_MISSING", "재평가할 원본 이미지가 없습니다.", status=409)

    previous_qa = dict(candidate.get("qa_result") or {})
    # "refresh" 는 운영자가 이 후보를 VLM 에 다시 묻겠다는 뜻이다 — 같은 QA 버전의
    # 중복 제거를 건너뛰고 캐시 항목을 덮어쓴다 (vlm_identity.qa_cache_mode 참고).
    force_refresh = vlm_identity.qa_cache_mode(vlm_cache_mode) == vlm_identity.QA_CACHE_REFRESH
    if (
        previous_qa.get("qa_version") == KEYFRAME_QA_VERSION
        and previous_qa.get("base_qa_version") == canonical_qa.CANONICAL_QA_VERSION
        and not force_refresh
    ):
        return _to_keyframe(kf_row, candidates, deduplicated=True)

    spec = action_keyframe_spec.get_role(str(kf_row.get("keyframe_role") or ""))
    if not spec:
        raise ActionKeyframeError("UNKNOWN_KEYFRAME_ROLE", "지원하지 않는 키프레임 역할입니다.", status=422)

    canonical = await canonical_pet_service.get_canonical(
        user_id=uid, pet_id=pid, version=kf_row.get("canonical_version")
    )
    if not canonical or canonical.id != str(kf_row.get("canonical_version_id") or ""):
        raise ActionKeyframeError("CANONICAL_ASSET_UNAVAILABLE", "정본 버전을 찾지 못했습니다.", status=409)
    anchor = next(
        (
            c
            for c in canonical.candidates
            if c.id == str(candidate.get("input_canonical_candidate_id") or "")
        ),
        None,
    ) or next((c for c in canonical.candidates if c.selected), None)

    profile = await pet_identity_service.get_profile(
        user_id=uid, pet_id=pid, version=canonical.identity_profile_version
    )

    fetch = fetch_bytes or pet_identity_service._default_fetch_bytes
    cutout = cutout_fn or canonical_pet_service._default_cutout_fn

    def _obj(bucket: Optional[str], path: Optional[str]):
        return SimpleNamespace(bucket=bucket or "", object_path=path or "", mime_type="image/png")

    raw_bytes = fetch(_obj(candidate.get("raw_bucket"), candidate.get("raw_object_path")))
    if not raw_bytes:
        raise ActionKeyframeError("CANDIDATE_ASSET_UNAVAILABLE", "저장된 후보 원본을 불러오지 못했습니다.", status=503)

    cut_bytes = None
    if candidate.get("cutout_object_path"):
        cut_bytes = fetch(_obj(candidate.get("cutout_bucket"), candidate.get("cutout_object_path")))
    if not cut_bytes:
        cut_bytes = cutout(raw_bytes)
    cutout_rgba = pet_identity_service.load_rgba(cut_bytes) if cut_bytes else None

    canonical_signature = None
    anchor_bytes = None
    if anchor:
        anchor_bytes = fetch(_obj(anchor.raw_bucket, anchor.raw_object_path))
        if anchor.cutout_object_path:
            anchor_cut = fetch(_obj(anchor.cutout_bucket, anchor.cutout_object_path))
            if anchor_cut:
                rgba = pet_identity_service.load_rgba(anchor_cut)
                if rgba is not None:
                    canonical_signature = pet_identity_service.compute_reference_signature(rgba)

    refset = await pet_reference_set_service.get_set(
        user_id=uid, pet_id=pid, version=canonical.reference_set_version
    )
    refs = await pet_reference_service.list_references(user_id=uid, pet_id=pid)
    refs_by_id = {str(r.id): r for r in refs}
    reference_signatures: list[dict[str, Any]] = []
    vlm_ref_images: list[tuple[bytes, str]] = [(anchor_bytes, "image/png")] if anchor_bytes else []
    if refset:
        for rid in candidate.get("input_reference_ids") or []:
            ref = refs_by_id.get(str(rid))
            if not ref:
                continue
            data = fetch(ref)
            if data and len(vlm_ref_images) < vlm_identity.MAX_IMAGES:
                vlm_ref_images.append((data, ref.mime_type or "image/jpeg"))
            sig = ((refset.reference_analysis.get(str(rid)) or {}).get("eligibility") or {}).get("signature")
            if sig:
                reference_signatures.append(sig)

    vlm_qa = vlm_identity.qa_action_keyframe(
        raw_bytes, vlm_ref_images, required_pose=spec.required_pose, required_visibility=spec.required_visibility,
        **({"cache_mode": vlm_cache_mode} if vlm_cache_mode else {}),
    )
    qa = evaluate_keyframe_candidate(
        cutout_rgba=cutout_rgba,
        profile=profile,
        canonical_signature=canonical_signature,
        reference_signatures=reference_signatures,
        spec=spec,
        vlm_qa=vlm_qa,
    )

    metadata = dict(candidate.get("generation_metadata") or {})
    history = list(metadata.get("qa_history") or [])
    if previous_qa:
        history.append(
            {"decision": candidate.get("decision"), "qa_result": previous_qa, "superseded_at": _now_iso()}
        )
    metadata["qa_history"] = history
    await canonical_pet_service._update(
        _candidates_table(), _MOCK_CANDIDATES, cand_id,
        {"qa_result": qa, "decision": qa["decision"], "generation_metadata": metadata},
    )
    candidate.update({"qa_result": qa, "decision": qa["decision"], "generation_metadata": metadata})

    ranked = _rank_candidates(candidates)
    selected = ranked[0] if ranked and ranked[0]["decision"] == canonical_qa.PASS else None
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
            f"best PASS candidate after {KEYFRAME_QA_VERSION}: "
            f"{selected['provider']} attempt {selected['attempt']}"
        )
        provenance = {
            "keyframe_id": kf_id,
            "keyframe_role": spec.role,
            "canonical_version_id": kf_row.get("canonical_version_id"),
            "candidate_id": selected["id"],
            "input_reference_ids": selected.get("input_reference_ids") or [],
            "provider": selected.get("provider"),
            "model": selected.get("model"),
        }
        cid = pid[4:] if pid.startswith("pet_") else pid
        for path, kind in (
            (selected.get("raw_object_path"), GENERATED_KIND_RAW),
            (selected.get("cutout_object_path"), GENERATED_KIND_CUTOUT),
            (selected.get("plate_object_path"), GENERATED_KIND_PLATE),
        ):
            if path:
                try:
                    await pet_reference_service.record_generated(
                        user_id=uid, content_id=cid, object_path=path,
                        generated_kind=kind, mime_type="image/png", provenance=provenance,
                    )
                except Exception:
                    logger.warning("키프레임 재평가 대장 기록 실패 (path=%s)", path, exc_info=True)
    elif any(c["decision"] == canonical_qa.REVIEW for c in candidates):
        status = STATUS_REVIEW
        selection_reason = "no PASS candidate — human review required"
    else:
        status = STATUS_FAILED
        selection_reason = "no usable candidate"

    fields = {
        "status": status,
        "selected_candidate_id": (str(selected["id"]) if selected else None),
        "selection_reason": selection_reason,
        "qa_summary": {
            "candidate_count": len(candidates),
            "decisions": {
                d: sum(1 for c in candidates if c["decision"] == d) for d in ("PASS", "REVIEW", "FAIL", "ERROR")
            },
            "policy": dict((kf_row.get("qa_summary") or {}).get("policy") or {}),
        },
        "completed_at": _now_iso(),
    }
    await canonical_pet_service._update(_keyframes_table(), _MOCK_KEYFRAMES, kf_id, fields)
    kf_row.update(fields)
    return _to_keyframe(kf_row, candidates)


# ══════════════════════════════════════════════════════════════════════════
# 조회 / 평가
# ══════════════════════════════════════════════════════════════════════════


async def _assert_owned(user_id: str, pet_id: str) -> None:
    from . import pet_reference_service

    try:
        await pet_reference_service.list_references(user_id=user_id, pet_id=pet_id)
    except pet_reference_service.PetReferenceError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e


async def get_keyframe(
    *, user_id: str, pet_id: str, keyframe_role: str, version: Optional[int] = None
) -> Optional[ActionKeyframe]:
    await _assert_owned(user_id, pet_id)
    role = (keyframe_role or "").strip().upper()
    rows = await _keyframe_rows(pet_id, role)
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
    return _to_keyframe(row, await _candidate_rows(str(row["id"])))


async def list_keyframes(*, user_id: str, pet_id: str) -> list[ActionKeyframe]:
    """역할별 최신 버전만."""
    await _assert_owned(user_id, pet_id)
    rows = await _keyframe_rows(pet_id)
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        role = str(r.get("keyframe_role") or "")
        if role not in latest or int(r.get("version") or 0) > int(latest[role].get("version") or 0):
            latest[role] = r
    return [_to_keyframe(r, []) for r in latest.values()]


async def record_keyframe_evaluation(
    *,
    user_id: str,
    pet_id: str,
    keyframe_id: str,
    candidate_id: Optional[str],
    scores: dict[str, Any],
    verdict: str,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Phase 4 평가 하네스 재사용 — 같은 테이블, kind='keyframe' + 역할/프로바이더 기록."""
    from . import canonical_pet_service

    provider = None
    role = None
    for c in await _candidate_rows(keyframe_id):
        if candidate_id and str(c.get("id")) == candidate_id:
            provider = c.get("provider")
            role = c.get("keyframe_role")
            break
    try:
        return await canonical_pet_service.record_evaluation(
            user_id=user_id,
            pet_id=pet_id,
            canonical_version_id=keyframe_id,
            candidate_id=candidate_id,
            scores={**scores, **({"keyframe_role": role} if role else {})},
            verdict=verdict,
            notes=notes,
            provider=provider,
            kind="keyframe",
        )
    except canonical_pet_service.CanonicalPetError as e:
        raise ActionKeyframeError(e.code, e.message, status=e.status) from e
